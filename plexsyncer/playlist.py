import json
import logging
import os
from typing import List

from integrations.spotify import (
    extract_playlist_id,
    extract_playlist_titles_from_url,
    get_spotify_client,
)
from plexsyncer.helpers import normalize_path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def remove_existing_playlists(playlist_folder: str) -> None:
    """Removes existing .m3u and .m3u8 files in the given folder."""
    logger.info(f"Removing existing .m3u and .m3u8 files in {playlist_folder}...")
    with os.scandir(playlist_folder) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.lower().endswith((".m3u", ".m3u8")):
                try:
                    os.remove(entry.path)
                    logger.info(f"Deleted playlist file: {entry.path}")
                except Exception as e:
                    logger.error(f"Could not delete {entry.path}: {e}")


def generate_playlist(
        folder: str,
        playlist_file: str,
        m3u8_local_root: str,
        m3u8_plex_root: str,
        encode_spaces: bool,
        incremental: bool = False,
        extensions: set = None,
) -> None:
    """Generates a playlist file (.m3u8) from media files in a folder."""
    if extensions is None:
        extensions = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".wma", ".m4a"}
    logger.debug(f"Processing playlist '{playlist_file}' from folder: {folder}")

    if incremental and os.path.exists(playlist_file):
        try:
            with open(playlist_file, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()
            existing_tracks = {
                line.strip() for line in existing_lines if not line.startswith("#")
            }
            logger.info(
                f"Found {len(existing_tracks)} existing tracks in {playlist_file}"
            )
        except Exception as e:
            logger.error(f"Error reading existing playlist {playlist_file}: {e}")
            existing_tracks = set()
        mode = "a"
        header_needed = False
    else:
        mode = "w"
        header_needed = True
        existing_tracks = set()

    with open(playlist_file, mode, encoding="utf-8") as playlist:
        if header_needed:
            playlist.write("#EXTM3U\n")
        for root, _, files in os.walk(folder):
            logger.debug(f"Scanning folder: {root}")
            for file in files:
                if any(file.lower().endswith(ext) for ext in extensions):
                    full_path = os.path.abspath(os.path.join(root, file))
                    normalized_path = full_path.replace(os.sep, "/")
                    if normalized_path.startswith(normalize_path(m3u8_local_root)):
                        rel_path = normalized_path.replace(
                            normalize_path(m3u8_local_root), m3u8_plex_root, 1
                        )
                    else:
                        rel_path = normalized_path
                    if encode_spaces:
                        rel_path = rel_path.replace(" ", "%20")
                    if rel_path in existing_tracks:
                        logger.debug(f"Skipping already listed track: {rel_path}")
                        continue
                    duration = 0
                    title = file
                    # Optionally use mutagen if available for metadata extraction.
                    try:
                        from mutagen import File as MutagenFile

                        if MutagenFile:
                            audio = MutagenFile(os.path.join(root, file))
                            if (
                                    audio
                                    and hasattr(audio, "info")
                                    and hasattr(audio.info, "length")
                            ):
                                duration = int(audio.info.length)
                            if audio and audio.tags:
                                title = audio.tags.get("TIT2", file)
                                if hasattr(title, "text"):
                                    title = title.text[0]
                    except Exception as e:
                        logger.debug(f"Error reading metadata for {file}: {e}")
                    playlist.write(f"#EXTINF:{duration},{title}\n")
                    playlist.write(rel_path + "\n")
                    logger.debug(
                        f"Added track: EXTINF:{duration},{title} | Path: {rel_path}"
                    )
    logger.info(f"Playlist updated: {playlist_file}")


def process_library(
        playlist_folder: str,
        m3u8_local_root: str,
        m3u8_plex_root: str,
        encode_spaces: bool,
        incremental: bool = False,
        ext: str = ".m3u8",
) -> List[str]:
    """Scans the playlist folder and processes each subdirectory as a separate playlist."""
    m3u8_local_root = normalize_path(m3u8_local_root)
    logger.debug(f"Using m3u8 local root: {m3u8_local_root}")
    if not incremental:
        remove_existing_playlists(playlist_folder)
    generated_files = []
    try:
        with os.scandir(playlist_folder) as entries:
            for entry in entries:
                if entry.is_dir():
                    folder_name = entry.name
                    folder_path = entry.path
                    playlist_file = os.path.join(folder_path, f"{folder_name}{ext}")
                    generate_playlist(
                        folder_path,
                        playlist_file,
                        m3u8_local_root,
                        m3u8_plex_root,
                        encode_spaces,
                        incremental=incremental,
                    )
                    logger.info(f"Processed playlist for folder: {folder_name}")
                    generated_files.append(playlist_file)
                else:
                    logger.debug(f"Skipping non-directory entry: {entry.name}")
    except Exception as e:
        logger.error(f"Error scanning playlist folder {playlist_folder}: {e}")
    return generated_files


def generate_master_playlist(
        generated_files: List[str],
        m3u8_local_root: str,
        m3u8_plex_root: str,
        output_file: str = "master.m3u8",
) -> None:
    """Generates a master m3u8 file that lists all remapped playlist file paths."""
    local_root_norm = normalize_path(m3u8_local_root)
    with open(output_file, "w", encoding="utf-8") as master:
        master.write("#EXTM3U\n")
        for file in generated_files:
            norm_file = normalize_path(file)
            if norm_file.startswith(local_root_norm):
                remapped = norm_file.replace(local_root_norm, m3u8_plex_root, 1)
            else:
                remapped = norm_file
            master.write(remapped + "\n")
    logger.info(f"Master playlist generated at: {output_file}")


def create_playlist_json_structure(
        input_json_file: str, output_json_file: str, encode_spaces: bool = False
) -> None:
    """
    Reads a JSON file with playlist data and creates a new JSON structure containing detailed
    track information from each playlist. This output can be used to search for tracks via the Plex API.
    """
    try:
        with open(input_json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Error reading JSON file: {e}")
        return

    playlists = data.get("playlists", [])
    if not playlists:
        logger.warning("No playlists found in the input JSON file.")
        return

    output_data = {"playlists": []}
    for pl in playlists:
        playlist_name = pl.get("name", "Unnamed Playlist")
        last_modified = pl.get("lastModifiedDate", "")
        output_playlist = {
            "name": playlist_name,
            "lastModifiedDate": last_modified,
            "tracks": [],
        }
        for item in pl.get("items", []):
            track = item.get("track")
            if track:
                track_name = track.get("trackName", "Unknown Track")
                artist_name = track.get("artistName", "Unknown Artist")
                album_name = track.get("albumName", "Unknown Album")
                track_uri = track.get("trackUri", "")
                if encode_spaces:
                    track_uri = track_uri.replace(" ", "%20")
                output_playlist["tracks"].append(
                    {
                        "trackName": track_name,
                        "artistName": artist_name,
                        "albumName": album_name,
                        "trackUri": track_uri,
                    }
                )
        output_data["playlists"].append(output_playlist)
        logger.info(
            f"Processed playlist '{playlist_name}' with {len(output_playlist['tracks'])} tracks."
        )
    try:
        with open(output_json_file, "w", encoding="utf-8") as out:
            json.dump(output_data, out, indent=2)
        logger.info(f"Output JSON structure written to: {output_json_file}")
    except Exception as e:
        logger.error(f"Error writing output JSON file: {e}")


def create_playlist_json_from_spotify_url(
        playlist_url: str,
        client_id: str,
        client_secret: str,
        output_json_file: str,
        encode_spaces: bool = False,
        use_oauth: bool = False,
        redirect_uri: str = None,
        scope: str = None,
) -> None:
    """
    Given a Spotify playlist URL, retrieves detailed track information from Spotify and writes a JSON
    structure containing the playlist name and a list of tracks. Supports both client credentials and user OAuth.

    The output JSON structure will be:

    {
      "playlist": {
          "name": "Playlist Name",
          "tracks": [
              {
                  "trackName": "Song Title",
                  "artistName": "Artist Name",
                  "albumName": "Album Name",
                  "trackUri": "spotify:track:XXXX"
              },
              ...
          ]
      }
    }
    """
    try:
        sp = get_spotify_client(
            client_id, client_secret, use_oauth, redirect_uri, scope
        )
        playlist_id = extract_playlist_id(playlist_url)
        playlist_data = sp.playlist(playlist_id)
        playlist_name = playlist_data.get("name", "Unnamed Playlist")
        tracks = []
        results = sp.playlist_items(playlist_id)
        while results:
            for item in results.get("items", []):
                track = item.get("track")
                if track:
                    track_name = track.get("name", "Unknown Track")
                    artists = track.get("artists", [])
                    artist_name = ", ".join(
                        [a.get("name", "Unknown Artist") for a in artists]
                    )
                    album_name = track.get("album", {}).get("name", "Unknown Album")
                    track_uri = track.get("uri", "")
                    if encode_spaces:
                        track_uri = track_uri.replace(" ", "%20")
                    tracks.append(
                        {
                            "trackName": track_name,
                            "artistName": artist_name,
                            "albumName": album_name,
                            "trackUri": track_uri,
                        }
                    )
            if results.get("next"):
                results = sp.next(results)
            else:
                results = None
        output_data = {"playlist": {"name": playlist_name, "tracks": tracks}}
        with open(output_json_file, "w", encoding="utf-8") as out:
            json.dump(output_data, out, indent=2)
        logger.info(f"Playlist JSON structure written to: {output_json_file}")
    except Exception as e:
        logger.error(f"Error creating JSON structure from Spotify URL: {e}")


# For compatibility with our module style, alias the extraction function.
extract_playlist_titles_from_url = extract_playlist_titles_from_url

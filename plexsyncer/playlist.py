#!/usr/bin/env python3
import concurrent.futures
import json
import logging
import os
from multiprocessing import cpu_count
from typing import List, Optional

from integrations.spotify import SpotifyIntegration
from integrations.tidal import TidalIntegration
from plexsyncer.helpers import normalize_path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def check_all_playlists_exist(playlist_folder: str, ext: str = ".m3u8") -> bool:
    """
    Check if all expected .m3u8 files exist in the playlist subdirectories.

    Args:
        playlist_folder: Root folder containing playlist subdirectories
        ext: Extension of playlist files to check for

    Returns:
        True if all expected playlist files exist, False otherwise
    """
    try:
        with os.scandir(playlist_folder) as entries:
            for entry in entries:
                if entry.is_dir():
                    playlist_file = os.path.join(entry.path, f"{entry.name}{ext}")
                    if not os.path.exists(playlist_file):
                        logger.info(f"Playlist file missing: {playlist_file}")
                        return False
        logger.info(f"All playlist files already exist in {playlist_folder}")
        return True
    except Exception as e:
        logger.error(f"Error checking playlist files in {playlist_folder}: {e}")
        return False


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
    extensions: Optional[set] = None,
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
                    try:
                        from mutagen import File as MutagenFile

                        audio = MutagenFile(full_path)
                        if (
                            audio
                            and hasattr(audio, "info")
                            and hasattr(audio.info, "length")
                        ):
                            duration = int(audio.info.length)
                        if audio and audio.tags:
                            title_tag = audio.tags.get("TIT2")
                            if title_tag:
                                title = (
                                    title_tag.text[0]
                                    if hasattr(title_tag, "text")
                                    else str(title_tag)
                                )
                    except Exception as e:
                        logger.debug(f"Error reading metadata for {file}: {e}")
                    playlist.write(f"#EXTINF:{duration},{title}\n")
                    playlist.write(rel_path + "\n")
                    logger.debug(
                        f"Added track: EXTINF:{duration},{title} | Path: {rel_path}"
                    )
    logger.info(f"Playlist updated: {playlist_file}")


def _generate_single_playlist_worker(args):
    """Worker function for parallel playlist generation."""
    (
        folder_path,
        folder_name,
        m3u8_local_root,
        m3u8_plex_root,
        encode_spaces,
        incremental,
        ext,
    ) = args

    playlist_file = os.path.join(folder_path, f"{folder_name}{ext}")

    try:
        generate_playlist(
            folder_path,
            playlist_file,
            m3u8_local_root,
            m3u8_plex_root,
            encode_spaces,
            incremental=incremental,
        )
        logger.info(f"✅ Processed playlist for folder: {folder_name}")
        return playlist_file
    except Exception as e:
        logger.error(f"❌ Error processing playlist for folder {folder_name}: {e}")
        return None


def process_library(
    playlist_folder: str,
    m3u8_local_root: str,
    m3u8_plex_root: str,
    encode_spaces: bool,
    incremental: bool = False,
    ext: str = ".m3u8",
    force_regenerate: bool = False,
    parallel: bool = True,
    max_workers: Optional[int] = None,
) -> List[str]:
    """
    Scans the playlist folder and processes each subdirectory as a separate playlist.

    Args:
        playlist_folder: Root folder containing playlist subdirectories
        m3u8_local_root: Local root folder for the media files
        m3u8_plex_root: Plex server root folder for the media files
        encode_spaces: Whether to encode spaces as %20 in the path
        incremental: Whether to run in incremental mode (append only new tracks)
        ext: Extension for playlist files
        force_regenerate: If True, force regeneration even if all playlists exist
        parallel: Whether to use parallel processing for playlist generation
        max_workers: Maximum number of parallel workers (defaults to CPU count)

    Returns:
        List of generated playlist file paths
    """
    m3u8_local_root = normalize_path(m3u8_local_root)
    logger.debug(f"Using m3u8 local root: {m3u8_local_root}")

    # Optimization: Skip regeneration if all playlists exist and force_regenerate is False
    if not force_regenerate and check_all_playlists_exist(playlist_folder, ext):
        logger.info(
            "All playlist files already exist. Skipping regeneration. Use force_regenerate=True to override."
        )
        # Return list of existing playlist files
        generated_files = []
        try:
            with os.scandir(playlist_folder) as entries:
                for entry in entries:
                    if entry.is_dir():
                        playlist_file = os.path.join(entry.path, f"{entry.name}{ext}")
                        if os.path.exists(playlist_file):
                            generated_files.append(playlist_file)
        except Exception as e:
            logger.error(f"Error collecting existing playlist files: {e}")
        return generated_files

    if not incremental:
        remove_existing_playlists(playlist_folder)

    # Collect all playlist folders to process
    playlist_folders = []
    try:
        with os.scandir(playlist_folder) as entries:
            for entry in entries:
                if entry.is_dir():
                    playlist_folders.append((entry.path, entry.name))
    except Exception as e:
        logger.error(f"Error scanning playlist folder {playlist_folder}: {e}")
        return []

    if not playlist_folders:
        logger.info("No playlist folders found.")
        return []

    logger.info(f"Found {len(playlist_folders)} playlist folders to process.")

    if parallel and len(playlist_folders) > 1:
        # Use parallel processing
        if max_workers is None:
            max_workers = min(cpu_count(), len(playlist_folders))

        logger.info(f"🚀 Using parallel processing with {max_workers} workers...")

        # Prepare arguments for worker function
        worker_args = [
            (
                folder_path,
                folder_name,
                m3u8_local_root,
                m3u8_plex_root,
                encode_spaces,
                incremental,
                ext,
            )
            for folder_path, folder_name in playlist_folders
        ]

        generated_files = []
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers
        ) as executor:
            # Submit all tasks
            future_to_folder = {
                executor.submit(_generate_single_playlist_worker, args): args[1]
                for args in worker_args
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_folder):
                folder_name = future_to_folder[future]
                try:
                    result = future.result()
                    if result:
                        generated_files.append(result)
                except Exception as e:
                    logger.error(f"❌ Error processing folder {folder_name}: {e}")

        logger.info(
            f"✅ Parallel processing complete. Generated {len(generated_files)} playlists."
        )

    else:
        # Use sequential processing (original behavior)
        logger.info("📝 Using sequential processing...")
        generated_files = []

        for folder_path, folder_name in playlist_folders:
            playlist_file = os.path.join(folder_path, f"{folder_name}{ext}")
            try:
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
            except Exception as e:
                logger.error(f"Error processing playlist for folder {folder_name}: {e}")

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
    track information from each playlist for Plex API.
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
        output_playlist = {"name": playlist_name, "tracks": []}
        for item in pl.get("tracks", []):
            track_uri = item.get("trackUri", "")
            if encode_spaces:
                track_uri = track_uri.replace(" ", "%20")
            output_playlist["tracks"].append({"trackUri": track_uri})
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
    redirect_uri: Optional[str] = None,
    scope: Optional[str] = None,
) -> None:
    """
    Given a Spotify playlist URL, uses the SpotifyIntegration helper to export playlist data to JSON.
    Supports client credentials or user OAuth flows.
    """
    try:
        spotify = SpotifyIntegration(
            client_id=client_id,
            client_secret=client_secret,
            use_oauth=use_oauth,
            redirect_uri=redirect_uri,
            scope=scope,
        )
        spotify.export_playlist(
            playlist_url,
            output_json_file,
            encode_spaces=encode_spaces,
        )
        logger.info(f"Spotify playlist exported to JSON: {output_json_file}")
    except Exception as e:
        logger.error(f"Error exporting Spotify playlist: {e}")


def create_playlist_json_from_tidal_url(
    client_id: str,
    redirect_uri: str,
    output_json_file: str,
    encode_spaces: bool = False,
) -> None:
    """
    Given TIDAL client credentials, fetches user playlists with tracks and writes to JSON.
    """
    try:
        tidal = TidalIntegration(client_id=client_id, redirect_uri=redirect_uri)
        data = tidal.fetch_user_playlists_with_tracks()
        # Optionally encode spaces in track URIs if needed
        if encode_spaces:
            for pl in data.get("playlists", []):
                for track in pl.get("tracks", []):
                    uri = track.get("trackUri", "")
                    track["trackUri"] = uri.replace(" ", "%20")
        with open(output_json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"TIDAL playlists exported to JSON: {output_json_file}")
    except Exception as e:
        logger.error(f"Error exporting TIDAL playlists: {e}")

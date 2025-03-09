"""
Spotify Integration for PlexSyncer

This module provides functions to:
  - Extract track titles from a Spotify playlist URL using the Spotify Web API.
  - Generate a new JSON structure from a locally downloaded JSON file that contains playlist data.
    The output JSON includes detailed track information (track name, artist, album, and track URI)
    which can later be used to search for those tracks via the Plex API.
  - Retrieve a Spotify playlist from its URL and generate a JSON file containing full track details.

It supports both client credentials flow and user OAuth authentication.
"""

import json
import logging
import re

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def extract_playlist_id(url: str) -> str:
    """
    Extracts the Spotify playlist ID from a given URL.

    Accepted formats include:
      - https://open.spotify.com/playlist/<playlist_id>?si=...
      - spotify:playlist:<playlist_id>

    Raises:
      ValueError: If no valid playlist ID is found.
    """
    pattern = r"(?:playlist/|playlist:)([a-zA-Z0-9]+)"
    match = re.search(pattern, url)
    if match:
        logger.debug(f"Extracted playlist ID: {match.group(1)}")
        return match.group(1)
    raise ValueError("Invalid Spotify playlist URL.")


def get_spotify_client(
    client_id: str,
    client_secret: str,
    use_oauth: bool = False,
    redirect_uri: str = None,
    scope: str = None,
) -> spotipy.Spotify:
    """
    Returns an authenticated Spotify client.

    Args:
      client_id: Spotify client ID.
      client_secret: Spotify client secret.
      use_oauth: If True, uses user OAuth (default is False, which uses client credentials).
      redirect_uri: Redirect URI required for user OAuth.
      scope: Spotify API scope (optional) for user OAuth.

    Returns:
      An authenticated Spotipy client.
    """
    if use_oauth:
        if not redirect_uri:
            raise ValueError("redirect_uri must be provided for user OAuth.")
        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
        )
        logger.debug("Using SpotifyOAuth for user authentication.")
    else:
        auth_manager = SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        logger.debug("Using SpotifyClientCredentials for authentication.")
    sp = spotipy.Spotify(auth_manager=auth_manager)
    return sp


def extract_playlist_titles_from_url(
    playlist_url: str,
    client_id: str,
    client_secret: str,
    use_oauth: bool = False,
    redirect_uri: str = None,
    scope: str = None,
) -> list:
    """
    Given a Spotify playlist URL, extracts and returns a list of track titles.

    Args:
      playlist_url: Spotify playlist URL.
      client_id: Spotify client ID.
      client_secret: Spotify client secret.
      use_oauth: Whether to use user OAuth.
      redirect_uri: Redirect URI for user OAuth.
      scope: Spotify API scope for user OAuth.

    Returns:
      List of track titles (strings).
    """
    sp = get_spotify_client(client_id, client_secret, use_oauth, redirect_uri, scope)
    playlist_id = extract_playlist_id(playlist_url)
    results = sp.playlist_items(playlist_id)
    titles = []
    while results:
        items = results.get("items", [])
        for item in items:
            track = item.get("track")
            if track:
                title = track.get("name")
                if title:
                    titles.append(title)
        if results.get("next"):
            results = sp.next(results)
        else:
            results = None
    return titles


def sanitize_filename(name: str) -> str:
    """
    Sanitize the filename by replacing or removing characters not allowed in filenames.
    """
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name)
    logger.debug(f"Sanitized filename: {sanitized}")
    return sanitized


def create_playlist_json_structure(
    input_json_file: str, output_json_file: str, encode_spaces: bool = False
) -> None:
    """
    Reads a JSON file with playlist data and creates a new JSON structure containing detailed
    track information from each playlist. This output can be used to search for these tracks via the Plex API.

    Args:
      input_json_file: Path to the input JSON file containing playlist data.
      output_json_file: Path where the output JSON structure will be written.
      encode_spaces: If True, spaces in track URIs will be encoded as %20.
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
    structure containing the playlist name and a list of tracks. Each track includes track name, artist name,
    album name, and track URI.

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

    Args:
      playlist_url: Spotify playlist URL.
      client_id: Spotify client ID.
      client_secret: Spotify client secret.
      output_json_file: Path where the output JSON structure will be written.
      encode_spaces: If True, spaces in track URIs will be encoded as %20.
      use_oauth: If True, uses user OAuth authentication.
      redirect_uri: Redirect URI for user OAuth (required if use_oauth is True).
      scope: Spotify API scope for user OAuth (optional).
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
                    # For artists, join names if more than one artist.
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


extract_playlist_titles_from_url = extract_playlist_titles_from_url

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Module for PlexSyncer

This module handles interactions with the Plex API. It provides functions to:
  - Lookup a Plex library section (by name) to get its section ID.
  - Upload playlist files to Plex using a remapped file path.
  - Verify that the playlists have been uploaded to Plex.
  - Compare local playlist content (from m3u8 files) with the content in Plex.
  - Generate a master playlist that lists all remapped playlists.

"""

import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import List, Set, Union
from urllib.parse import unquote, urlparse

import requests
from plexapi.server import PlexServer

from plexsyncer.helpers import normalize_path

logger = logging.getLogger(__name__)
# Set to DEBUG to see detailed track matching attempts
logger.setLevel(logging.DEBUG)


def _prepare_plex_url(plex_url: str) -> str:
    """
    Prepare Plex URL for PlexAPI connection.

    Handles protocol issues that might cause PlexAPI to default to HTTPS port 443.

    Args:
        plex_url: The raw Plex server URL

    Returns:
        str: Cleaned URL ready for PlexServer connection
    """
    from urllib.parse import urlparse

    clean_url = plex_url.rstrip("/")
    parsed = urlparse(clean_url)

    # If no scheme provided, assume HTTP
    if not parsed.scheme:
        clean_url = f"http://{clean_url}"
        logger.debug(f"No protocol specified, using HTTP: {clean_url}")

    return clean_url


def _configure_ssl_verification(disable_ssl_verification: bool = False):
    """
    Configure SSL verification for PlexAPI connections.

    Args:
        disable_ssl_verification: If True, disable SSL certificate verification
    """
    if disable_ssl_verification:
        import ssl

        import urllib3

        # Disable SSL warnings
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Set SSL context to not verify certificates
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Apply to plexapi if possible
        try:
            from plexapi import server

            # Try to set SSL context on the session
            logger.info("🔓 SSL certificate verification disabled")
        except Exception as e:
            logger.debug(f"Could not configure SSL context: {e}")

        return ssl_context
    return None


handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)


def get_section_id_from_library(
    plex_url: str,
    plex_token: str,
    library_name: str,
    disable_ssl_verification: bool = False,
) -> str:
    """
    Retrieves the Plex library section ID for a given library name.

    Args:
      plex_url: Base URL of the Plex server (e.g., "http://localhost:32400").
      plex_token: Plex authentication token.
      library_name: Name of the Plex library to use.
      disable_ssl_verification: If True, disable SSL certificate verification

    Returns:
      The section ID as a string.

    Raises:
      Exception: If the library cannot be found or API call fails.
    """
    try:
        from plexapi.server import PlexServer

        # Configure SSL verification if needed
        if disable_ssl_verification:
            _configure_ssl_verification(disable_ssl_verification=True)

            # Create PlexServer connection with SSL verification disabled
            import requests
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            session = requests.Session()
            session.verify = False

            try:
                plex = PlexServer(
                    _prepare_plex_url(plex_url), plex_token, session=session
                )
            except TypeError:
                # If session parameter not supported, try without it
                plex = PlexServer(_prepare_plex_url(plex_url), plex_token)
        else:
            plex = PlexServer(_prepare_plex_url(plex_url), plex_token)

        for section in plex.library.sections():
            if section.title.lower() == library_name.lower():
                logger.info(f"Found library '{library_name}' with key: {section.key}")
                return section.key
        raise ValueError(f"Library with name '{library_name}' not found")
    except Exception as e:
        logger.error(f"Error fetching section id for library '{library_name}': {e}")
        raise


def upload_playlist_via_api(
    file_path: str,
    m3u8_local_root: str,
    m3u8_plex_root: str,
    section_id: str,
    plex_token: str,
    plex_url: str,
    encode_spaces: bool,
) -> None:
    """
    Uploads a playlist file to Plex using its API. Uses the correct Plex playlist upload endpoint.
    Based on: http://[PlexServer]:32400/playlists/upload?sectionID=[ID]&path=[PathTo.M3U]&X-Plex-Token=[Token]

    IMPORTANT: This function creates a modified M3U file with Plex server paths and proper URL encoding,
    as required by the Plex API. The file paths in the M3U must match exactly what Plex expects.

    Args:
      file_path: Path to the local playlist file.
      m3u8_local_root: Local root folder for the media files.
      m3u8_plex_root: Plex server root folder for the media files.
      section_id: Plex library section ID.
      plex_token: Plex authentication token.
      plex_url: Base URL of the Plex server (e.g., "http://localhost:32400").
      encode_spaces: Whether to encode spaces as %20 in the path.
    """
    import tempfile
    import urllib.parse

    logger.info(
        f"🔄 Processing playlist '{os.path.basename(file_path)}' for Plex upload..."
    )

    # Read the original playlist file
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Error reading playlist file '{file_path}': {e}")
        raise

    # Create a modified M3U file with Plex paths and proper encoding
    playlist_name = os.path.splitext(os.path.basename(file_path))[0]

    # Create temporary file for the Plex-formatted playlist
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".m3u", delete=False, encoding="utf-8"
    ) as temp_file:
        temp_file.write("#EXTM3U\n")

        norm_local = normalize_path(m3u8_local_root)
        norm_plex = normalize_path(m3u8_plex_root)

        logger.debug(f"Local root: {norm_local}")
        logger.debug(f"Plex root: {norm_plex}")

        tracks_processed = 0
        tracks_converted = 0

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Handle EXTINF lines
            if line.startswith("#EXTINF:"):
                temp_file.write(line + "\n")

                # Look for the next non-comment line (the file path)
                i += 1
                while i < len(lines) and (
                    lines[i].strip().startswith("#") or not lines[i].strip()
                ):
                    if lines[i].strip().startswith("#"):
                        temp_file.write(lines[i])
                    i += 1

                if i < len(lines):
                    file_line = lines[i].strip()
                    if file_line:
                        tracks_processed += 1

                        # Convert local path to Plex path
                        norm_file_path = normalize_path(file_line)

                        if norm_file_path.startswith(norm_local):
                            # Replace local root with Plex root
                            relative_path = norm_file_path[len(norm_local) :].lstrip(
                                "/"
                            )
                            plex_file_path = f"{norm_plex}/{relative_path}"
                            plex_file_path = plex_file_path.replace("\\", "/")

                            # URL encode the path properly (spaces and special characters)
                            if encode_spaces:
                                # Split path into components and encode each part
                                path_parts = plex_file_path.split("/")
                                encoded_parts = []
                                for part in path_parts:
                                    if part:  # Skip empty parts
                                        # Encode special characters but keep forward slashes
                                        encoded_part = urllib.parse.quote(part, safe="")
                                        encoded_parts.append(encoded_part)
                                    else:
                                        encoded_parts.append(part)
                                plex_file_path = "/".join(encoded_parts)

                            temp_file.write(plex_file_path + "\n")
                            tracks_converted += 1

                            logger.debug(f"Converted: {file_line} -> {plex_file_path}")
                        else:
                            logger.warning(
                                f"File path '{norm_file_path}' doesn't start with local root '{norm_local}', keeping original"
                            )
                            temp_file.write(file_line + "\n")

            elif line.startswith("#") or not line:
                # Copy other comments and empty lines as-is
                temp_file.write(line + "\n")
            else:
                # Handle file paths that don't have EXTINF (simple M3U format)
                tracks_processed += 1
                norm_file_path = normalize_path(line)

                if norm_file_path.startswith(norm_local):
                    relative_path = norm_file_path[len(norm_local) :].lstrip("/")
                    plex_file_path = f"{norm_plex}/{relative_path}"
                    plex_file_path = plex_file_path.replace("\\", "/")

                    if encode_spaces:
                        path_parts = plex_file_path.split("/")
                        encoded_parts = []
                        for part in path_parts:
                            if part:
                                encoded_part = urllib.parse.quote(part, safe="")
                                encoded_parts.append(encoded_part)
                            else:
                                encoded_parts.append(part)
                        plex_file_path = "/".join(encoded_parts)

                    temp_file.write(plex_file_path + "\n")
                    tracks_converted += 1

                    logger.debug(f"Converted: {line} -> {plex_file_path}")
                else:
                    logger.warning(
                        f"File path '{norm_file_path}' doesn't start with local root '{norm_local}', keeping original"
                    )
                    temp_file.write(line + "\n")

            i += 1

        temp_playlist_path = temp_file.name

    logger.info(
        f"📊 Converted {tracks_converted}/{tracks_processed} tracks to Plex format"
    )

    if tracks_converted == 0:
        logger.error("No tracks were successfully converted - playlist may not work")

    # Upload the converted playlist file
    try:
        # Construct the correct Plex upload URL
        upload_url = f"{plex_url.rstrip('/')}/playlists/upload"

        params = {
            "sectionID": section_id,
            "path": temp_playlist_path,  # Use the temporary file path
            "X-Plex-Token": plex_token,
        }

        logger.info(f"🚀 Uploading to Plex: {playlist_name}")
        logger.debug(f"Upload URL: {upload_url}")
        logger.debug(f"Temp playlist file: {temp_playlist_path}")
        logger.debug(f"Parameters: {params}")

        # Use the correct Plex API endpoint for uploading playlists
        response = requests.post(upload_url, params=params, timeout=30)

        if response.ok:
            logger.info(f"✅ Successfully uploaded playlist: {playlist_name}")
        else:
            logger.error(
                f"❌ Upload failed for {playlist_name}: {response.status_code} {response.text}"
            )
            raise Exception(
                f"Upload failed with status {response.status_code}: {response.text}"
            )

    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_playlist_path)
            logger.debug(f"Cleaned up temporary file: {temp_playlist_path}")
        except Exception as e:
            logger.warning(
                f"Could not clean up temporary file {temp_playlist_path}: {e}"
            )


def verify_uploaded_playlists(
    plex_url: str, plex_token: str, expected_playlists: Set[str]
) -> None:
    """
    Verifies that the expected playlists (by name) exist on Plex.

    Args:
      plex_url: Base URL of the Plex server.
      plex_token: Plex authentication token.
      expected_playlists: A set of expected playlist names.
    """
    try:
        from plexapi.server import PlexServer

        plex = PlexServer(_prepare_plex_url(plex_url), plex_token)
    except Exception as e:
        logger.error(f"Error connecting to Plex for verification: {e}")
        return

    try:
        plex_playlist_titles = {
            p.title for p in plex.playlists() if p and hasattr(p, "title")
        }
        missing = expected_playlists - plex_playlist_titles
        if missing:
            logger.warning(f"Missing playlists on Plex: {missing}")
        else:
            logger.info("All local playlists have been uploaded to Plex.")
    except Exception as e:
        logger.error(f"Error verifying playlists on Plex: {e}")


def verify_local_playlists_content_in_plex(
    plex_url: str, plex_token: str, generated_files: List[str]
) -> None:
    """
    Verifies that each local m3u8 playlist's track titles match those in Plex.

    Args:
      plex_url: Base URL of the Plex server.
      plex_token: Plex authentication token.
      generated_files: List of local playlist file paths.
    """
    try:
        from plexapi.server import PlexServer

        plex = PlexServer(_prepare_plex_url(plex_url), plex_token)
    except Exception as e:
        logger.error(f"Error connecting to Plex for content verification: {e}")
        return

    for playlist_file in generated_files:
        try:
            playlist_name = os.path.basename(os.path.dirname(playlist_file))
            with open(playlist_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            local_titles = [
                line.strip().split(",", 1)[1]
                for line in lines
                if line.startswith("#EXTINF:") and len(line.strip().split(",", 1)) == 2
            ]
            logger.info(
                f"Local playlist '{playlist_name}' has {len(local_titles)} tracks."
            )

            plex_playlist = next(
                (
                    p
                    for p in plex.playlists()
                    if p
                    and hasattr(p, "title")
                    and p.title.lower() == playlist_name.lower()
                ),
                None,
            )
            if plex_playlist is None:
                logger.warning(f"Plex playlist '{playlist_name}' not found.")
                continue

            plex_titles = [item.title for item in plex_playlist.items()]
            missing_titles = set(local_titles) - set(plex_titles)
            extra_titles = set(plex_titles) - set(local_titles)
            if missing_titles or extra_titles:
                logger.warning(
                    f"In Plex playlist '{playlist_name}', differences found:"
                )
                if missing_titles:
                    logger.warning(f"  Missing in Plex: {missing_titles}")
                if extra_titles:
                    logger.warning(f"  Extra in Plex: {extra_titles}")
            else:
                logger.info(
                    f"Playlist '{playlist_name}' matches between local and Plex."
                )
        except Exception as e:
            logger.error(f"Error verifying content for {playlist_file}: {e}")


def generate_master_playlist(
    generated_files: List[str],
    m3u8_local_root: str,
    m3u8_plex_root: str,
    output_file: str = "master.m3u8",
) -> None:
    """
    Generates a master m3u8 file that lists all remapped playlist file paths.

    Args:
      generated_files: List of local playlist file paths.
      m3u8_local_root: Local root folder for m3u8 files.
      m3u8_plex_root: Plex server root folder for m3u8 files.
      output_file: Path to the master playlist file to be created.
    """
    local_root_norm = normalize_path(m3u8_local_root)
    try:
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
    except Exception as e:
        logger.error(f"Error generating master playlist: {e}")


def create_playlist_from_m3u8(
    file_path: str,
    m3u8_local_root: str,
    m3u8_plex_root: str,
    section_id: Union[str, int],
    plex_token: str,
    plex_url: str,
    force_replace: bool = False,
    progress_callback=None,
    disable_ssl_verification: bool = False,
) -> bool:
    """
    Creates a playlist in Plex using the PlexAPI library by reading an M3U8 file.
    This is the recommended method for creating playlists in Plex.

    Args:
      file_path: Path to the local playlist file.
      m3u8_local_root: Local root folder for the media files.
      m3u8_plex_root: Plex server root folder for the media files.
      section_id: Plex library section ID (string or integer).
      plex_token: Plex authentication token.
      plex_url: Base URL of the Plex server.
      force_replace: Whether to force replace an existing playlist with the same name.
      disable_ssl_verification: Whether to disable SSL certificate verification.

    Returns:
      bool: True if playlist was created successfully, False otherwise.
    """
    try:
        # Configure SSL verification if needed
        if disable_ssl_verification:
            _configure_ssl_verification(disable_ssl_verification=True)
            logger.info(
                "🔓 SSL certificate verification disabled for PlexAPI connection"
            )

            # Suppress SSL warnings when verification is disabled
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Connect to Plex server
        plex = PlexServer(_prepare_plex_url(plex_url), plex_token)
        logger.info(f"✅ Connected to Plex server: {plex.friendlyName}")

        # Get the library section
        original_section_id = section_id
        try:
            # Convert section_id to integer if it's a string
            if isinstance(section_id, str):
                section_id = int(section_id)
            logger.info(
                f"🔍 Looking for library section with ID: {section_id} (original: {original_section_id})"
            )
            section = plex.library.sectionByID(section_id)
            logger.info(
                f"📚 Found library section: '{section.title}' (ID: {section.key}, Type: {section.type})"
            )
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid section ID '{original_section_id}': {e}")
            return False
        except Exception as e:
            logger.error(f"Could not find library section with ID {section_id}: {e}")
            logger.info("💡 Available libraries:")
            try:
                for lib_section in plex.library.sections():
                    logger.info(
                        f"  - {lib_section.title} (ID: {lib_section.key}, Type: {lib_section.type})"
                    )
            except Exception:
                logger.error("Could not list available libraries")
            return False

        # Parse playlist name from file path
        playlist_name = os.path.splitext(os.path.basename(file_path))[0]
        # Normalize the file path for consistent logging
        normalized_path = os.path.normpath(file_path)
        logger.info(
            f"Processing playlist '{playlist_name}' from file: {normalized_path}"
        )

        # Check for existing playlist first to determine if we need full processing
        existing_playlist = None
        try:
            existing_playlists = plex.playlists()
            for playlist in existing_playlists:
                if (
                    playlist
                    and hasattr(playlist, "title")
                    and playlist.title == playlist_name
                ):
                    existing_playlist = playlist
                    logger.info(f"Found existing playlist: {playlist_name}")
                    break
        except Exception as e:
            logger.warning(f"Error checking for existing playlists: {e}")

        # If force_replace is True and playlist exists, delete it first
        if existing_playlist and force_replace:
            logger.info(f"⚠️ Force replacing existing playlist: {playlist_name}")
            try:
                if hasattr(existing_playlist, "delete"):
                    existing_playlist.delete()
                    logger.info(
                        f"Deleted existing playlist for replacement: {playlist_name}"
                    )
                    existing_playlist = None  # Clear reference
            except Exception as delete_e:
                logger.warning(
                    f"Error deleting existing playlist for replacement: {delete_e}"
                )
                return False

        # If incremental mode and playlist exists, load existing tracks for comparison
        existing_playlist_tracks = []
        existing_track_keys = set()

        if existing_playlist and not force_replace:
            logger.info(
                f"Incremental mode: loading existing tracks from playlist '{playlist_name}'..."
            )
            if progress_callback:
                progress_callback("Loading existing playlist tracks...")

            try:
                existing_playlist_tracks = existing_playlist.items()
                existing_track_keys = {
                    track.ratingKey for track in existing_playlist_tracks
                }
                logger.info(
                    f"✅ Loaded {len(existing_playlist_tracks)} existing tracks from playlist"
                )
            except Exception as e:
                logger.warning(f"Error loading existing playlist tracks: {e}")

        # Read and parse the M3U8 file
        tracks = []
        tracks_to_add = []  # Only tracks that need to be added
        try:
            # OPTIMIZATION: For existing playlists, we only need to load library tracks if we find missing ones
            # For new playlists, we still need to load all tracks from library
            all_plex_tracks = None

            if not existing_playlist or force_replace:
                # New playlist or force replace - load all library tracks
                start_time = time.time()
                logger.info(
                    f"🔄 Loading all tracks from library '{section.title}' for matching..."
                )
                if progress_callback:
                    progress_callback("Loading library tracks...")

                all_plex_tracks = section.search(libtype="track")
                load_time = time.time() - start_time
                logger.info(
                    f"✅ Loaded {len(all_plex_tracks)} tracks from Plex library in {load_time:.2f} seconds"
                )

            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Filter out comment lines and get track file paths
            track_lines = [
                line.strip()
                for line in lines
                if line.strip() and not line.startswith("#")
            ]
            total_tracks = len(track_lines)

            logger.info(
                f"📋 Processing {total_tracks} tracks from playlist '{playlist_name}'"
            )

            match_start_time = time.time()
            tracks_already_in_playlist = 0

            for i, line in enumerate(track_lines, 1):
                if progress_callback:
                    progress_callback(
                        f"Checking track {i}/{total_tracks}: {os.path.basename(line)}"
                    )

                # Convert local path to search terms
                local_path = line
                filename = os.path.basename(local_path)
                decoded_filename = unquote(filename)
                track_name = os.path.splitext(decoded_filename)[0]

                # For existing playlists, first check if we can find this track by filename matching
                found_in_existing = False
                if existing_playlist and not force_replace:
                    # Try to find this track in the existing playlist by name matching
                    normalized_search = _normalize_track_name(track_name)

                    for existing_track in existing_playlist_tracks:
                        try:
                            existing_normalized = _normalize_track_name(
                                existing_track.title
                            )
                            if (
                                _similarity_score(
                                    normalized_search, existing_normalized
                                )
                                > 0.85
                            ):
                                tracks.append(existing_track)
                                found_in_existing = True
                                tracks_already_in_playlist += 1
                                logger.debug(
                                    f"✅ [{i}/{total_tracks}] Already in playlist: {track_name}"
                                )
                                break
                        except Exception:
                            continue

                if found_in_existing:
                    continue

                # Track not found in existing playlist, need to search library
                if all_plex_tracks is None:
                    # Lazy load library tracks only when needed
                    start_time = time.time()
                    logger.info(
                        f"🔄 Loading library tracks for missing track search..."
                    )
                    if progress_callback:
                        progress_callback(
                            "Loading library tracks for missing tracks..."
                        )

                    all_plex_tracks = section.search(libtype="track")
                    load_time = time.time() - start_time
                    logger.info(
                        f"✅ Loaded {len(all_plex_tracks)} tracks from library in {load_time:.2f} seconds"
                    )

                # Use optimized matching against library tracks
                try:
                    found_track = _find_best_track_match(track_name, all_plex_tracks)

                    if found_track:
                        tracks.append(found_track)
                        if existing_playlist and not force_replace:
                            tracks_to_add.append(found_track)
                        logger.debug(
                            f"✅ [{i}/{total_tracks}] Found in library: {track_name}"
                        )
                    else:
                        logger.warning(
                            f"❌ [{i}/{total_tracks}] Could not find track: {track_name}"
                        )

                except Exception as e:
                    logger.warning(f"Error matching track '{track_name}': {e}")
                    continue

            match_time = time.time() - match_start_time
            logger.info(f"🚀 Track processing completed in {match_time:.2f} seconds")

            if existing_playlist and not force_replace:
                logger.info(
                    f"📊 Results: {tracks_already_in_playlist} already in playlist, {len(tracks_to_add)} new tracks to add"
                )
            else:
                logger.info(
                    f"📊 Found {len(tracks)}/{total_tracks} tracks ({len(tracks)/total_tracks*100:.1f}% success rate)"
                )

        except Exception as e:
            logger.error(f"Error reading playlist file '{file_path}': {e}")
            return False

        if not tracks:
            logger.error(
                f"No tracks found for playlist '{playlist_name}'. Cannot create empty playlist."
            )
            return False

        # Handle incremental update for existing playlist
        if existing_playlist and not force_replace:
            # Incremental update: only add missing tracks
            try:
                if tracks_to_add:
                    # Add only the missing tracks
                    existing_playlist.addItems(tracks_to_add)
                    logger.info(
                        f"✅ Added {len(tracks_to_add)} new tracks to existing playlist '{playlist_name}' "
                        f"(was {len(existing_playlist_tracks)} tracks, now {len(existing_playlist_tracks) + len(tracks_to_add)} tracks)"
                    )

                    # Log which tracks were added
                    for track in tracks_to_add:
                        try:
                            logger.debug(f"   Added: {track.title}")
                        except:
                            logger.debug(f"   Added: [track]")

                else:
                    logger.info(
                        f"⏭️ Playlist '{playlist_name}' already up-to-date with {len(existing_playlist_tracks)} tracks"
                    )

                return True

            except Exception as e:
                logger.warning(
                    f"Error updating existing playlist '{playlist_name}': {e}"
                )
                logger.info("Falling back to recreating the playlist...")

                # Fallback: delete and recreate if incremental update fails
                try:
                    if hasattr(existing_playlist, "delete"):
                        existing_playlist.delete()
                        logger.info(
                            f"Deleted existing playlist due to update error: {playlist_name}"
                        )
                        existing_playlist = (
                            None  # Clear reference to create new playlist
                        )
                except Exception as delete_e:
                    logger.warning(
                        f"Error deleting playlist for recreation: {delete_e}"
                    )

        # Create new playlist or replace existing one
        if not existing_playlist:
            logger.info(f"Creating new playlist: {playlist_name}")
        else:
            logger.info(f"Replacing existing playlist: {playlist_name}")

        # Create the playlist with retry logic (for new playlists or fallback)
        max_retries = 3
        retry_delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                playlist = plex.createPlaylist(playlist_name, items=tracks)
                logger.info(
                    f"Successfully created playlist '{playlist_name}' with {len(tracks)} tracks"
                )
                return True

            except Exception as e:
                logger.warning(
                    f"Error creating playlist '{playlist_name}' (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    logger.error(
                        f"Failed to create playlist '{playlist_name}' after {max_retries} attempts: {e}"
                    )
                    return False

        # This should never be reached, but add explicit return for safety
        return False

    except Exception as e:
        logger.error(
            f"Error connecting to Plex server or creating playlist from '{file_path}': {e}"
        )
        return False


def test_plex_connection(
    plex_url: str, plex_token: str, disable_ssl_verification: bool = False
) -> bool:
    """
    Test the connection to Plex server and list available libraries.
    This is a diagnostic function to help troubleshoot connection issues.

    Args:
        plex_url: Base URL of the Plex server
        plex_token: Plex authentication token
        disable_ssl_verification: If True, disable SSL certificate verification

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        # Handle URL parsing for PlexAPI - ensure proper format
        clean_url = _prepare_plex_url(plex_url)
        logger.info(f"🔗 Attempting connection to: {clean_url}")

        # Configure SSL verification if needed
        if disable_ssl_verification:
            logger.info("🔓 Disabling SSL certificate verification")
            _configure_ssl_verification(disable_ssl_verification=True)

            # Create PlexServer connection with SSL verification disabled
            import requests
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            session = requests.Session()
            session.verify = False

            try:
                plex = PlexServer(clean_url, plex_token, session=session)
            except TypeError:
                # If session parameter not supported, try without it
                logger.warning(
                    "Session parameter not supported, trying without SSL verification configuration"
                )
                plex = PlexServer(clean_url, plex_token)
        else:
            plex = PlexServer(clean_url, plex_token)

        logger.info(f"✅ Successfully connected to Plex server: {plex.friendlyName}")

        sections = plex.library.sections()
        logger.info(f"📚 Available libraries ({len(sections)}):")
        for section in sections:
            logger.info(
                f"  - {section.title} (ID: {section.key}, Type: {section.type})"
            )

        playlists = plex.playlists()
        logger.info(f"🎵 Existing playlists ({len(playlists)})")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to connect to Plex server: {e}")
        logger.error(f"   URL: {plex_url}")
        logger.error(
            f"   Token: {plex_token[:10]}...{plex_token[-5:] if len(plex_token) > 15 else '[hidden]'}"
        )
        return False


def debug_library_content(
    plex_url: str, plex_token: str, section_id: Union[str, int], limit: int = 10
) -> None:
    """
    Debug function to show sample content from a Plex library section.
    This helps troubleshoot track matching issues.

    Args:
        plex_url: Base URL of the Plex server
        plex_token: Plex authentication token
        section_id: Plex library section ID
        limit: Number of sample tracks to show
    """
    try:
        plex = PlexServer(_prepare_plex_url(plex_url), plex_token)

        # Convert section_id to integer if needed
        if isinstance(section_id, str):
            section_id = int(section_id)

        section = plex.library.sectionByID(section_id)

        logger.info(f"🔍 Library '{section.title}' content sample ({limit} tracks):")

        tracks = section.search(libtype="track")[:limit]
        for i, track in enumerate(tracks, 1):
            try:
                artist = track.artist().title if track.artist() else "Unknown Artist"
                album = track.album().title if track.album() else "Unknown Album"
                logger.info(f"  {i}. {track.title} by {artist} (Album: {album})")
            except Exception as e:
                logger.info(f"  {i}. {track.title} (Error getting details: {e})")

        total_tracks = len(section.search(libtype="track"))
        logger.info(f"📊 Total tracks in library: {total_tracks}")

    except Exception as e:
        logger.error(f"Error debugging library content: {e}")


def compare_m3u8_to_plex_playlist(
    m3u8_file: str,
    plex_url: str,
    plex_token: str,
    section_id: Union[str, int],
    playlist_name: Union[str, None] = None,
) -> dict:
    """
    Compare an M3U8 file to a Plex playlist and return detailed comparison results.
    This is a diagnostic tool to help understand which tracks are missing.

    Args:
        m3u8_file: Path to the M3U8 file to compare
        plex_url: Base URL of the Plex server
        plex_token: Plex authentication token
        section_id: Plex library section ID
        playlist_name: Name of Plex playlist (defaults to M3U8 filename)

    Returns:
        dict: Comparison results with statistics and missing tracks
    """
    try:
        # Connect to Plex
        plex = PlexServer(_prepare_plex_url(plex_url), plex_token)

        # Get playlist name from file if not provided
        if not playlist_name:
            playlist_name = os.path.splitext(os.path.basename(m3u8_file))[0]

        # Find Plex playlist
        plex_playlist = None
        for playlist in plex.playlists():
            if (
                playlist
                and hasattr(playlist, "title")
                and playlist.title == playlist_name
            ):
                plex_playlist = playlist
                break

        if not plex_playlist:
            return {
                "error": f"Plex playlist '{playlist_name}' not found",
                "available_playlists": [
                    p.title for p in plex.playlists() if p and hasattr(p, "title")
                ],
            }

        # Read M3U8 file
        with open(m3u8_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        m3u8_tracks = []
        for line in lines:
            if line.strip() and not line.startswith("#"):
                filename = os.path.basename(line.strip())
                track_name = os.path.splitext(unquote(filename))[0]
                m3u8_tracks.append(_normalize_track_name(track_name))

        # Get Plex playlist tracks
        plex_tracks = []
        for track in plex_playlist.items():
            plex_tracks.append(_normalize_track_name(track.title))

        # Compare tracks
        m3u8_set = set(m3u8_tracks)
        plex_set = set(plex_tracks)

        missing_in_plex = m3u8_set - plex_set
        extra_in_plex = plex_set - m3u8_set
        common_tracks = m3u8_set & plex_set

        # Calculate match percentage
        if m3u8_tracks:
            match_percentage = len(common_tracks) / len(m3u8_tracks) * 100
        else:
            match_percentage = 0

        results = {
            "playlist_name": playlist_name,
            "m3u8_file": m3u8_file,
            "total_m3u8_tracks": len(m3u8_tracks),
            "total_plex_tracks": len(plex_tracks),
            "common_tracks": len(common_tracks),
            "missing_in_plex": list(missing_in_plex),
            "extra_in_plex": list(extra_in_plex),
            "match_percentage": round(match_percentage, 1),
            "status": "OK" if not missing_in_plex else "MISSING_TRACKS",
        }

        # Log summary
        logger.info(f"📊 Playlist Comparison Results for '{playlist_name}':")
        logger.info(f"  M3U8 tracks: {len(m3u8_tracks)}")
        logger.info(f"  Plex tracks: {len(plex_tracks)}")
        logger.info(f"  Common tracks: {len(common_tracks)}")
        logger.info(f"  Missing in Plex: {len(missing_in_plex)}")
        logger.info(f"  Extra in Plex: {len(extra_in_plex)}")
        logger.info(f"  Match percentage: {match_percentage:.1f}%")

        if missing_in_plex:
            logger.warning(f"❌ Missing tracks in Plex:")
            for track in list(missing_in_plex)[:10]:  # Show first 10
                logger.warning(f"    - {track}")
            if len(missing_in_plex) > 10:
                logger.warning(f"    ... and {len(missing_in_plex) - 10} more")

        return results

    except Exception as e:
        logger.error(f"Error comparing M3U8 to Plex playlist: {e}")
        return {"error": str(e)}


def upload_playlist_via_http_api(
    file_path: str,
    m3u8_local_root: str,
    m3u8_plex_root: str,
    server_file_path: str,
    section_id: str,
    plex_token: str,
    plex_url: str,
    encode_spaces: bool = True,
    disable_ssl_verification: bool = False,
) -> None:
    """
    Uploads a playlist file to Plex using the HTTP API endpoint.

    This method assumes that M3U/M3U8 playlist files already exist on the Plex server
    at the specified server_file_path. It simply references these existing files
    and instructs Plex to import them using the HTTP API.

    IMPORTANT: The playlist files must already exist on the Plex server at the
    server_file_path location and be accessible by the Plex Media Server process.

    Args:
      file_path: Path to the local playlist file (used to determine the playlist name).
      m3u8_local_root: Local root folder for the media files (unused, kept for compatibility).
      m3u8_plex_root: Plex server root folder for the media files (unused, kept for compatibility).
      server_file_path: Full path to the playlist file on the Plex server.
      section_id: Plex library section ID.
      plex_token: Plex authentication token.
      plex_url: Base URL of the Plex server (e.g., "http://localhost:32400").
      encode_spaces: Whether to encode spaces as %20 in the path (unused, kept for compatibility).
    """
    playlist_name = os.path.splitext(os.path.basename(file_path))[0]
    logger.info(
        f"🔄 Referencing existing playlist '{playlist_name}' for HTTP API upload..."
    )

    # The server_file_path is already the full path to the playlist file on the server
    logger.info(f"📁 Using server playlist file: {server_file_path}")

    # Configure SSL verification if needed
    if disable_ssl_verification:
        _configure_ssl_verification(disable_ssl_verification=True)
        logger.info("🔓 SSL certificate verification disabled for HTTP API upload")

        # Suppress SSL warnings when verification is disabled
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Upload the playlist using the HTTP API
    try:
        # Construct the correct Plex upload URL
        upload_url = f"{plex_url.rstrip('/')}/playlists/upload"

        params = {
            "sectionID": section_id,
            "path": server_file_path,  # Use the server file path
            "force": 1,  # Force overwrite existing playlists
            "X-Plex-Token": plex_token,
        }

        logger.info(f"🚀 Uploading to Plex via HTTP API: {playlist_name}")
        logger.debug(f"Upload URL: {upload_url}")
        logger.debug(f"Server playlist file: {server_file_path}")
        logger.debug(f"Parameters: {params}")

        # Use the Plex HTTP API endpoint for uploading playlists
        # Configure SSL verification for the requests call
        ssl_verify = not disable_ssl_verification
        if disable_ssl_verification:
            logger.debug("🔓 Disabling SSL verification for HTTP API request")

        response = requests.post(
            upload_url, params=params, timeout=30, verify=ssl_verify
        )

        logger.info(f"📡 HTTP Response: {response.status_code}")
        if response.text:
            logger.debug(f"Response body: {response.text}")

        if response.ok:
            logger.info(
                f"✅ Successfully uploaded playlist via HTTP API: {playlist_name}"
            )
        else:
            logger.error(
                f"❌ HTTP API upload failed for {playlist_name}: {response.status_code} {response.text}"
            )
            raise Exception(
                f"HTTP API upload failed with status {response.status_code}: {response.text}"
            )

    except Exception as e:
        logger.error(f"Error during HTTP API upload: {e}")
        raise


def _normalize_track_name(name: str) -> str:
    """
    Normalize a track name for better matching by removing common patterns.

    Args:
        name: Original track name

    Returns:
        Normalized track name
    """
    # Remove file extension
    name = os.path.splitext(name)[0]

    # URL decode
    name = unquote(name)

    # Remove track numbers at the start (e.g., "01 - ", "1. ")
    name = re.sub(r"^\d+\s*[-\.]\s*", "", name)

    # Remove quality indicators and brackets
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\[[^\]]*\]", "", name)

    # Clean up whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name.lower()


def _extract_artist_and_title(track_name: str) -> tuple:
    """
    Extract artist and title from track name patterns.

    Args:
        track_name: Track name to parse

    Returns:
        Tuple of (artist, title) or (None, track_name) if no pattern found
    """
    if " - " not in track_name:
        return None, track_name

    parts = track_name.split(" - ")

    # Skip track number if present
    start_idx = 0
    if len(parts) > 0 and parts[0].strip().replace(".", "").isdigit():
        start_idx = 1

    if len(parts) > start_idx + 1:
        artist = parts[start_idx].strip()
        title = " - ".join(parts[start_idx + 1 :]).strip()
        return artist, title

    return None, track_name


def _similarity_score(a: str, b: str) -> float:
    """Calculate similarity score between two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _find_best_track_match(
    search_name: str, plex_tracks: list, min_similarity: float = 0.6
) -> object:
    """
    Find the best matching track from a pre-loaded list of Plex tracks.

    Args:
        search_name: Name to search for
        plex_tracks: List of all Plex track objects
        min_similarity: Minimum similarity threshold

    Returns:
        Best matching track object or None
    """
    normalized_search = _normalize_track_name(search_name)
    artist_search, title_search = _extract_artist_and_title(normalized_search)

    best_match = None
    best_score = 0.0

    for track in plex_tracks:
        try:
            # Get track info
            track_title = track.title.lower()
            track_artist = track.artist().title.lower() if track.artist() else ""

            # Strategy 1: Exact title match
            if normalized_search == track_title:
                return track

            # Strategy 2: Artist + title match
            if artist_search and title_search:
                if (
                    artist_search.lower() in track_artist
                    and _similarity_score(title_search, track_title) > 0.8
                ):
                    return track

            # Strategy 3: High similarity title match
            title_similarity = _similarity_score(normalized_search, track_title)
            if title_similarity > best_score and title_similarity >= min_similarity:
                best_score = title_similarity
                best_match = track

            # Strategy 4: If we have artist info, check combined similarity
            if artist_search and title_search:
                combined_track = f"{track_artist} {track_title}"
                combined_search = f"{artist_search.lower()} {title_search}"
                combined_similarity = _similarity_score(combined_search, combined_track)
                if (
                    combined_similarity > best_score
                    and combined_similarity >= min_similarity
                ):
                    best_score = combined_similarity
                    best_match = track

        except Exception as e:
            logger.debug(f"Error processing track {track}: {e}")
            continue

    return best_match if best_score >= min_similarity else None

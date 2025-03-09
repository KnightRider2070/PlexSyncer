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
from typing import List, Set

import requests

from plexsyncer.helpers import normalize_path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)


def get_section_id_from_library(
        plex_url: str, plex_token: str, library_name: str
) -> str:
    """
    Retrieves the Plex library section ID for a given library name.

    Args:
      plex_url: Base URL of the Plex server (e.g., "http://localhost:32400").
      plex_token: Plex authentication token.
      library_name: Name of the Plex library to use.

    Returns:
      The section ID as a string.

    Raises:
      Exception: If the library cannot be found or API call fails.
    """
    try:
        from plexapi.server import PlexServer

        plex = PlexServer(plex_url.rstrip("/"), plex_token)
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
        api_url_base: str,
        encode_spaces: bool,
) -> None:
    """
    Uploads a playlist file to Plex using its API. Remaps the local file path to the Plex file path.

    Args:
      file_path: Path to the local playlist file.
      m3u8_local_root: Local root folder for the media files.
      m3u8_plex_root: Plex server root folder for the media files.
      section_id: Plex library section ID.
      plex_token: Plex authentication token.
      api_url_base: Base URL for the Plex playlists upload API.
      encode_spaces: Whether to encode spaces as %20 in the path.
    """
    norm_file_path = normalize_path(file_path)
    norm_local = normalize_path(m3u8_local_root)
    logger.debug(f"Normalized file path: {norm_file_path}")
    if norm_file_path.startswith(norm_local):
        new_path = norm_file_path.replace(norm_local, m3u8_plex_root, 1)
    else:
        logger.warning(
            f"File path '{norm_file_path}' does not start with '{norm_local}'. Using original path."
        )
        new_path = norm_file_path
    if encode_spaces:
        new_path = new_path.replace(" ", "%20")
    params = {
        "sectionID": section_id,
        "path": new_path,
        "X-Plex-Token": plex_token,
    }
    logger.info(f"Uploading '{file_path}' with remapped path '{new_path}'...")
    try:
        # If necessary, rename .m3u8 to .m3u for upload.
        upload_path = file_path
        if file_path.endswith(".m3u8"):
            new_name = file_path.rsplit(".", 1)[0] + ".m3u"
            os.rename(file_path, new_name)
            upload_path = new_name
            logger.info(f"Renamed file for upload: {file_path} -> {new_name}")
        with open(upload_path, "rb") as f:
            files = {"file": (os.path.basename(upload_path), f)}
            response = requests.post(api_url_base, params=params, files=files)
        if response.ok:
            logger.info(f"Successfully uploaded: {upload_path}")
        else:
            logger.error(
                f"Upload failed for {upload_path}: {response.status_code} {response.text}"
            )
    except Exception as e:
        logger.error(f"Error uploading {file_path}: {e}")


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

        plex = PlexServer(plex_url.rstrip("/"), plex_token)
    except Exception as e:
        logger.error(f"Error connecting to Plex for verification: {e}")
        return

    try:
        plex_playlist_titles = {p.title for p in plex.playlists()}
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

        plex = PlexServer(plex_url.rstrip("/"), plex_token)
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
                    if p.title.lower() == playlist_name.lower()
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

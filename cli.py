#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI for PlexSyncer

This tool scans a local folder of media files (organized in subdirectories) and creates
m3u8 playlists with an #EXTM3U header and #EXTINF lines. It supports incremental updates,
uploads the playlists to Plex via its API, verifies uploads, and generates a master playlist
listing all remapped playlist paths.

"""

import argparse
import logging
import os

from plexsyncer.api import (
    generate_master_playlist,
    get_section_id_from_library,
    upload_playlist_via_api,
    verify_local_playlists_content_in_plex,
    verify_uploaded_playlists,
)
from plexsyncer.playlist import process_library

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)


def command_generate(args):
    # Generate or use existing m3u8 playlists.
    if args.use_existing:
        logger.info("Using existing m3u8 files from playlist folder...")
        generated_files = []
        for root, _, files in os.walk(args.playlist_folder):
            for f in files:
                if f.lower().endswith(".m3u8"):
                    generated_files.append(os.path.join(root, f))
        logger.info(f"Found {len(generated_files)} existing playlist files.")
    else:
        logger.info("Generating playlists...")
        generated_files = process_library(
            playlist_folder=args.playlist_folder,
            m3u8_local_root=args.m3u8_local_root,
            m3u8_plex_root=args.m3u8_plex_root,
            encode_spaces=args.encode_spaces,
            incremental=args.incremental,
            ext=".m3u8",
        )
        logger.info("Playlist generation complete.")

    # Upload playlists if not generate-only.
    if not args.generate_only:
        try:
            section_id = get_section_id_from_library(
                args.plex_url, args.plex_token, args.library_name
            )
        except Exception as e:
            logger.error(f"Unable to determine section id: {e}")
            return
        for file_path in generated_files:
            if os.path.exists(file_path):
                upload_playlist_via_api(
                    file_path=file_path,
                    m3u8_local_root=args.m3u8_local_root,
                    m3u8_plex_root=args.m3u8_plex_root,
                    section_id=section_id,
                    plex_token=args.plex_token,
                    api_url_base=args.api_url,
                    encode_spaces=args.encode_spaces,
                )
            else:
                logger.error(f"File not found: {file_path}")

    # Verify uploads if requested.
    if args.verify_uploads:
        expected = set()
        try:
            for entry in os.scandir(args.playlist_folder):
                if entry.is_dir():
                    expected.add(entry.name)
        except Exception as e:
            logger.error(f"Error scanning expected playlists: {e}")
        verify_uploaded_playlists(args.plex_url, args.plex_token, expected)

    # Verify local m3u8 content against Plex if requested.
    if args.verify_m3u8:
        verify_local_playlists_content_in_plex(
            args.plex_url, args.plex_token, generated_files
        )

    # Generate master playlist.
    logger.info("Generating master playlist based on provided m3u8 mapping...")
    generate_master_playlist(
        generated_files,
        m3u8_local_root=args.m3u8_local_root,
        m3u8_plex_root=args.m3u8_plex_root,
        output_file="master.m3u8",
    )


def command_verify(args):
    # Verify that uploaded playlists exist on Plex.
    expected = set()
    try:
        for entry in os.scandir(args.playlist_folder):
            if entry.is_dir():
                expected.add(entry.name)
    except Exception as e:
        logger.error(f"Error scanning expected playlists: {e}")
        return
    verify_uploaded_playlists(args.plex_url, args.plex_token, expected)
    # Optionally verify local m3u8 content.
    if args.verify_m3u8:
        generated_files = []
        for root, _, files in os.walk(args.playlist_folder):
            for f in files:
                if f.lower().endswith(".m3u8"):
                    generated_files.append(os.path.join(root, f))
        verify_local_playlists_content_in_plex(
            args.plex_url, args.plex_token, generated_files
        )


def main():
    parser = argparse.ArgumentParser(description="Plex Playlist Generator & Uploader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 'generate' command.
    gen_parser = subparsers.add_parser(
        "generate", help="Generate (and optionally upload) playlists"
    )
    gen_parser.add_argument(
        "--playlist-folder",
        required=True,
        help="Folder containing playlist subdirectories",
    )
    gen_parser.add_argument(
        "--m3u8-local-root", required=True, help="Local root folder for m3u8 files"
    )
    gen_parser.add_argument(
        "--m3u8-plex-root", required=True, help="Plex root folder for m3u8 files"
    )
    gen_parser.add_argument(
        "--api-url",
        default="https://plex.example.com/playlists/upload",
        help="API URL for uploading playlists",
    )
    gen_parser.add_argument(
        "--plex-token", required=True, help="Plex token for API calls"
    )
    gen_parser.add_argument(
        "--library-name", required=True, help="Name of the Plex library to use"
    )
    gen_parser.add_argument(
        "--plex-url", required=True, help="Base URL of your Plex server"
    )
    gen_parser.add_argument(
        "--incremental",
        action="store_true",
        help="Append only new tracks if playlist already exists",
    )
    gen_parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate/update playlists only; skip upload",
    )
    gen_parser.add_argument(
        "--use-existing",
        action="store_true",
        help="Use existing m3u8 files; skip regeneration",
    )
    gen_parser.add_argument(
        "--verify-uploads",
        action="store_true",
        help="Verify that playlists exist on Plex (by name)",
    )
    gen_parser.add_argument(
        "--verify-m3u8",
        action="store_true",
        help="Verify that each local m3u8 playlist's track titles exist in Plex",
    )
    gen_parser.add_argument(
        "--encode-spaces",
        action="store_true",
        help="Encode spaces as %20 in file paths",
    )
    gen_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging (DEBUG level)"
    )
    gen_parser.set_defaults(func=command_generate)

    # 'verify' command.
    ver_parser = subparsers.add_parser(
        "verify", help="Verify uploaded playlists in Plex"
    )
    ver_parser.add_argument(
        "--playlist-folder",
        required=True,
        help="Folder containing playlist subdirectories",
    )
    ver_parser.add_argument(
        "--plex-url", required=True, help="Base URL of your Plex server"
    )
    ver_parser.add_argument(
        "--plex-token", required=True, help="Plex token for API calls"
    )
    ver_parser.add_argument(
        "--verify-m3u8",
        action="store_true",
        help="Also verify local m3u8 content against Plex",
    )
    ver_parser.set_defaults(func=command_verify)

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled.")

    args.func(args)


if __name__ == "__main__":
    main()

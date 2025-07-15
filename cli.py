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
import json
import logging
import os

from integrations.spotify import SpotifyIntegration
from integrations.tidal import TidalIntegration
from plexsyncer.api import (
    create_playlist_from_m3u8,
    generate_master_playlist,
    get_section_id_from_library,
    upload_playlist_via_api,
    verify_local_playlists_content_in_plex,
    verify_uploaded_playlists,
)
from plexsyncer.playlist import process_library

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Default log level is INFO

# Console output (StreamHandler)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)

# Optional: File output (FileHandler)
file_handler = logging.FileHandler("plexsyncer.log")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)


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

        # Determine parallel processing settings
        parallel = args.parallel and not args.sequential
        max_workers = args.max_workers if hasattr(args, "max_workers") else None

        if parallel:
            logger.info("🚀 Using parallel processing for faster generation...")
        else:
            logger.info("📝 Using sequential processing...")

        generated_files = process_library(
            playlist_folder=args.playlist_folder,
            m3u8_local_root=args.m3u8_local_root,
            m3u8_plex_root=args.m3u8_plex_root,
            encode_spaces=args.encode_spaces,
            incremental=args.incremental,
            ext=".m3u8",
            parallel=parallel,
            max_workers=max_workers,
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

        success_count = 0
        total_count = len(generated_files)

        for file_path in generated_files:
            if os.path.exists(file_path):
                success = create_playlist_from_m3u8(
                    file_path=file_path,
                    m3u8_local_root=args.m3u8_local_root,
                    m3u8_plex_root=args.m3u8_plex_root,
                    section_id=section_id,
                    plex_token=args.plex_token,
                    plex_url=args.plex_url,
                )
                if success:
                    success_count += 1
            else:
                logger.error(f"File not found: {file_path}")

        logger.info(
            f"Playlist creation finished. {success_count}/{total_count} playlists created successfully."
        )

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


def command_spotify_export(args):
    si = SpotifyIntegration(
        client_id=args.client_id,
        client_secret=args.client_secret,
        use_oauth=args.use_oauth,
        redirect_uri=args.redirect_uri,
        scope=args.scope,
    )
    # fetch all playlists+tracks
    data = si.fetch_user_playlists_with_tracks()
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Spotify playlists exported to {args.output}")


def command_spotify_augment(args):
    si = SpotifyIntegration(
        client_id=args.client_id,
        client_secret=args.client_secret,
        use_oauth=args.use_oauth,
        redirect_uri=args.redirect_uri,
        scope=args.scope,
    )
    si.augment_tidal_with_spotify_ids(
        tidal_json_path=args.input_json, output_path=args.output_json
    )
    logger.info(f"Augmented JSON with Spotify URIs → {args.output_json}")


def command_spotify_push(args):
    si = SpotifyIntegration(
        client_id=args.client_id,
        client_secret=args.client_secret,
        use_oauth=args.use_oauth,
        redirect_uri=args.redirect_uri,
        scope=args.scope,
    )
    si.add_playlists_and_tracks_from_json(args.input_json)
    logger.info(f"Pushed playlists from {args.input_json} to Spotify")


def command_tidal_export(args):
    ti = TidalIntegration(
        client_id=args.client_id,
        client_secret=args.client_secret,
        personal_access_token=args.personal_access_token,
        redirect_uri=args.redirect_uri,
    )
    data = ti.fetch_user_playlists_with_tracks()
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Tidal playlists exported to {args.output}")


def command_tidal_augment(args):
    ti = TidalIntegration(
        client_id=args.client_id,
        client_secret=args.client_secret,
        personal_access_token=args.personal_access_token,
        redirect_uri=args.redirect_uri,
    )
    ti.augment_spotify_with_tidal_ids(
        spotify_json_path=args.input_json, output_path=args.output_json
    )
    logger.info(f"Augmented JSON with Tidal IDs → {args.output_json}")


def command_tidal_push(args):
    ti = TidalIntegration(
        client_id=args.client_id,
        client_secret=args.client_secret,
        personal_access_token=args.personal_access_token,
        redirect_uri=args.redirect_uri,
    )
    ti.add_playlists_and_tracks_from_json(args.input_json)
    logger.info(f"Pushed playlists from {args.input_json} to Tidal")


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
        "--parallel",
        action="store_true",
        default=True,
        help="Use parallel processing for faster playlist generation (default: True)",
    )
    gen_parser.add_argument(
        "--sequential",
        action="store_true",
        help="Use sequential processing instead of parallel (overrides --parallel)",
    )
    gen_parser.add_argument(
        "--max-workers",
        type=int,
        help="Maximum number of parallel workers (defaults to CPU count)",
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
    ver_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging (DEBUG level)"
    )
    ver_parser.set_defaults(func=command_verify)

    migrate = subparsers.add_parser(
        "migrate", help="Migrate playlists between Spotify and Tidal"
    )
    mig_sub = migrate.add_subparsers(dest="service", required=True)

    # Spotify side
    sp_exp = mig_sub.add_parser(
        "spotify-export", help="Export your Spotify playlists to JSON"
    )
    sp_exp.add_argument("--client-id", required=True)
    sp_exp.add_argument("--client-secret", required=True)
    sp_exp.add_argument("--use-oauth", action="store_true")
    sp_exp.add_argument("--redirect-uri")
    sp_exp.add_argument("--scope")
    sp_exp.add_argument("--output", required=True, help="Output JSON path")
    sp_exp.set_defaults(func=command_spotify_export)

    sp_aug = mig_sub.add_parser(
        "spotify-augment", help="Augment a Tidal JSON with Spotify URIs"
    )
    sp_aug.add_argument("--client-id", required=True)
    sp_aug.add_argument("--client-secret", required=True)
    sp_aug.add_argument("--use-oauth", action="store_true")
    sp_aug.add_argument("--redirect-uri")
    sp_aug.add_argument("--scope")
    sp_aug.add_argument("--input-json", required=True)
    sp_aug.add_argument("--output-json", required=True)
    sp_aug.set_defaults(func=command_spotify_augment)

    sp_push = mig_sub.add_parser(
        "spotify-push", help="Create/update Spotify playlists from JSON"
    )
    sp_push.add_argument("--client-id", required=True)
    sp_push.add_argument("--client-secret", required=True)
    sp_push.add_argument("--use-oauth", action="store_true")
    sp_push.add_argument("--redirect-uri")
    sp_push.add_argument("--scope")
    sp_push.add_argument("--input-json", required=True)
    sp_push.set_defaults(func=command_spotify_push)

    # Tidal side
    td_exp = mig_sub.add_parser(
        "tidal-export", help="Export your Tidal playlists to JSON"
    )
    td_exp.add_argument("--client-id", required=True)
    td_exp.add_argument("--client-secret")
    td_exp.add_argument("--personal-access-token")
    td_exp.add_argument("--redirect-uri")
    td_exp.add_argument("--output", required=True)
    td_exp.set_defaults(func=command_tidal_export)

    td_aug = mig_sub.add_parser(
        "tidal-augment", help="Augment a Spotify JSON with Tidal IDs"
    )
    td_aug.add_argument("--client-id", required=True)
    td_aug.add_argument("--client-secret")
    td_aug.add_argument("--personal-access-token")
    td_aug.add_argument("--redirect-uri")
    td_aug.add_argument("--input-json", required=True)
    td_aug.add_argument("--output-json", required=True)
    td_aug.set_defaults(func=command_tidal_augment)

    td_push = mig_sub.add_parser(
        "tidal-push", help="Create/update Tidal playlists from JSON"
    )
    td_push.add_argument("--client-id", required=True)
    td_push.add_argument("--client-secret")
    td_push.add_argument("--personal-access-token")
    td_push.add_argument("--redirect-uri")
    td_push.add_argument("--input-json", required=True)
    td_push.set_defaults(func=command_tidal_push)

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled.")

    args.func(args)


if __name__ == "__main__":
    main()

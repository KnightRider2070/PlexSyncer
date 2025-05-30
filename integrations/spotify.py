import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

__all__ = [
    "extract_playlist_id",
    "sanitize_filename",
    "SpotifyIntegration",
]

# ---------- Logging Configuration ----------


def configure_logging(
    name: str = __name__,
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    logger = logging.getLogger(name)
    if logger.handlers:
        return
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if log_file:
        from logging.handlers import RotatingFileHandler

        fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)


# setup default logging at import
configure_logging(log_file="spotify_integration.log")
logger = logging.getLogger(__name__)


# ---------- Constants for Backoff ----------
_MAX_RETRIES = 5
_BASE_BACKOFF = 1.0  # seconds
_MAX_BACKOFF = 60.0  # seconds
_REQUEST_TIMEOUT = 10.0


# ---------- Utility Functions ----------


def extract_playlist_id(url: str) -> str:
    pattern = r"(?:playlist[/:])([A-Za-z0-9]+)"
    match = re.search(pattern, url)
    if not match:
        logger.error("Invalid Spotify playlist URL or URI: %s", url)
        raise ValueError(f"Invalid Spotify playlist URL or URI: {url}")
    return match.group(1)


def sanitize_filename(name: str) -> str:
    # Allow letters, digits, underscore, hyphen, and spaces; replace others with space
    cleaned = re.sub(r"[^\w\- ]", " ", name)
    # Collapse multiple spaces into one
    cleaned = re.sub(r" +", " ", cleaned)
    return cleaned.strip()


def _retry_spotify_call(callable_func, *args, **kwargs):
    """
    Retry a spotipy call with exponential backoff, jitter, and rate-limit handling.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return callable_func(*args, **kwargs)
        except SpotifyException as exc:
            status = exc.http_status
            if status == 429:
                retry_after = int(exc.headers.get("Retry-After", 1))
                backoff = (
                    min(_MAX_BACKOFF, _BASE_BACKOFF * (2 ** (attempt - 1)))
                    + retry_after
                )
                wait = random.uniform(0, backoff)
                logger.warning(
                    "Spotify rate limit hit; retrying in %.2f seconds (attempt %d)",
                    wait,
                    attempt,
                )
                time.sleep(wait)
                continue
            if 500 <= status < 600:
                backoff = min(_MAX_BACKOFF, _BASE_BACKOFF * (2 ** (attempt - 1)))
                wait = random.uniform(0, backoff)
                logger.warning(
                    "Spotify server error %s; retrying in %.2f seconds (attempt %d)",
                    status,
                    wait,
                    attempt,
                )
                time.sleep(wait)
                continue
            raise
    # Final attempt
    return callable_func(*args, **kwargs)


# ---------- Core Class ----------


class SpotifyIntegration:
    """
    A helper class for interacting with Spotify and exporting playlist data.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        use_oauth: bool = False,
        redirect_uri: Optional[str] = None,
        scope: Optional[str] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.use_oauth = use_oauth
        self.redirect_uri = redirect_uri
        self.scope = scope
        self._client: Optional[spotipy.Spotify] = None

    def _get_client(self) -> spotipy.Spotify:
        if not self._client:
            if self.use_oauth:
                if not self.redirect_uri:
                    raise ValueError(
                        "redirect_uri is required for OAuth authentication."
                    )
                auth_manager = SpotifyOAuth(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    redirect_uri=self.redirect_uri,
                    scope=self.scope,
                )
                logger.debug("Authenticated via SpotifyOAuth.")
            else:
                auth_manager = SpotifyClientCredentials(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                )
                logger.debug("Authenticated via Client Credentials.")
            self._client = spotipy.Spotify(auth_manager=auth_manager)
        return self._client

    def extract_titles(self, playlist_url: str) -> List[str]:
        sp = self._get_client()
        pid = extract_playlist_id(playlist_url)
        titles: List[str] = []
        results = _retry_spotify_call(sp.playlist_items, pid)
        while results:
            for item in results.get("items", []):
                track = item.get("track")
                if track and track.get("name"):
                    titles.append(track["name"])
            nxt = results.get("next")
            if not nxt:
                break
            results = _retry_spotify_call(sp.next, results)
        logger.info("Extracted %d titles from playlist %s.", len(titles), pid)
        return titles

    def fetch_tracks(
        self,
        playlist_id: str,
        encode_spaces: bool = False,
        page_limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sp = self._get_client()
        tracks: List[Dict[str, Any]] = []
        offset = 0
        while True:
            resp = _retry_spotify_call(
                sp.playlist_items,
                playlist_id,
                limit=page_limit,
                offset=offset,
                fields="items.track.name,items.track.artists.name,items.track.album.name,items.track.uri,next",
            )
            for item in resp.get("items", []):
                track = item.get("track") or {}
                name = track.get("name", "Unknown")
                artists = [a.get("name", "Unknown") for a in track.get("artists", [])]
                album = track.get("album", {}).get("name", "Unknown")
                uri = (
                    track.get("uri", "").replace(" ", "%20")
                    if encode_spaces
                    else track.get("uri", "")
                )
                tracks.append(
                    {
                        "trackName": name,
                        "artistName": ", ".join(artists),
                        "albumName": album,
                        "trackUri_spotify": uri,
                    }
                )
            if not resp.get("next"):
                break
            offset += page_limit
        logger.info("Fetched %d tracks from playlist %s.", len(tracks), playlist_id)
        return tracks

    def export_playlist(
        self,
        playlist_url: str,
        output_path: str,
        encode_spaces: bool = False,
    ) -> None:
        sp = self._get_client()
        pid = extract_playlist_id(playlist_url)
        meta = _retry_spotify_call(sp.playlist, pid)
        name = meta.get("name", "Unnamed")
        tracks = self.fetch_tracks(pid, encode_spaces)
        data = {"playlist": {"name": name, "tracks": tracks}}
        self._save(data, output_path)
        logger.info("Exported playlist '%s' to %s.", name, output_path)

    def transform_local_structure(
        self,
        input_file: str,
        output_file: str,
        encode_spaces: bool = False,
    ) -> None:
        try:
            data = json.loads(Path(input_file).read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed reading %s: %s", input_file, e)
            return

        playlists = data.get("playlists", [])
        out: Dict[str, Any] = {"playlists": []}
        for pl in playlists:
            items = pl.get("items", [])
            tracks: List[Dict[str, Any]] = []
            for item in items:
                t = item.get("track", {})
                uri = (
                    t.get("trackUri_spotify", "").replace(" ", "%20")
                    if encode_spaces
                    else t.get("trackUri_spotify", "")
                )
                tracks.append(
                    {
                        "trackName": t.get("trackName", "Unknown"),
                        "artistName": t.get("artistName", "Unknown"),
                        "albumName": t.get("albumName", "Unknown"),
                        "trackUri_spotify": uri,
                    }
                )
            out["playlists"].append(
                {
                    "name": pl.get("name", "Unnamed"),
                    "lastModifiedDate": pl.get("lastModifiedDate", ""),
                    "tracks": tracks,
                }
            )
            logger.info(
                "Processed '%s' with %d tracks.", pl.get("name", "Unnamed"), len(tracks)
            )
        self._save(out, output_file)

    def update_local_export(
        self,
        local_file: str,
        output_file: str,
    ) -> None:
        try:
            _ = json.loads(Path(local_file).read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Cannot read %s: %s", local_file, e)
            return

        saved_playlists = self.fetch_user_playlists_with_tracks()
        loved = self.fetch_loved_tracks()
        combined = {**saved_playlists, **loved}
        self._save(combined, output_file)

    def fetch_user_playlists_with_tracks(
        self,
        encode_spaces: bool = False,
        page_limit: int = 50,
    ) -> Dict[str, Any]:
        sp = self._get_client()
        all_playlists: List[Dict[str, Any]] = []
        offset = 0
        while True:
            resp = _retry_spotify_call(
                sp.current_user_playlists, limit=page_limit, offset=offset
            )
            for pl in resp.get("items", []):
                pid = pl.get("id")
                name = pl.get("name")
                logger.info("Fetching '%s' (%s) tracks...", name, pid)
                tracks = self.fetch_tracks(pid, encode_spaces)
                all_playlists.append({"id": pid, "name": name, "tracks": tracks})
            if not resp.get("next"):
                break
            offset += page_limit
        return {"playlists": all_playlists}

    def fetch_loved_tracks(
        self,
        encode_spaces: bool = False,
        page_limit: int = 50,
    ) -> Dict[str, Any]:
        sp = self._get_client()
        loved: List[Dict[str, Any]] = []
        offset = 0
        while True:
            resp = _retry_spotify_call(
                sp.current_user_saved_tracks, limit=page_limit, offset=offset
            )
            for item in resp.get("items", []):
                t = item.get("track", {})
                uri = (
                    t.get("uri", "").replace(" ", "%20")
                    if encode_spaces
                    else t.get("uri", "")
                )
                loved.append(
                    {
                        "trackName": t.get("name", "Unknown"),
                        "artistName": ", ".join(
                            [a.get("name", "Unknown") for a in t.get("artists", [])]
                        ),
                        "albumName": t.get("album", {}).get("name", "Unknown"),
                        "trackUri_spotify": uri,
                    }
                )
            if not resp.get("next"):
                break
            offset += page_limit
        return {"lovedTracks": loved}

    def _save(self, data: Dict[str, Any], path: str) -> None:
        try:
            safe_path = sanitize_filename(path)
            Path(safe_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("Saved data to %s", safe_path)
        except Exception as e:
            logger.error("Failed writing JSON to %s: %s", path, e)

import base64
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

import requests

__all__ = [
    "TidalIntegration",
    "sanitize_filename",
    "extract_playlist_id",
]

# -------------------- Base URLs & Timeouts --------------------
_OAUTH_AUTHORIZE_URL = "https://login.tidal.com/authorize"
_OAUTH_TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
_API_BASE = "https://openapi.tidal.com/v2"

_MAX_RETRIES = 2
_BASE_BACKOFF = 15.0
_MAX_BACKOFF = 600.0
_REQUEST_TIMEOUT = 20.0


# -------------------- Logging Configuration --------------------
def configure_logging(
    name=__name__, level=logging.INFO, log_file: Optional[str] = None
):
    logger = logging.getLogger(name)
    if logger.handlers:
        return
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if log_file:
        from logging.handlers import RotatingFileHandler

        fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3)
        fh.setFormatter(fmt)
        logger.addHandler(fh)


configure_logging(log_file="tidal_integration.log")
logger = logging.getLogger(__name__)


# -------------------- Utility Functions --------------------
def extract_playlist_id(uri_or_url: str) -> str:
    """
    Extracts a numeric TIDAL playlist ID from either a playlist URI
    (e.g. "tidal:playlist:123456") or a URL (e.g. "https://tidal.com/browse/playlist/123456").
    """
    m = re.search(r"(?:playlist[/:])([0-9A-Za-z\-]+)", uri_or_url)
    if not m:
        raise ValueError(f"Invalid TIDAL playlist URL or URI: {uri_or_url}")
    return m.group(1)


def sanitize_filename(name: str) -> str:
    """
    Sanitize a filename or playlist name by replacing invalid chars, removing dots, collapsing spaces.
    """
    cleaned = re.sub(r"[^\w\- ]", " ", name)
    cleaned = re.sub(r" +", " ", cleaned)
    return cleaned.strip()


def _clean_for_search(text: str) -> str:
    no_accents = _strip_accents(text)
    cleaned = re.sub(r"[^\w\s\-]", " ", no_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(
        ch for ch in normalized if not unicodedata.category(ch).startswith("M")
    )


def _clean_for_search(text: str) -> str:
    no_accents = _strip_accents(text)
    cleaned = re.sub(r"[^\w\s\-]", " ", no_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def generate_pkce_pair(length: int = 64) -> (str, str):
    code_verifier = (
        base64.urlsafe_b64encode(os.urandom(length)).rstrip(b"=").decode("utf-8")
    )
    sha256_digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = (
        base64.urlsafe_b64encode(sha256_digest).rstrip(b"=").decode("utf-8")
    )
    return code_verifier, code_challenge


# -------------------- Local HTTP Callback Server for PKCE --------------------
class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/callback":
            code = qs.get("code", [None])[0]
            state = qs.get("state", [None])[0]
            self.server.auth_code = code
            self.server.auth_state = state
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authorization received. You may close this window.</h1></body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format_str, *args):
        logger.info("HTTP Server: " + format_str % args)


def _start_local_http_server(port: int = 8888) -> HTTPServer:
    server = HTTPServer(("localhost", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# -------------------- Core Integration Class with PKCE --------------------
class TidalIntegration:
    def __init__(
        self,
        client_id: str,
        redirect_uri: Optional[str] = None,
        client_secret: Optional[str] = None,
        personal_access_token: Optional[str] = None,
        cache_file: Optional[str] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.personal_access_token = personal_access_token
        self.redirect_uri = redirect_uri.rstrip("/") if redirect_uri else None
        self.cache_file = Path(cache_file) if cache_file else None

        # In-memory token data
        self._token_data: Dict[str, Any] = {}
        self._pkce_verifier: Optional[str] = None
        self._auth_in_progress = False

        # A requests.Session for all API calls
        self._http = requests.Session()
        self._http.headers.update(
            {
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            }
        )

        # Kick off loading/refreshing or PKCE
        self._ensure_token()

    def _load_cache(self):
        if self.cache_file and self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text("utf-8"))
                self._token_data = data
            except Exception:
                self._token_data = {}

    def _save_cache(self):
        if self.cache_file:
            try:
                data_to_save = self._token_data.copy()
                self.cache_file.write_text(
                    json.dumps(data_to_save, indent=2), encoding="utf-8"
                )
            except Exception as e:
                logger.error("Failed to write cache file: %s", e)

    def _has_valid_token(self) -> bool:
        token = self._token_data.get("access_token")
        expiry = self._token_data.get("expires_in")
        ts = self._token_data.get("timestamp")
        if not token or not expiry or not ts:
            return False
        return time.time() < (ts + expiry - 60)

    def _refresh_access_token(self) -> bool:
        refresh_token = self._token_data.get("refresh_token")
        if not refresh_token:
            return False

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        resp = requests.post(
            _OAUTH_TOKEN_URL, data=payload, headers=headers, timeout=_REQUEST_TIMEOUT
        )
        if resp.status_code != 200:
            return False

        data = resp.json()
        self._token_data["access_token"] = data["access_token"]
        self._token_data["refresh_token"] = data.get("refresh_token", refresh_token)
        self._token_data["expires_in"] = data["expires_in"]
        self._token_data["scope"] = data.get("scope", self._token_data.get("scope"))
        self._token_data["timestamp"] = int(time.time())
        self._save_cache()
        logger.info("Refreshed TIDAL access token")
        return True

    def _ensure_token(self):
        # 1) If user provided a PAT, just store it
        if self.personal_access_token:
            self._token_data = {
                "access_token": self.personal_access_token,
                "expires_in": float("inf"),
                "refresh_token": None,
                "scope": None,
                "timestamp": time.time(),
            }
            return

        # 2) If client_secret present, do client‐credentials
        if self.client_secret:
            resp = requests.post(
                _OAUTH_TOKEN_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            data["expires_in"] = data.get("expires_in", 0)
            data["timestamp"] = int(time.time())
            self._token_data = data
            return

        # 3) PKCE flow: try cache -> refresh -> full flow
        self._load_cache()
        if self._has_valid_token():
            return

        if self._token_data.get("refresh_token"):
            try:
                if self._refresh_access_token():
                    return
            except:
                self._token_data = {}

        logger.info(
            "Starting TIDAL PKCE Authorization Flow. Please authorize in your browser."
        )
        self._auth_in_progress = True
        self._run_pkce_flow()
        self._auth_in_progress = False

    def _run_pkce_flow(self):
        if not self.redirect_uri:
            raise RuntimeError("redirect_uri must be provided for PKCE flow")

        # 1) Generate PKCE
        code_verifier, code_challenge = generate_pkce_pair()
        self._pkce_verifier = code_verifier

        # 2) Start local HTTP server to catch /callback
        parsed = urllib.parse.urlparse(self.redirect_uri)
        port = parsed.port or 80
        server = _start_local_http_server(port=port)
        logger.info(f"-> Listening on {self.redirect_uri} for OAuth callback...")

        # 3) Build /authorize URL
        scopes = (
            "user.read "
            "collection.read "
            "search.read "
            "playlists.write "
            "playlists.read "
            "entitlements.read "
            "collection.write "
            "playback "
            "recommendations.read "
            "search.write"
        )
        state = os.urandom(16).hex()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scopes,
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
            "state": state,
        }
        query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        authorize_url = _OAUTH_AUTHORIZE_URL + "?" + query_string
        logger.info("-> Open this URL in your browser to authorize:")
        logger.info("   %s", authorize_url)
        webbrowser.open(authorize_url, new=1)

        # 4) Wait for callback
        start_time = time.time()
        while not hasattr(server, "auth_code"):
            if time.time() - start_time > 300:
                server.shutdown()
                raise TimeoutError("Timed out waiting for OAuth callback.")
            time.sleep(0.5)

        auth_code = server.auth_code
        returned_state = server.auth_state
        server.shutdown()
        logger.info(f"-> Received authorization code: {auth_code}")
        logger.info(f"-> Returned state:            {returned_state}")

        # 5) Verify state
        if returned_state != state:
            raise RuntimeError("State mismatch: potential CSRF attack")

        # 6) Exchange code for tokens
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": auth_code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": self._pkce_verifier,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        resp = requests.post(
            _OAUTH_TOKEN_URL, data=payload, headers=headers, timeout=_REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        token_data = resp.json()

        # 7) Store tokens & cache
        self._token_data["access_token"] = token_data["access_token"]
        self._token_data["refresh_token"] = token_data.get("refresh_token")
        self._token_data["expires_in"] = token_data["expires_in"]
        self._token_data["scope"] = token_data.get("scope")
        self._token_data["timestamp"] = int(time.time())
        self._save_cache()
        logger.info("✅ Authorization complete; tokens saved to cache.")

    def _auth_headers(self) -> Dict[str, str]:
        if not self._has_valid_token():
            if not self._refresh_access_token():
                self._ensure_token()
        token = self._token_data.get("access_token")
        if not token:
            raise RuntimeError("No TIDAL access token available.")
        return {"Authorization": f"Bearer {token}"}

    def _compute_rate_limit_delay(self, resp: requests.Response) -> float:
        try:
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 0))
            requested = int(resp.headers.get("X-RateLimit-Requested-Tokens", 1))
            replenish = float(resp.headers.get("X-RateLimit-Replenish-Rate", 0.0))
            if requested > remaining and replenish > 0:
                shortage = requested - remaining
                return shortage / replenish
            return float(resp.headers.get("Retry-After", 0))
        except Exception:
            return 0

    def _request_with_retries(
        self, method: str, url: str, **kwargs
    ) -> requests.Response:
        max_total_wait = 600  # Increase total allowed wait time
        total_waited = 0
        for attempt in range(1, _MAX_RETRIES + 2):
            extra_headers = kwargs.pop("headers", {})
            hdrs = {**self._http.headers, **self._auth_headers(), **extra_headers}
            try:
                resp = self._http.request(
                    method, url, headers=hdrs, timeout=_REQUEST_TIMEOUT, **kwargs
                )
            except requests.RequestException as e:
                logger.warning(f"TIDAL HTTP error (attempt {attempt}): {e}")
                if attempt == _MAX_RETRIES + 1:
                    raise
                wait = min(_BASE_BACKOFF * (2 ** (attempt - 1)), _MAX_BACKOFF)
                jitter = random.uniform(0.5, 1.5)
                wait *= jitter
                logger.info(f"Retrying after {wait:.2f}s...")
                time.sleep(wait)
                total_waited += wait
                continue

            if resp.status_code == 401 and self._token_data.get("refresh_token"):
                logger.warning("TIDAL 401: refreshing token (attempt %d)", attempt)
                self._refresh_access_token()
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = float(retry_after)
                else:
                    wait = self._compute_rate_limit_delay(resp)
                    if wait <= 0:
                        wait = min(_BASE_BACKOFF * (2 ** (attempt - 1)), _MAX_BACKOFF)
                jitter = random.uniform(0.8, 1.5)
                wait *= jitter
                logger.warning(
                    "TIDAL rate limit hit; backing off for %.2f s (attempt %d/%d)",
                    wait,
                    attempt,
                    _MAX_RETRIES + 1,
                )
                time.sleep(wait)
                total_waited += wait
                if total_waited > max_total_wait:
                    raise RuntimeError("Aborting after too many rate-limit waits.")
                continue

            if 500 <= resp.status_code < 600:
                wait = min(_MAX_BACKOFF, _BASE_BACKOFF * (2 ** (attempt - 1)))
                jitter = random.uniform(0.8, 1.5)
                wait *= jitter
                logger.warning(
                    "TIDAL server error %s; retrying in %.2f s (attempt %d/%d)",
                    resp.status_code,
                    wait,
                    attempt,
                    _MAX_RETRIES + 1,
                )
                time.sleep(wait)
                total_waited += wait
                continue

            resp.raise_for_status()
            return resp

        logger.error("Exhausted all retries for TIDAL request: %s %s", method, url)
        resp.raise_for_status()
        return resp  # type: ignore

    def _search_track(self, title: str, artist: str) -> Optional[str]:
        clean_title = _clean_for_search(title)
        clean_artist = _clean_for_search(artist)
        q = requests.utils.quote(f"{clean_title} {clean_artist}")
        url = (
            f"{_API_BASE}/searchResults/{q}"
            "?countryCode=US&include=tracks&explicitFilter=exclude&page[size]=1"
        )
        resp = self._request_with_retries("GET", url)
        doc = resp.json()
        tracks = (
            doc.get("data", {})
            .get("relationships", {})
            .get("tracks", {})
            .get("data", [])
        )
        return str(tracks[0]["id"]) if tracks else None

    def fetch_user_playlists_with_tracks(
        self, resolve_artist: bool = False
    ) -> Dict[str, Any]:
        """
        Fetches every playlist for the authenticated user, including all track metadata:
          - trackName
          - artistName
          - albumName
          - trackUri_tidal (TIDAL ID)

        Steps:
          1) GET /users/me?countryCode=US -> retrieve current user_id
          2) Page through GET /playlists?filter[r.owners.id]=<user_id>&countryCode=US&limit=50&offset=…
             to get each page of playlists owned by that user
          3) For each playlist_id, page through GET /playlists/{playlist_id}/relationships/items?countryCode=US
             to retrieve full track objects in batches
          4) Build and return a dict of the form:
             {
               "playlists": [
                 {
                   "id": "<playlist_id>",
                   "name": "<playlist_name>",
                   "tracks": [
                     {
                       "trackName": "...",
                       "artistName": "...",
                       "albumName": "...",
                       "trackUri_tidal": "<tidal_id>"
                     },
                     …
                   ]
                 },
                 …
               ]
             }
        """
        # ─── 1) Get current user ID ───────────────────────────────────────────────
        resp_me = self._request_with_retries("GET", f"{_API_BASE}/users/me")
        me_data = resp_me.json().get("data", {})
        logger.info(f"Fetched user data: {me_data}")
        user_id = me_data.get("id")
        if not user_id:
            raise RuntimeError("Unable to fetch user ID from /users/me")
        country_code = me_data.get("attributes", {}).get("countryCode", "US")

        # ─── 2) Page through all playlists owned by this user ───────────────────
        all_playlists = []
        limit_pl = 50
        offset_pl = 0

        while True:
            url_pl = (
                f"{_API_BASE}/playlists"
                f"?filter[r.owners.id]={user_id}"
                f"&countryCode={country_code}"
                f"&limit={limit_pl}"
                f"&offset={offset_pl}"
            )
            resp_pl = self._request_with_retries("GET", url_pl)
            playlists_page = resp_pl.json().get("data", [])
            if not playlists_page:
                break

            for pl in playlists_page:
                pl_id = pl.get("id")
                pl_name = pl.get("attributes", {}).get("name", "Unknown")

                # ─── 3) Fetch every track in this playlist in pages ───────────────
                tracks_list = []
                limit_tr = 100
                offset_tr = 0

                while True:
                    url_items = (
                        f"{_API_BASE}/playlists/{pl_id}/relationships/items"
                        f"?countryCode={country_code}"
                    )
                    resp_items = self._request_with_retries("GET", url_items)
                    resp_json = resp_items.json()
                    items_page = resp_json.get("data", [])
                    included = resp_json.get("included", [])

                    # Build a lookup for included tracks by id
                    track_lookup = {
                        track["id"]: track
                        for track in included
                        if track["type"] == "tracks"
                    }

                    if not items_page:
                        break

                    for item in items_page:
                        track_id = item.get("id")
                        track_obj = track_lookup.get(track_id, {})
                        track_attrs = track_obj.get("attributes", {})

                        track_name = track_attrs.get("title", "Unknown")

                        # Extract first artist’s name (if present)
                        artists_rel = (
                            track_obj.get("relationships", {})
                            .get("artists", {})
                            .get("data", [])
                        )
                        if artists_rel:
                            artist_name = (
                                artists_rel[0]
                                .get("attributes", {})
                                .get("name", "Unknown")
                            )
                        else:
                            artist_name = "Unknown"

                        # Optionally resolve artist details
                        if resolve_artist and artists_rel:
                            try:
                                url_artist = (
                                    f"{_API_BASE}/tracks/{track_id}/relationships/artists"
                                    f"?countryCode={country_code}"
                                )
                                resp_artist = self._request_with_retries(
                                    "GET", url_artist
                                )
                                artist_data = resp_artist.json().get("data", [])
                                if artist_data:
                                    artist_name = (
                                        artist_data[0]
                                        .get("attributes", {})
                                        .get("name", artist_name)
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to resolve artist for track {track_id}: {e}"
                                )

                        # Extract first album’s title (if present)
                        albums_rel = (
                            track_obj.get("relationships", {})
                            .get("albums", {})
                            .get("data", [])
                        )
                        if albums_rel:
                            album_name = (
                                albums_rel[0]
                                .get("attributes", {})
                                .get("title", "Unknown")
                            )
                        else:
                            album_name = "Unknown"

                        track_entry = {
                            "trackName": track_name,
                            "artistName": artist_name,
                            "albumName": album_name,
                            "trackUri_tidal": str(track_id) if track_id else None,
                        }
                        tracks_list.append(track_entry)

                    if len(items_page) < limit_tr:
                        break

                    offset_tr += limit_tr
                    time.sleep(0.2)  # small pause to avoid hitting rate limits

                # Add this playlist and its collected tracks to the result list
                all_playlists.append(
                    {
                        "id": pl_id,
                        "name": pl_name,
                        "tracks": tracks_list,
                    }
                )

            if len(playlists_page) < limit_pl:
                break

            offset_pl += limit_pl
            time.sleep(0.2)  # small pause before fetching next page of playlists

        return {"playlists": all_playlists}

    def create_tidal_playlist(
        self,
        name: str,
        description: str = "",
        privacy: str = "PRIVATE",
        country_code: str = "DE",
    ) -> Optional[str]:
        """
        Create a new TIDAL playlist and return its playlist ID.
        """
        url = f"{_API_BASE}/playlists?countryCode={country_code}"
        payload = {
            "data": {
                "type": "playlists",
                "attributes": {
                    "name": name,
                    "description": description,
                    "privacy": privacy,
                },
            }
        }
        headers = {
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
        }
        try:
            resp = self._request_with_retries(
                "POST", url, json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            playlist_id = data.get("data", {}).get("id")
            logger.info(f"Created TIDAL playlist '{name}' with id: {playlist_id}")
            return playlist_id
        except Exception as e:
            logger.error(f"Failed to create TIDAL playlist '{name}': {e}")
            return None

    def _add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: list[str],
        country_code: str = "US",
    ) -> None:
        """
        Add a list of track IDs to a playlist in batches of up to 50.

        Parameters:
        - playlist_id:   The TIDAL playlist UUID (string).
        - track_ids:     A list of TIDAL track IDs (strings).
        - country_code:  Two-letter ISO code, e.g. "US".
        """
        batch_size = 20
        for i in range(0, len(track_ids), batch_size):
            batch_chunk = track_ids[i : i + batch_size]

            url = (
                f"{_API_BASE}/playlists/{playlist_id}/relationships/items"
                f"?countryCode={country_code}"
            )

            # Each item in "data" must have {"type":"tracks", "id": "<track_id>"}
            payload = {
                "data": [
                    {"type": "tracks", "id": str(track_id)} for track_id in batch_chunk
                ],
                # "meta": {"positionBefore": "0"}  # optional: insert at front when needed
            }

            headers = {
                **self._auth_headers(),
                "Content-Type": "application/vnd.api+json",
            }

            resp = requests.post(
                url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                logger.error(
                    "Failed to add batch %d–%d to playlist %s: %s",
                    i,
                    i + len(batch_chunk) - 1,
                    playlist_id,
                    resp.text,
                )
                raise

            logger.info(
                "Added %d tracks (indices %d–%d) to playlist %s",
                len(batch_chunk),
                i,
                i + len(batch_chunk) - 1,
                playlist_id,
            )
            time.sleep(0.2)  # be gentle on the API

    def augment_spotify_with_tidal_ids(
        self,
        spotify_json_path: str,
        output_path: str,
        country_code: str = "DE",
    ):
        """
        For each track in the Spotify JSON, add a Tidal ID if missing.
        If a track with the same Spotify ID in another playlist already has a Tidal ID, reuse it.
        Otherwise, search Tidal using the searchSuggestions endpoint.
        Progress is saved to a .part file after each search to allow resuming.
        Adds a 'tidal_search_level' field to each track indicating which search level matched.
        """
        part_path = output_path + ".part"

        # Load from .part file if it exists, else from original
        if os.path.exists(part_path):
            logger.info(f"Resuming from partial file: {part_path}")
            with open(part_path, "r", encoding="utf-8") as f:
                spotify_data = json.load(f)
        else:
            with open(spotify_json_path, "r", encoding="utf-8") as f:
                spotify_data = json.load(f)

        # Build a lookup for already-found Tidal IDs by Spotify track ID
        spotify_to_tidal = {}

        # First pass: collect all known mappings
        for playlist in spotify_data.get("playlists", []):
            playlist["name"] = sanitize_filename(
                playlist.get("name", "Untitled Playlist")
            )
            for track in playlist.get("tracks", []):
                spotify_id = track.get("trackUri_spotify") or track.get("spotify_id")
                tidal_id = track.get("trackUri_tidal")
                if spotify_id and tidal_id:
                    spotify_to_tidal[spotify_id] = tidal_id

        # Second pass: fill in missing Tidal IDs, reusing or searching as needed
        for playlist in spotify_data.get("playlists", []):
            for track in playlist.get("tracks", []):
                spotify_id = track.get("trackUri_spotify") or track.get("spotify_id")
                # Only search if trackUri_tidal is missing or null
                if not spotify_id or (
                    "trackUri_tidal" in track and track["trackUri_tidal"]
                ):
                    continue

                # Reuse if already found
                tidal_id = spotify_to_tidal.get(spotify_id)
                if tidal_id:
                    track["trackUri_tidal"] = tidal_id
                    track["tidal_search_level"] = "reused"
                    # Save progress immediately
                    with open(part_path, "w", encoding="utf-8") as f:
                        json.dump(spotify_data, f, indent=2, ensure_ascii=False)
                    continue

                # Otherwise, search Tidal with progressive simplification
                title = track.get("trackName", "")
                artist = track.get("artistName", "")

                def strip_accents(text):
                    return "".join(
                        ch
                        for ch in unicodedata.normalize("NFKD", text)
                        if not unicodedata.category(ch).startswith("M")
                    )

                def simplify_query(title, artist, level=0):
                    # Level 0: full (accents stripped, slashes replaced)
                    t = strip_accents(title).replace("/", " ")
                    a = strip_accents(artist).replace("/", " ")
                    if level == 0:
                        return f"{t} {a}".strip()
                    # Level 1: remove parentheses and their contents
                    t = re.sub(r"\(.*?\)", "", t)
                    # Level 2: remove after colon or dash
                    if level >= 2:
                        t = re.split(r"[:\-]", t)[0]
                    # Level 3: just title and first artist
                    if level == 3:
                        a = a.split(",")[0]
                    # Level 4: just title
                    if level == 4:
                        return t.strip()
                    return f"{t} {a}".strip()

                max_levels = 5
                found = False
                for level in range(max_levels):
                    query = simplify_query(title, artist, level)
                    query = re.sub(r'[":\[\]\(\)\']', " ", query)
                    query = re.sub(r"\s+", " ", query).strip()
                    query = query[:100]
                    encoded_query = urllib.parse.quote(query)
                    url = (
                        f"https://openapi.tidal.com/v2/searchSuggestions/"
                        f"{encoded_query}/relationships/directHits"
                        f"?countryCode={country_code}&explicitFilter=include%2C%20exclude&include=directHits"
                    )
                    logger.info(
                        f"Searching Tidal (level {level}) for: '{query}' (original: '{title}' by '{artist}')"
                    )
                    try:
                        resp = self._request_with_retries("GET", url)
                        results = resp.json()
                        if isinstance(results, list):
                            logger.warning(
                                f"Unexpected list response for '{title}' by '{artist}': {results}"
                            )
                            track["trackUri_tidal"] = None
                            track["tidal_search_level"] = None
                            break
                        included = results.get("included", [])
                        tidal_track = next(
                            (item for item in included if item.get("type") == "tracks"),
                            None,
                        )
                        if tidal_track and "id" in tidal_track:
                            tidal_id = str(tidal_track["id"])
                            track["trackUri_tidal"] = tidal_id
                            track["tidal_search_level"] = level
                            spotify_to_tidal[spotify_id] = tidal_id
                            found = True
                            break
                        else:
                            track["trackUri_tidal"] = None
                            track["tidal_search_level"] = None
                    except Exception as e:
                        logger.warning(
                            f"Failed to search Tidal (level {level}) for '{title}' by '{artist}': {e}"
                        )
                        track["trackUri_tidal"] = None
                        track["tidal_search_level"] = None

                # Save progress immediately after each search attempt
                with open(part_path, "w", encoding="utf-8") as f:
                    json.dump(spotify_data, f, indent=2, ensure_ascii=False)

        # Finalize: move .part to output_path
        os.replace(part_path, output_path)
        logger.info(f"Augmented playlists written to {output_path}")

    def find_playlist_by_name(
        self, name: str, country_code: str = "US"
    ) -> Optional[str]:
        """
        Search the user's TIDAL playlists for one matching the given name.
        Return its ID if found, else None.
        """
        try:
            resp_me = self._request_with_retries("GET", f"{_API_BASE}/users/me")
            user_id = resp_me.json().get("data", {}).get("id")
            if not user_id:
                return None

            offset = 0
            limit = 50
            while True:
                url = (
                    f"{_API_BASE}/playlists"
                    f"?filter[r.owners.id]={user_id}"
                    f"&countryCode={country_code}"
                    f"&limit={limit}&offset={offset}"
                )
                resp = self._request_with_retries("GET", url)
                playlists = resp.json().get("data", [])
                if not playlists:
                    break

                for pl in playlists:
                    pl_name = pl.get("attributes", {}).get("name", "")
                    if sanitize_filename(pl_name) == sanitize_filename(name):
                        return pl.get("id")

                if len(playlists) < limit:
                    break
                offset += limit
        except Exception as e:
            logger.warning(f"Error searching for playlist '{name}': {e}")
        return None

    def _add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: list[str],
        country_code: str = "US",
    ) -> None:
        """
        Add track IDs to a playlist in batches of up to 20, with retry on rate limit.
        """
        batch_size = 20
        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            url = (
                f"{_API_BASE}/playlists/{playlist_id}/relationships/items"
                f"?countryCode={country_code}"
            )
            payload = {"data": [{"type": "tracks", "id": str(tid)} for tid in batch]}
            headers = {
                **self._auth_headers(),
                "Content-Type": "application/vnd.api+json",
            }

            retries = 3
            for attempt in range(1, retries + 1):
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT
                )
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    wait = float(ra) if ra else _BASE_BACKOFF
                    wait *= random.uniform(0.8, 1.5)
                    logger.warning(
                        f"Rate limited adding tracks; retry in {wait:.1f}s (#{attempt})"
                    )
                    time.sleep(wait)
                    continue
                try:
                    resp.raise_for_status()
                    logger.info(
                        f"Added {len(batch)} tracks ({i}-{i+len(batch)-1}) to {playlist_id}"
                    )
                    break
                except requests.HTTPError:
                    logger.error(f"Failed batch {i}-{i+len(batch)-1}: {resp.text}")
                    if attempt == retries:
                        raise
                    time.sleep(_BASE_BACKOFF)
            time.sleep(1.0)

    def get_playlist_track_ids(
        self,
        playlist_id: str,
        country_code: str = "US",
    ) -> set[str]:
        """
        Retrieve all track IDs currently in a TIDAL playlist to avoid duplicates.
        Reads each playlist-item relationship and extracts the track ID.
        """
        existing_ids: set[str] = set()
        limit = 100
        offset = 0
        while True:
            url = (
                f"{_API_BASE}/playlists/{playlist_id}/relationships/items"
                f"?countryCode={country_code}&limit={limit}&offset={offset}"
            )
            resp_json = self._request_with_retries("GET", url).json()
            items = resp_json.get("data", [])
            if not items:
                break

            for item in items:
                # Each item has a relationship to the track itself
                track_rel = item.get("relationships", {}).get("track", {}).get("data")
                if track_rel and "id" in track_rel:
                    existing_ids.add(str(track_rel["id"]))

            if len(items) < limit:
                break
            offset += limit
        return existing_ids

    def add_playlists_and_tracks_from_json(
        self,
        input_json_path: str,
        country_code: str = "US",
    ):
        """
        For each playlist in the JSON:
          1) Use existing `tidal_id` or search/create as needed.
          2) Fetch existing track IDs, filter out duplicates.
          3) Add only new tracks, annotate JSON, and persist file.
        """
        with open(input_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for pl in data.get("playlists", []):
            name = sanitize_filename(pl.get("name", "Untitled Playlist"))
            tidal_id = pl.get("tidal_id")

            # Determine playlist ID
            if not tidal_id:
                tidal_id = self.find_playlist_by_name(name, country_code)
                if not tidal_id:
                    tidal_id = self.create_tidal_playlist(
                        name=name,
                        description="Migrated from JSON",
                        privacy="PRIVATE",
                        country_code=country_code,
                    )
                    if not tidal_id:
                        logger.warning(
                            f"Skipping '{name}' — could not obtain playlist ID."
                        )
                        continue
                    logger.info(f"Created playlist '{name}' -> {tidal_id}")
                else:
                    logger.info(f"Found playlist '{name}' -> {tidal_id}")
                pl["tidal_id"] = tidal_id

            # Load track URIs from JSON
            desired_ids = [
                t["trackUri_tidal"]
                for t in pl.get("tracks", [])
                if t.get("trackUri_tidal")
            ]

            # Filter out already added tracks
            existing_ids = self.get_playlist_track_ids(tidal_id, country_code)
            new_ids = [tid for tid in desired_ids if tid not in existing_ids]
            if not new_ids:
                logger.info(f"No new tracks to add for '{name}', skipping.")
                continue

            # Add only new tracks
            try:
                self._add_tracks_to_playlist(
                    playlist_id=tidal_id,
                    track_ids=new_ids,
                    country_code=country_code,
                )
            except Exception as e:
                logger.error(f"Error adding new tracks to '{name}': {e}")

            time.sleep(2.0)

        # Persist updates to JSON (including new tidal_id fields)
        with open(input_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

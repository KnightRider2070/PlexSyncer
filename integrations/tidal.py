import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

__all__ = ["extract_playlist_id", "sanitize_filename", "TidalIntegration"]

# Base URLs
_OAUTH_BASE = "https://auth.tidal.com/v1"
_API_BASE = "https://openapi.tidal.com/v2"

# Timeouts & retry settings
_MAX_RETRIES = 2
_BASE_BACKOFF = 6.0  # base delay in seconds
_MAX_BACKOFF = 60.0  # maximum delay cap
_REQUEST_TIMEOUT = 10.0

# ---------- Logging Configuration ----------


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

# ---------- Utility Functions ----------


def extract_playlist_id(url: str) -> str:
    m = re.search(r"(?:playlist[/:])(\d+)", url)
    if not m:
        raise ValueError(f"Invalid TIDAL playlist URL or URI: {url}")
    return m.group(1)


def sanitize_filename(name: str) -> str:
    """
    Sanitize a filename by:
      1) Replacing any character that is not a letter, digit, underscore,
         hyphen, or space with a single space.
      2) Removing dots entirely (they fall under “not allowed”).
      3) Collapsing consecutive spaces into one.
      4) Stripping leading/trailing spaces.
    """
    # 1) Replace invalid characters (including dots) with space
    cleaned = re.sub(r"[^\w\- ]", " ", name)
    # 2) Collapse multiple spaces to a single space
    cleaned = re.sub(r" +", " ", cleaned)
    # 3) Trim leading/trailing spaces
    return cleaned.strip()


# ---------- Core Integration Class ----------


class TidalIntegration:
    """
    - OAuth 2.0 Client Credentials or Device Flow login (runs once)
    - Personal-access-token supported
    - Search via authenticated JSON:API
    - Playlist creation & track adding
    """

    def __init__(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        personal_access_token: Optional[str] = None,
        cache_file: Optional[str] = None,
    ):
        """
        :param client_id:             Your TIDAL OAuth2 client_id
        :param client_secret:         Your TIDAL OAuth2 client_secret (for client-credentials flow)
        :param personal_access_token: If given, skips login and uses this Bearer token
        :param cache_file:            Path to JSON file for caching tokens
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.personal_access_token = personal_access_token
        self.cache_file = Path(cache_file) if cache_file else None
        self._token_data: Dict[str, Any] = {}
        self._auth_attempted = False
        self._http = requests.Session()
        self._http.headers.update(
            {
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            }
        )
        try:
            self._ensure_token()
        except Exception as e:
            logger.warning("Initial auth failed: %s", e)

    def _load_cache(self):
        if self.cache_file and self.cache_file.exists():
            self._token_data = json.loads(self.cache_file.read_text())

    def _save_cache(self):
        if self.cache_file:
            self.cache_file.write_text(json.dumps(self._token_data, indent=2))

    def _device_authorization(self) -> Dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"client_id": self.client_id, "scope": "r_usr w_usr w_sub"}
        resp = requests.post(
            f"{_OAUTH_BASE}/oauth2/device_authorization",
            headers=headers,
            data=data,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _poll_token(
        self, device_code: str, interval: float, expires_in: float
    ) -> Dict[str, Any]:
        deadline = time.time() + expires_in
        while time.time() < deadline:
            resp = self._http.post(
                f"{_OAUTH_BASE}/oauth2/token",
                headers={"Accept": "application/json"},
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": self.client_id,
                },
                timeout=_REQUEST_TIMEOUT,
            )
            data = resp.json()
            if resp.ok:
                data["expiry_ts"] = time.time() + data.get("expires_in", 0)
                return data
            if data.get("error") == "authorization_pending":
                time.sleep(interval)
                continue
            resp.raise_for_status()
        raise TimeoutError("Device code login expired.")

    def _refresh_token(self):
        rt = self._token_data.get("refresh_token")
        if not rt:
            raise RuntimeError("No refresh_token available for refresh")
        resp = self._http.post(
            f"{_OAUTH_BASE}/oauth2/token",
            headers={"Accept": "application/json"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": self.client_id,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        data["expiry_ts"] = time.time() + data.get("expires_in", 0)
        self._token_data = data
        self._save_cache()
        logger.info("Refreshed access token via refresh_token")

    def _ensure_token(self):
        if self._auth_attempted:
            return
        self._auth_attempted = True
        if self.personal_access_token:
            self._token_data = {
                "access_token": self.personal_access_token,
                "expiry_ts": float("inf"),
            }
            return
        if self.client_secret:
            resp = requests.post(
                f"{_OAUTH_BASE}/oauth2/token",
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
            data["expiry_ts"] = time.time() + data.get("expires_in", 0)
            self._token_data = data
            return
        self._load_cache()
        token = self._token_data.get("access_token")
        if token and time.time() <= self._token_data.get("expiry_ts", 0):
            return
        if token:
            try:
                self._refresh_token()
                return
            except Exception:
                self._token_data = {}
        logger.info("Starting device-code flow; visit the logged URL & authorize.")
        da = self._device_authorization()
        logger.info("Open and authorize here: %s", da["verification_uri_complete"])
        tok = self._poll_token(da["device_code"], da["interval"], da["expires_in"])
        self._token_data = tok
        self._save_cache()
        logger.info("Device flow complete; token cached.")

    def _auth_headers(self) -> Dict[str, str]:
        token = self._token_data.get("access_token")
        if not token:
            raise RuntimeError("Authentication required before making API calls.")
        return {"Authorization": f"Bearer {token}"}

    def _compute_rate_limit_delay(self, resp):
        try:
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 0))
            requested = int(resp.headers.get("X-RateLimit-Requested-Tokens", 1))
            replenish = float(resp.headers.get("X-RateLimit-Replenish-Rate", 0.0))
            if requested > remaining and replenish > 0:
                shortage = requested - remaining
                return shortage / replenish
            # Fallback to Retry-After header if available
            return float(resp.headers.get("Retry-After", 0))
        except Exception:
            return 0

    def _request_with_retries(
        self, method: str, url: str, **kwargs
    ) -> requests.Response:
        max_total_wait = 300  # total max time to wait on rate limits (sec)
        total_waited = 0

        for attempt in range(1, _MAX_RETRIES + 2):  # +1 for a final fail-fast try
            extra = kwargs.pop("headers", {})
            hdrs = {**self._http.headers, **self._auth_headers(), **extra}
            try:
                resp = self._http.request(
                    method, url, headers=hdrs, timeout=_REQUEST_TIMEOUT, **kwargs
                )
            except requests.RequestException as e:
                logger.warning(f"HTTP request failed (attempt {attempt}): {e}")
                if attempt == _MAX_RETRIES + 1:
                    raise
                wait = min(_BASE_BACKOFF * (2 ** (attempt - 1)), _MAX_BACKOFF)
                logger.info(f"Backing off for {wait:.2f} seconds before retrying.")
                time.sleep(wait)
                continue

            # Handle Unauthorized
            if resp.status_code == 401 and self._token_data.get("refresh_token"):
                logger.warning(
                    "401 Unauthorized; refreshing token (attempt %d)", attempt
                )
                self._refresh_token()
                continue

            # Handle Rate Limit
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    wait = float(retry_after)
                else:
                    wait = self._compute_rate_limit_delay(resp)
                    if wait <= 0:
                        wait = min(_BASE_BACKOFF * (2 ** (attempt - 1)), _MAX_BACKOFF)
                # Full jitter, but at least 1 second to avoid hammering
                jittered = random.uniform(1, wait)
                logger.warning(
                    "429 rate-limit hit. Backing off for %.2f s (attempt %d/%d)...",
                    jittered,
                    attempt,
                    _MAX_RETRIES + 1,
                )
                time.sleep(jittered)
                total_waited += jittered
                if total_waited > max_total_wait:
                    raise RuntimeError(
                        f"Aborting after too many rate-limits/waits (> {max_total_wait}s)."
                    )
                continue

            # Handle Server Errors with capped exponential backoff + jitter
            if 500 <= resp.status_code < 600:
                wait = min(_MAX_BACKOFF, _BASE_BACKOFF * (2 ** (attempt - 1)))
                jittered = random.uniform(1, wait)
                logger.warning(
                    "Server error %s; retrying in %.2f s (attempt %d/%d)",
                    resp.status_code,
                    jittered,
                    attempt,
                    _MAX_RETRIES + 1,
                )
                time.sleep(jittered)
                continue

            # Success or client error
            resp.raise_for_status()
            return resp

        # Final attempt – will raise if still failing
        logger.error("Exhausted all retries for %s %s", method, url)
        resp.raise_for_status()
        return resp

    def _search_track(self, title: str, artist: str) -> Optional[str]:
        """
        Uses authenticated JSON:API searchResults. Requires a valid access token.
        """
        q = requests.utils.quote(f"{title} {artist}")
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
        return tracks[0]["id"] if tracks else None

    def augment_with_tidal_ids(
        self, input_file: str, output_file: Optional[str] = None
    ):
        out = sanitize_filename(output_file or input_file)
        tmp = out + ".part"
        # Load existing partial or source data
        if Path(tmp).exists():
            try:
                data = json.loads(Path(tmp).read_text("utf-8"))
                logger.info(f"Resuming from partial file: {tmp}")
            except Exception:
                data = json.loads(Path(input_file).read_text("utf-8"))
        else:
            data = json.loads(Path(input_file).read_text("utf-8"))

        playlists = (
            [data.get("playlist")]
            if data.get("playlist")
            else data.get("playlists", [])
        )

        for pi, pl in enumerate(playlists):
            for ti, t in enumerate(pl.get("tracks", [])):
                tidal_id = t.get("trackUri_tidal", None)
                if tidal_id is not None and str(tidal_id).strip():
                    # Already has a non-None, non-empty value, skip searching
                    continue
                # Else, look up the track
                title, artist = t.get("trackName", ""), t.get("artistName", "")
                try:
                    t["trackUri_tidal"] = self._search_track(title, artist)
                except Exception as e:
                    logger.warning("Lookup failed for '%s %s': %s", title, artist, e)
                    t["trackUri_tidal"] = None
                # Save progress after each track
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception as err:
                    logger.error(f"Error writing temp file: {err}")
                # Show progress if desired
                logger.info(
                    "Processed %d/%d tracks in playlist %d/%d",
                    ti + 1,
                    len(pl.get("tracks", [])),
                    pi + 1,
                    len(playlists),
                )
                time.sleep(0.2)

        Path(tmp).replace(out)
        logger.info("Augmented JSON saved to %s", out)

    def create_playlists_from_json(self, spotify_json: str):
        doc = json.loads(Path(spotify_json).read_text("utf-8"))
        resp = self._request_with_retries("GET", f"{_API_BASE}/users/me")
        user_id = resp.json()["data"]["id"]
        pls = [doc["playlist"]] if "playlist" in doc else doc.get("playlists", [])
        for pl in pls:
            resp = self._request_with_retries(
                "POST",
                f"{_API_BASE}/users/{user_id}/playlists",
                headers={"Content-Type": "application/vnd.api+json"},
                json={
                    "data": {"type": "playlists", "attributes": {"title": pl["name"]}}
                },
            )
            pl_id = resp.json()["data"]["id"]
            logger.info("Created playlist '%s' (id=%s)", pl["name"], pl_id)
            tids = [
                t["trackUri_tidal"]
                for t in pl.get("tracks", [])
                if t.get("trackUri_tidal")
            ]
            for i in range(0, len(tids), 100):
                batch = tids[i : i + 100]
                self._request_with_retries(
                    "POST",
                    f"{_API_BASE}/playlists/{pl_id}/tracks",
                    headers={"Content-Type": "application/vnd.api+json"},
                    json={"data": [{"type": "tracks", "id": tid} for tid in batch]},
                )
                logger.info("  + Added %d tracks to '%s'", len(batch), pl["name"])


# ------------------------------------------
# Example usage:
# ti = TidalIntegration(client_id="YOUR_CLIENT_ID", cache_file="tidal_session.json")
# ti.augment_with_tidal_ids("spotify_export.json", "augmented.json")
# ti.create_playlists_from_json("augmented.json")

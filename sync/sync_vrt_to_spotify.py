#!/usr/bin/env python3
"""
VRT to Spotify Sync Service
Syncs VRT "De Jaren Nul" playlist to a Spotify Connect device via the Spotify Web API.
No audio streaming infrastructure needed — the device plays natively via Spotify Connect.
"""

import base64
import json
import os
import re
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Set
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

from sheets_logger import create_logger as create_sheets_logger


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)

load_dotenv()

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN", "")
SPOTIFY_DEVICE_NAME   = os.getenv("SPOTIFY_DEVICE_NAME", "Keuken")
CONTROL_PORT          = int(os.getenv("CONTROL_PORT", "8877"))
CHECK_INTERVAL        = 120   # seconds between VRT syncs
STREAM_NAME           = "De Jaren Nul - 109"

# Song matching weights / thresholds
MATCH_ARTIST_WEIGHT = 0.7
MATCH_TITLE_WEIGHT  = 0.3
MATCH_THRESHOLD     = 0.7
MATCH_MAX_RESULTS   = 10

# VRT GraphQL
GRAPHQL_URL  = "https://www.vrt.be/vrtnu-api/graphql/public/v1"
COMPONENT_ID = "$byU4fHBsYXlsaXN0fHAlL2xpdmVzdHJlYW0vYXVkaW8vc3R1ZGlvLWJydXNzZWwtZGUtamFyZW4tbnVsLz90YWI9cGxheWxpc3Ql"

QUERY = """
query component($componentId: ID!, $lazyItemCount: Int = 100) {
  component(id: $componentId) {
    ... on ContainerNavigationItem {
      components {
        ... on PaginatedTileList {
          paginatedItems(first: $lazyItemCount) {
            edges {
              node {
                ... on SongTile {
                  title
                  description
                  startDate
                  active
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Shared state (read by HTTP control server thread)
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    spotify: Optional["SpotifyClient"] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    initial_uris: List[str] = field(default_factory=list)

state = AppState()


# ---------------------------------------------------------------------------
# Spotify Web API client
# ---------------------------------------------------------------------------

class SpotifyClient:

    API_BASE = "https://api.spotify.com/v1"
    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token: Optional[str] = None
        self.token_expiry: float = 0.0
        self._refresh_access_token()

    # --- token management ---------------------------------------------------

    def _refresh_access_token(self):
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        data = urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }).encode()
        req = Request(
            self.TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        self.access_token = body["access_token"]
        self.token_expiry = time.time() + body.get("expires_in", 3600) - 60
        print("🔑 Spotify token refreshed")

    def _ensure_token(self):
        if time.time() >= self.token_expiry:
            self._refresh_access_token()

    # --- low-level HTTP helpers ---------------------------------------------

    def _request(self, method: str, path: str, params: dict = None,
                 body: dict = None, retry: bool = True):
        self._ensure_token()
        url = f"{self.API_BASE}{path}"
        if params:
            url += "?" + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urlopen(req, timeout=10) as resp:
                raw = resp.read().strip()
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None  # 204 or non-JSON response
        except HTTPError as e:
            if e.code == 401 and retry:
                self._refresh_access_token()
                return self._request(method, path, params, body, retry=False)
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 5))
                print(f"⏳ Spotify rate limit — waiting {retry_after}s")
                time.sleep(retry_after)
                return self._request(method, path, params, body, retry=False)
            if e.code == 204:
                return None
            body_text = e.read().decode() if hasattr(e, 'read') else str(e)
            print(f"⚠️  Spotify API {method} {path} → HTTP {e.code}: {body_text[:200]}")
            return None

    def _get(self, path, params=None):
        return self._request("GET", path, params=params)

    def _put(self, path, params=None, body=None):
        return self._request("PUT", path, params=params, body=body or {})

    def _post(self, path, params=None, body=None):
        return self._request("POST", path, params=params, body=body or {})

    # --- player API ---------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for tracks. Returns list of track objects."""
        result = self._get("/search", params={"q": query, "type": "track", "limit": limit})
        if result:
            return result.get("tracks", {}).get("items", [])
        return []

    def get_devices(self) -> List[Dict]:
        result = self._get("/me/player/devices")
        return result.get("devices", []) if result else []

    def get_playback_state(self) -> Optional[Dict]:
        """Returns playback state or None if nothing is playing."""
        return self._get("/me/player")

    def transfer_playback(self, device_id: str, play: bool = False):
        """Transfer playback to device. play=True also starts playback."""
        self._put("/me/player", body={"device_ids": [device_id], "play": play})

    def start_playback(self, device_id: str, uris: List[str]):
        """Start playback of a list of track URIs on the given device."""
        self._put("/me/player/play",
                  params={"device_id": device_id},
                  body={"uris": uris})

    def pause_playback(self, device_id: str):
        """Pause playback."""
        self._put("/me/player/pause", params={"device_id": device_id})

    def add_to_queue(self, uri: str, device_id: str):
        """Add a single track URI to the playback queue."""
        self._post("/me/player/queue", params={"uri": uri, "device_id": device_id})


# ---------------------------------------------------------------------------
# VRT playlist fetching (unchanged from original)
# ---------------------------------------------------------------------------

def fetch_vrt_playlist() -> List[Dict]:
    headers = {
        "content-type": "application/json",
        "x-vrt-client-name": "WEB",
        "x-vrt-client-version": "1.5.15",
    }
    payload = json.dumps({
        "operationName": "component",
        "query": QUERY,
        "variables": {"componentId": COMPONENT_ID, "lazyItemCount": 30},
    }).encode()
    req = Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        components = (data.get("data", {})
                          .get("component", {})
                          .get("components", []))
        for component in components:
            if "paginatedItems" in component:
                songs = []
                for edge in component["paginatedItems"]["edges"]:
                    node = edge.get("node", {})
                    if node.get("title") and node.get("description"):
                        songs.append({
                            "title":      node.get("title", "").strip(),
                            "artist":     node.get("description", "").strip(),
                            "start_date": node.get("startDate", ""),
                            "active":     node.get("active", False),
                        })
                return songs
    except Exception as e:
        print(f"Error fetching VRT playlist: {e}")
    return []


# ---------------------------------------------------------------------------
# Song matching (unchanged from original)
# ---------------------------------------------------------------------------

def song_key(song: Dict) -> str:
    return f"{song['artist']}::{song['title']}::{song['start_date']}"


def normalize_artist_name(artist: str) -> str:
    if not artist:
        return ""
    artist = artist.lower()
    artist = re.sub(r'\([^)]*\)', '', artist)
    artist = re.sub(r'\[[^\]]*\]', '', artist)
    artist = re.sub(r'\b(feat\.?|ft\.?|featuring)\b.*', '', artist)
    artist = artist.replace('&', 'and').replace(' vs. ', ' vs ').replace(' vs ', ' ')
    artist = re.sub(r'[.,;]', '', artist)
    artist = re.sub(r'\s+', ' ', artist)
    return artist.strip()


def calculate_artist_similarity(artist1: str, artist2: str) -> float:
    norm1 = normalize_artist_name(artist1)
    norm2 = normalize_artist_name(artist2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 in norm2 or norm2 in norm1:
        return 0.85
    return SequenceMatcher(None, norm1, norm2).ratio()


def find_best_match(vrt_artist: str, vrt_title: str,
                    search_results: List[Dict]) -> Optional[Dict]:
    best_score = 0.0
    best_track = None

    vrt_artist_parts = re.split(
        r'\s+&\s+|\s+and\s+|\s*,\s*|\s+feat\.?\s+|\s+ft\.?\s+',
        vrt_artist.lower()
    )
    vrt_artist_parts = [p.strip() for p in vrt_artist_parts if p.strip()]

    for track in search_results[:MATCH_MAX_RESULTS]:
        track_artists = track.get("artists", [])
        if not track_artists:
            continue

        all_spotify_artists = " ".join(a.get("name", "") for a in track_artists)
        full_match_sim = calculate_artist_similarity(vrt_artist, all_spotify_artists)

        if len(vrt_artist_parts) > 1:
            matches = 0
            for vrt_part in vrt_artist_parts:
                for artist_obj in track_artists:
                    if calculate_artist_similarity(vrt_part, artist_obj.get("name", "")) >= MATCH_THRESHOLD:
                        matches += 1
                        break
            multi_artist_sim = matches / len(vrt_artist_parts) if vrt_artist_parts else 0
            max_artist_sim = max(full_match_sim, multi_artist_sim)
        else:
            max_artist_sim = max(
                calculate_artist_similarity(vrt_artist, a.get("name", ""))
                for a in track_artists
            )

        title_sim = calculate_artist_similarity(vrt_title, track.get("name", ""))
        score = (max_artist_sim * MATCH_ARTIST_WEIGHT) + (title_sim * MATCH_TITLE_WEIGHT)
        if score > best_score:
            best_score = score
            best_track = track

    return best_track if best_score >= MATCH_THRESHOLD else None


def search_and_add_song(spotify: SpotifyClient, song: Dict,
                        not_found_songs: Set[str], logger=None) -> Optional[str]:
    """Search Spotify for a song, return its URI or None."""
    first_artist = re.split(r'[&,]|feat\.?|ft\.?', song["artist"])[0].strip()
    for query in [
        f"{song['artist']} {song['title']}",
        f"{first_artist} {song['title']}",
        song["title"],
    ]:
        results = spotify.search(query)
        if results:
            track = find_best_match(song["artist"], song["title"], results)
            if track:
                uri = track.get("uri")
                if uri:
                    artist_names = ", ".join(a["name"] for a in track.get("artists", []))
                    print(f"  ✓ Found: {song['artist']} - {song['title']}")
                    print(f"    → {artist_names} - {track.get('name')}")
                    if logger:
                        logger.log_song(artist_names, track.get("name", ""), uri)
                    return uri

    not_found_songs.add(song_key(song))
    print(f"  ✗ Not found: {song['artist']} - {song['title']}")
    return None


# ---------------------------------------------------------------------------
# Device discovery (Spotify API + mDNS fallback)
# ---------------------------------------------------------------------------

def _scan_mdns(timeout: float = 4.0) -> List[Dict]:
    """Scan LAN for _spotify-connect._tcp services via mDNS."""
    found: List[Dict] = []
    lock = threading.Lock()

    def on_change(zeroconf: Zeroconf, service_type: str, name: str,
                  state_change: ServiceStateChange):
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if not info or not info.addresses:
            return
        props = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        with lock:
            found.append({
                "address": socket.inet_ntoa(info.addresses[0]),
                "port":    info.port,
                "path":    props.get("CPath", "/spotify-info"),
            })

    zc = Zeroconf()
    browser = ServiceBrowser(zc, "_spotify-connect._tcp.local.", handlers=[on_change])
    time.sleep(timeout)
    browser.cancel()
    zc.close()
    return found


def discover_and_wake_device(spotify: SpotifyClient, target_name: str) -> Optional[str]:
    """
    Scan LAN via mDNS for Spotify Connect devices.
    For each device found, fetch its deviceID via the Zeroconf getInfo endpoint,
    then attempt transfer_playback to wake its Spotify session.
    Returns the deviceID of the matching device if found, else None.
    """
    print("📡 Scanning LAN for Spotify Connect devices via mDNS...")
    candidates = _scan_mdns()
    if not candidates:
        print("  No Spotify Connect devices found on LAN")
        return None

    for d in candidates:
        url = f"http://{d['address']}:{d['port']}{d['path']}?action=getInfo"
        try:
            with urlopen(Request(url), timeout=5) as resp:
                info = json.loads(resp.read())
            remote_name = info.get("remoteName", "")
            device_id   = info.get("deviceID", "")
            print(f"  mDNS: '{remote_name}' at {d['address']}:{d['port']} "
                  f"(id: {device_id[:8] if device_id else '?'}…)")
            if remote_name.lower() == target_name.lower() and device_id:
                print("  Device is on the LAN — attempting to wake Spotify Connect session...")
                spotify.transfer_playback(device_id, play=False)
                return device_id
        except Exception as e:
            print(f"  getInfo failed for {d['address']}: {e}")

    print(f"  '{target_name}' not found among LAN devices")
    return None


def find_device(spotify: SpotifyClient, name: str,
                retries: int = 10, delay: int = 5) -> Optional[str]:
    """
    Find a Spotify Connect device by name.
    Checks the Spotify Web API first; on the 3rd failed attempt uses mDNS to
    locate the device on the LAN and nudges its Spotify Connect session via
    transfer_playback. Returns device_id or None after all retries.
    """
    mdns_attempted = False
    for attempt in range(1, retries + 1):
        devices = spotify.get_devices()
        for device in devices:
            if device.get("name", "").lower() == name.lower():
                print(f"✓ Found device: {device['name']} (id: {device['id'][:8]}…)")
                state.device_name = device["name"]
                return device["id"]

        available = [d.get("name") for d in devices]
        print(f"  Device '{name}' not found (attempt {attempt}/{retries}), "
              f"available: {available}")

        # After the 3rd regular attempt, try mDNS wake-up once
        if attempt == 3 and not mdns_attempted:
            mdns_attempted = True
            mdns_id = discover_and_wake_device(spotify, name)
            if mdns_id:
                # Give device a moment to register with Spotify backend
                time.sleep(5)
                continue  # retry API check immediately

        if attempt < retries:
            time.sleep(delay)

    return None


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def build_initial_uris(spotify: SpotifyClient, seen_songs: Set[str],
                       not_found_songs: Set[str], logger=None) -> List[str]:
    """Fetch VRT playlist and return a list of Spotify URIs (oldest → newest)."""
    vrt_songs = fetch_vrt_playlist()
    if not vrt_songs:
        print("No songs from VRT")
        return []

    print(f"Fetched {len(vrt_songs)} songs from VRT")
    songs_to_add = list(reversed(vrt_songs[:20]))  # oldest first
    uris = []
    for song in songs_to_add:
        if not song["title"] or not song["artist"]:
            continue
        uri = search_and_add_song(spotify, song, not_found_songs, logger)
        if uri:
            seen_songs.add(song_key(song))
            uris.append(uri)
    return uris


def sync_new_songs(spotify: SpotifyClient, device_id: str,
                   seen_songs: Set[str], not_found_songs: Set[str],
                   logger=None):
    """Check VRT for new songs and add them to the Spotify queue."""
    vrt_songs = fetch_vrt_playlist()
    if not vrt_songs:
        print("No songs from VRT, skipping sync")
        return

    print(f"Fetched {len(vrt_songs)} songs from VRT")
    new_count = 0
    for song in vrt_songs[:5]:
        if not song["title"] or not song["artist"]:
            continue
        key = song_key(song)
        if key not in seen_songs and key not in not_found_songs:
            print(f"\n  🆕 New song: {song['artist']} - {song['title']}")
            uri = search_and_add_song(spotify, song, not_found_songs, logger)
            if uri:
                spotify.add_to_queue(uri, device_id)
                seen_songs.add(key)
                new_count += 1

    if new_count == 0:
        print("  No new songs")




# ---------------------------------------------------------------------------
# HTTP control server
# ---------------------------------------------------------------------------

class ControlHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/play":
            self._handle_play()
        elif path == "/stop":
            self._handle_stop()
        elif path == "/status":
            self._handle_status()
        else:
            self._respond(404, {"error": "Not found"})

    def _handle_play(self):
        if not state.spotify or not state.device_id:
            self._respond(503, {"error": "Service not ready"})
            return
        try:
            ps = state.spotify.get_playback_state()
            if ps and ps.get("is_playing"):
                self._respond(200, {"status": "already_playing"})
                return
            if state.initial_uris:
                state.spotify.start_playback(state.device_id, state.initial_uris)
                state.initial_uris = []   # consumed — subsequent /play calls use transfer
            else:
                state.spotify.transfer_playback(state.device_id, play=True)
            self._respond(200, {"status": "playing"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _handle_stop(self):
        if not state.spotify or not state.device_id:
            self._respond(503, {"error": "Service not ready"})
            return
        try:
            state.spotify.pause_playback(state.device_id)
            self._respond(200, {"status": "stopped"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _handle_status(self):
        if not state.spotify:
            self._respond(503, {"error": "Service not ready"})
            return
        try:
            ps = state.spotify.get_playback_state()
            if not ps:
                self._respond(200, {"is_playing": False, "device": state.device_name})
                return
            track = ps.get("item") or {}
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            self._respond(200, {
                "is_playing":  ps.get("is_playing", False),
                "track":       track.get("name", ""),
                "artist":      artists,
                "device_name": ps.get("device", {}).get("name", ""),
            })
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} - {fmt % args}")


def start_control_server():
    server = HTTPServer(("0.0.0.0", CONTROL_PORT), ControlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"🌐 Control server listening on port {CONTROL_PORT} "
          f"(/play, /stop, /status)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(f"VRT to Spotify Sync Service")
    print(f"Stream: {STREAM_NAME}")
    print(f"Device: {SPOTIFY_DEVICE_NAME}")
    print(f"Checking every {CHECK_INTERVAL}s")
    print("=" * 60)
    print()

    if not all([SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN]):
        print("❌ SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN must all be set.")
        raise SystemExit(1)

    spotify = SpotifyClient(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN)
    state.spotify = spotify

    start_control_server()

    print(f"🔍 Looking for Spotify Connect device '{SPOTIFY_DEVICE_NAME}'...")
    device_id = find_device(spotify, SPOTIFY_DEVICE_NAME)
    while not device_id:
        print(f"⚠️  '{SPOTIFY_DEVICE_NAME}' not reachable via Spotify. "
              f"Retrying in 30s… (tip: open Spotify app and connect to the speaker once)")
        time.sleep(30)
        device_id = find_device(spotify, SPOTIFY_DEVICE_NAME, retries=3, delay=5)
    state.device_id = device_id

    logger = create_sheets_logger()
    seen_songs: Set[str] = set()
    not_found_songs: Set[str] = set()

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Initial sync...")
    uris = build_initial_uris(spotify, seen_songs, not_found_songs, logger)
    if uris:
        state.initial_uris = uris
        print(f"✓ {len(uris)} tracks queued — call /play to start playback")
    else:
        print("⚠️  No tracks found for initial queue")

    while True:
        try:
            time.sleep(CHECK_INTERVAL)
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Syncing...")
            sync_new_songs(spotify, device_id, seen_songs, not_found_songs, logger)
            print("✓ Sync complete")
        except KeyboardInterrupt:
            print("\nStopping...")
            break
        except Exception as e:
            print(f"Error in sync loop: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
One-time helper to obtain a Spotify refresh token via the Authorization Code flow.

Usage:
  1. Add http://localhost:8888/callback as a Redirect URI in your Spotify app at
     https://developer.spotify.com/dashboard
  2. Run: python get_token.py
  3. Open the printed URL in your browser and authorize
  4. Copy the printed SPOTIFY_REFRESH_TOKEN= line into your .env file

Requires only Python stdlib — no pip installs needed.
"""

import base64
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


def load_dotenv(path=".env"):
    """Load key=value pairs from a .env file into os.environ (no-op if missing)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            # Strip optional surrounding quotes
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env or environment.")
    raise SystemExit(1)

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization denied.")
            auth_code = "ERROR"
            return

        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Authorization successful! You can close this tab.")

    def log_message(self, *args):
        pass  # suppress request logs


def exchange_code(code: str) -> dict:
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })
    auth_url = f"https://accounts.spotify.com/authorize?{params}"

    print("=" * 60)
    print(f"Using client_id: {CLIENT_ID}")
    print()
    print("Open this URL in your browser to authorize:")
    print()
    print(auth_url)
    print()
    print("Waiting for callback on http://localhost:8888 ...")
    print("=" * 60)

    server = HTTPServer(("localhost", 8888), CallbackHandler)
    server.handle_request()  # handles exactly one request then returns

    if auth_code == "ERROR" or not auth_code:
        print("❌ Authorization failed or was denied.")
        raise SystemExit(1)

    print("✓ Got authorization code, exchanging for tokens...")
    tokens = exchange_code(auth_code)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print(f"❌ No refresh token in response: {tokens}")
        raise SystemExit(1)

    print()
    print("✅ Success! Add this to your .env file:")
    print()
    print(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
    print()


if __name__ == "__main__":
    main()

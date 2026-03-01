#!/usr/bin/env python3
"""
PKCE auth flow using the Arylic device's Spotify client ID.

This obtains a token that the Arylic A30 may accept in the addUser Zeroconf flow,
since the device firmware might validate that tokens come from its own client ID.

Usage:
  1. Run: python get_token_arylic.py
  2. Open the printed URL in your browser and authorize
  3. Copy the printed ARYLIC_SPOTIFY_REFRESH_TOKEN= line into your .env
"""
import base64, hashlib, json, os, sys, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ARYLIC_CLIENT_ID = "a933200be9704253b5f19b61b406c73f"
REDIRECT_URI     = "http://127.0.0.1:8888/callback"
SCOPES           = "streaming user-modify-playback-state user-read-playback-state user-read-currently-playing"

verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode()
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()

params = urllib.parse.urlencode({
    "client_id":             ARYLIC_CLIENT_ID,
    "response_type":         "code",
    "redirect_uri":          REDIRECT_URI,
    "scope":                 SCOPES,
    "code_challenge_method": "S256",
    "code_challenge":        challenge,
})
auth_url = f"https://accounts.spotify.com/authorize?{params}"

print("=" * 60, flush=True)
print("Open this URL in your browser to authorize:", flush=True)
print()
print(auth_url, flush=True)
print()
print("Waiting for callback on http://localhost:8888 ...", flush=True)
print("=" * 60, flush=True)

auth_code = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        p = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        if "error" in p:
            self.send_response(400); self.end_headers()
            self.wfile.write(b"Authorization denied.")
            auth_code = "ERROR"
            return
        auth_code = p.get("code")
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Authorization successful! You can close this tab.")
    def log_message(self, *a): pass

HTTPServer(("localhost", 8888), Handler).handle_request()

if not auth_code or auth_code == "ERROR":
    print("Authorization failed.", flush=True)
    sys.exit(1)

print("Got auth code, exchanging for tokens (PKCE — no secret needed)...", flush=True)

data = urllib.parse.urlencode({
    "grant_type":    "authorization_code",
    "code":          auth_code,
    "redirect_uri":  REDIRECT_URI,
    "client_id":     ARYLIC_CLIENT_ID,
    "code_verifier": verifier,
}).encode()

with urllib.request.urlopen(urllib.request.Request(
    "https://accounts.spotify.com/api/token",
    data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    method="POST"
)) as r:
    tokens = json.loads(r.read())

print(f"\nToken scopes: {tokens.get('scope')}", flush=True)

if tokens.get("refresh_token"):
    print("\n✅ Success! Add this to your .env file:", flush=True)
    print()
    print(f"ARYLIC_SPOTIFY_REFRESH_TOKEN={tokens['refresh_token']}", flush=True)
    print(f"\nAccess token preview: {tokens.get('access_token', '')[:40]}...", flush=True)
else:
    # PKCE sometimes doesn't return refresh tokens — just store the access token
    print("\n⚠️  No refresh token. Access token only (valid 1h):", flush=True)
    print(f"ARYLIC_ACCESS_TOKEN={tokens.get('access_token')}", flush=True)

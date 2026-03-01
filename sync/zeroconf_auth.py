#!/usr/bin/env python3
"""
Spotify Connect Zeroconf addUser implementation.

Reclaims a Spotify Connect device for our Spotify account by implementing
the client-side of the Zeroconf addUser protocol:
  1. DH key exchange with the device
  2. Build credential blob (username + OAuth access token)
  3. Encrypt with AES-192-ECB (inner) + AES-128-CTR (outer)
  4. POST addUser to device's Zeroconf endpoint

Algorithm based on librespot authentication.md and the Zeroconf protocol spec.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

# ---------------------------------------------------------------------------
# DH parameters — Spotify's 768-bit prime (RFC 2409 Oakley Group 1)
# ---------------------------------------------------------------------------

_PRIME = int(
    "ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd1"
    "29024e088a67cc74020bbea63b139b22514a08798e3404dd"
    "ef9519b3cd3a431b302b0a6df25f14374fe1356d6d51c245"
    "e485b576625e7ec6f44c42e9a63a3620ffffffffffffffff",
    16,
)
_GENERATOR = 2
_KEY_BYTES = 96  # 768-bit keys


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def _dh_generate():
    """Return (private_int, public_bytes)."""
    private = int.from_bytes(os.urandom(_KEY_BYTES - 1), "big")
    public  = pow(_GENERATOR, private, _PRIME).to_bytes(_KEY_BYTES, "big")
    return private, public


def _dh_shared(device_pub_b64: str, client_private: int) -> bytes:
    """Compute DH shared secret from device's base64 public key."""
    device_pub = int.from_bytes(base64.b64decode(device_pub_b64), "big")
    return pow(device_pub, client_private, _PRIME).to_bytes(_KEY_BYTES, "big")


def _hmac_sha1(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha1).digest()


def _aes128_ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend()).encryptor()
    return enc.update(data) + enc.finalize()


def _aes192_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend()).encryptor()
    return enc.update(data) + enc.finalize()


# ---------------------------------------------------------------------------
# Blob construction
# ---------------------------------------------------------------------------

def _write_int(n: int) -> bytes:
    """
    Variable-length integer encoding used in Spotify's blob format.
    Mirrors librespot's read_int: 1 byte for values ≤127, 2 bytes otherwise.
      small values (<=127) → [n]
      large values         → [(n & 0x7f) | 0x80, n >> 7]
    """
    if n > 127:
        return bytes([(n & 0x7f) | 0x80, n >> 7])
    return bytes([n])


def _build_inner_blob(username: str, access_token: str, device_id: str) -> bytes:
    """
    Build and encrypt the inner credential blob.

    Exact format reverse-engineered from librespot's with_blob() decoder:

        read_u8()       → [0x00] separator
        read_bytes()    → write_int(len(username)) + username
        read_u8()       → [0x00] separator
        read_int()      → write_int(4)  # AUTHENTICATION_SPOTIFY_TOKEN
        read_u8()       → [0x00] separator
        read_bytes()    → write_int(len(token)) + token

    XOR obfuscation: ALL bytes from position 16 onwards (forward direction).
    Key derivation: PBKDF2(SHA1(device_id), username, 256 iters, 20 bytes)
                    → SHA1(base_key) || htonl(20)  [24 bytes for AES-192]
    """
    username_b = username.encode()
    token_b    = access_token.encode()

    # Build plaintext — mirrors what librespot's with_blob() decoder expects
    raw = bytearray()
    raw.extend(b'\x00')                        # separator (read_u8 #1)
    raw.extend(_write_int(len(username_b)))    # } read_bytes → username
    raw.extend(username_b)                     # }
    raw.extend(b'\x00')                        # separator (read_u8 #2)
    raw.extend(_write_int(4))                  # read_int → auth_type = SpotifyToken
    raw.extend(b'\x00')                        # separator (read_u8 #3)
    raw.extend(_write_int(len(token_b)))       # } read_bytes → token
    raw.extend(token_b)                        # }

    # Zero-pad to AES block boundary
    pad = (16 - len(raw) % 16) % 16
    raw.extend(bytes(pad))

    # XOR obfuscation: ALL bytes from position 16 to end (forward direction).
    # Librespot decrypts by going backwards (l-1 → 16), each byte[j] ^= byte[j-16]
    # where byte[j-16] is always the encrypted value (untouched). The inverse
    # is the same operation applied forward (16 → l-1).
    l = len(raw)
    for j in range(16, l):
        raw[j] ^= raw[j - 16]

    # Derive 24-byte AES-192 key via PBKDF2
    secret   = hashlib.sha1(device_id.encode()).digest()
    kdf      = PBKDF2HMAC(algorithm=hashes.SHA1(), length=20, salt=username_b,
                           iterations=0x100, backend=default_backend())
    base_key = kdf.derive(secret)
    key      = hashlib.sha1(base_key).digest() + struct.pack(">I", len(base_key))  # 24 bytes

    return _aes192_ecb_encrypt(key, bytes(raw))


def _build_outer_blob(inner: bytes, device_pub_b64: str,
                      client_private: int) -> tuple[bytes, bytes]:
    """
    Wrap inner blob with the DH outer layer.
    Returns (encrypted_blob_bytes, client_public_key_bytes).

    Layout: IV (16) + AES128-CTR(inner) + HMAC-SHA1(20)
    """
    shared  = _dh_shared(device_pub_b64, client_private)
    base_key        = hashlib.sha1(shared).digest()          # 20 bytes
    checksum_key    = _hmac_sha1(base_key, b"checksum")      # 20 bytes
    encryption_key  = _hmac_sha1(base_key, b"encryption")[:16]  # 16 bytes

    iv        = os.urandom(16)
    encrypted = _aes128_ctr(encryption_key, iv, inner)
    mac       = _hmac_sha1(checksum_key, encrypted)

    return iv + encrypted + mac


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_user(address: str, port: int, path: str,
             device_id: str, device_pub_b64: str,
             username: str, access_token: str,
             client_device_id: str = None,
             client_device_name: str = "jaren-nul-sync") -> dict:
    """
    Call the Zeroconf addUser endpoint to claim the device for our account.

    Returns the parsed JSON response from the device.
    """
    if client_device_id is None:
        client_device_id = hashlib.sha1(os.urandom(20)).hexdigest()

    client_private, client_public = _dh_generate()

    inner = _build_inner_blob(username, access_token, device_id)
    blob  = _build_outer_blob(inner, device_pub_b64, client_private)

    blob_b64       = base64.b64encode(blob).decode()
    client_key_b64 = base64.b64encode(client_public).decode()

    url  = f"http://{address}:{port}{path}"
    body = urlencode({
        "action":     "addUser",
        "userName":   username,
        "blob":       blob_b64,
        "clientKey":  client_key_b64,
        "deviceId":   client_device_id,
        "deviceName": client_device_name,
        "tokenType":  "accesstoken",
    }).encode()

    req = Request(url, data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                  method="POST")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Standalone test — run directly to claim the device
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import time
    import base64 as _b64
    from urllib.parse import urlencode as _ue

    def _load_env(path=".env"):
        if not os.path.exists(path):
            return
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    _load_env()

    CLIENT_ID     = os.environ["SPOTIFY_CLIENT_ID"]
    CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
    REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"]
    DEVICE_NAME   = os.environ.get("SPOTIFY_DEVICE_NAME", "Keuken")

    # 1. Get a fresh access token + Spotify username
    creds = _b64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = urlopen(Request(
        "https://accounts.spotify.com/api/token",
        data=_ue({"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN}).encode(),
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    ), timeout=10)
    tok      = json.loads(r.read())
    token    = tok["access_token"]
    print(f"✓ Access token obtained")

    r = urlopen(Request("https://api.spotify.com/v1/me",
                        headers={"Authorization": f"Bearer {token}"}), timeout=10)
    me       = json.loads(r.read())
    username = me["id"]
    print(f"✓ Username: {username}")

    # 2. mDNS scan to find device
    import socket, threading
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

    found = []
    lock  = threading.Lock()

    def _on_change(zeroconf: Zeroconf, service_type, name, state_change):
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if not info or not info.addresses:
            return
        props = {(k.decode() if isinstance(k, bytes) else k):
                 (v.decode() if isinstance(v, bytes) else v)
                 for k, v in (info.properties or {}).items()}
        with lock:
            found.append({"address": socket.inet_ntoa(info.addresses[0]),
                          "port":    info.port,
                          "path":    props.get("CPath", "/spotify-info")})

    zc = Zeroconf()
    ServiceBrowser(zc, "_spotify-connect._tcp.local.", handlers=[_on_change])
    print("📡 mDNS scan (4s)...")
    time.sleep(4)
    zc.close()

    target = None
    for d in found:
        r = urlopen(Request(f"http://{d['address']}:{d['port']}{d['path']}?action=getInfo"),
                    timeout=5)
        info = json.loads(r.read())
        print(f"  Found: '{info.get('remoteName')}' at {d['address']}")
        if info.get("remoteName", "").lower() == DEVICE_NAME.lower():
            target = {**d, "device_id": info["deviceID"], "public_key": info["publicKey"]}

    if not target:
        print(f"✗ '{DEVICE_NAME}' not found on LAN")
        sys.exit(1)

    print(f"\n🔑 Sending addUser to '{DEVICE_NAME}'...")
    result = add_user(
        address        = target["address"],
        port           = target["port"],
        path           = target["path"],
        device_id      = target["device_id"],
        device_pub_b64 = target["public_key"],
        username       = username,
        access_token   = token,
    )
    print(f"Response: {result}")

    # 3. Verify device now appears in Spotify Web API
    time.sleep(3)
    r = urlopen(Request("https://api.spotify.com/v1/me/player/devices",
                        headers={"Authorization": f"Bearer {token}"}), timeout=10)
    devices = json.loads(r.read()).get("devices", [])
    names   = [d["name"] for d in devices]
    print(f"\nSpotify devices after addUser: {names}")
    if DEVICE_NAME in names:
        print(f"✅ '{DEVICE_NAME}' is now registered under our account!")
    else:
        print(f"❌ '{DEVICE_NAME}' still not visible — blob likely incorrect")

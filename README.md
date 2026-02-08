# De Jaren Nul - 109

Auto-sync VRT "De Jaren Nul" playlist to an Icecast stream via Spotify.

## Quick Start

### 1. Get Spotify Credentials

**You need Spotify Premium**

1. Go to https://mopidy.com/ext/spotify/
2. Click the "Authenticate Mopidy with Spotify" button
3. Follow the instructions in the popup
4. After closing the popup, the page will show your credentials
5. Copy the `client_id` and `client_secret` values

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and paste your credentials:
```ini
SPOTIFY_CLIENT_ID=your_client_id_from_mopidy
SPOTIFY_CLIENT_SECRET=your_client_secret_from_mopidy
```

### 3. Start

```bash
docker-compose up -d
```

### 4. Listen

Stream URL: `http://YOUR_SERVER_IP:8000/stream`

Mopidy Web UI: `http://YOUR_SERVER_IP:6680`

## Configuration

All configuration is in `.env`:

- `SPOTIFY_CLIENT_ID` - Your Spotify client ID
- `SPOTIFY_CLIENT_SECRET` - Your Spotify client secret

Everything else is hardcoded for simplicity:
- Stream checks every 2 minutes
- Playlist cleanup every 24 hours
- Stream name: "De Jaren Nul - 109"

## Usage

### View logs
```bash
docker-compose logs -f
```

### Restart
```bash
docker-compose restart
```

### Stop
```bash
docker-compose down
```

## Architecture

- **Icecast** - Streaming server
- **Mopidy** - Music server with Spotify backend
- **Sync Service** - Python script that syncs VRT playlist to Mopidy

The sync service:
- Fetches the latest playlist from VRT every 2 minutes
- Searches for songs on Spotify
- Adds new songs to the queue
- Removes old songs once per day

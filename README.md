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

| Variable | Required | Description |
|---|---|---|
| `SPOTIFY_CLIENT_ID` | Yes | Your Spotify client ID |
| `SPOTIFY_CLIENT_SECRET` | Yes | Your Spotify client secret |
| `GOOGLE_SHEETS_ID` | No | Google Spreadsheet ID to log added songs |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | No | Path to Google service account JSON on the host |

Everything else is hardcoded for simplicity:
- Stream checks every 2 minutes
- Playlist cleanup every 24 hours
- Stream name: "De Jaren Nul - 109"

## Google Sheets Logging (optional)

When configured, every song added to the playlist is logged to a Google Sheet with columns:
**Spotify Artist | Spotify Title | Times Added | URI**

The Spotify URI is used as the unique key — when the same track is added again, the Times Added counter increments rather than creating a duplicate row.

### Setup

**1. Create a Google Cloud service account**

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts** and create a service account
5. Create a JSON key for the service account and download it

**2. Share your spreadsheet**

1. Create a new Google Sheet
2. Copy the spreadsheet ID from the URL: `https://docs.google.com/spreadsheets/d/**SPREADSHEET_ID**/edit`
3. Share the sheet with the service account's email address (Editor access)

**3. Configure `.env`**

```ini
GOOGLE_SHEETS_ID=your_spreadsheet_id_here
GOOGLE_SHEETS_CREDENTIALS_PATH=/path/to/service-account.json
```

The credentials file is mounted read-only into the container at runtime — it does not get baked into the Docker image.

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

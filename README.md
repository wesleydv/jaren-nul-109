# De Jaren Nul - 109

Syncs the VRT "De Jaren Nul" playlist to a Spotify Connect speaker.

The service runs as a Docker container. It periodically fetches the VRT playlist, searches for each track on Spotify, and adds new songs to the speaker's queue. Playback is fully manual — the service never auto-starts or auto-resumes.

## Setup

### 1. Create a Spotify app

Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create an app. Set the redirect URI to `http://127.0.0.1:8888/callback`.

Copy the **Client ID** and **Client Secret**.

### 2. Get a refresh token

```bash
cp .env.example .env
# Fill in SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET
python sync/get_token.py
```

Authorize in the browser. Copy the printed `SPOTIFY_REFRESH_TOKEN=...` line into `.env`.

### 3. Configure the speaker name

Set `SPOTIFY_DEVICE_NAME` in `.env` to the name of the speaker as it appears in the Spotify app (e.g. `Keuken`).

The speaker must be visible in your Spotify account at least once before starting the container — open the Spotify app and connect to it manually to register it.

### 4. Deploy

```bash
docker-compose up -d
```

## Controlling playback

The container exposes an HTTP control API on port 8877:

| Endpoint | Description |
|---|---|
| `GET /play` | Start playback (first call loads the queue; subsequent calls resume) |
| `GET /stop` | Pause playback |
| `GET /status` | Returns current playback state as JSON |

```bash
curl http://localhost:8877/play
curl http://localhost:8877/stop
curl http://localhost:8877/status
```

## Google Sheets logging (optional)

When configured, every track added to the queue is logged to a Google Sheet with columns: **Artist · Title · Times Added · Spotify URI**.

1. Create a Google Cloud service account and enable the **Google Sheets API**
2. Download the JSON key file
3. Share your spreadsheet with the service account's email (Editor access)
4. Add to `.env`:

```ini
GOOGLE_SHEETS_ID=your_spreadsheet_id_here
GOOGLE_SHEETS_CREDENTIALS_PATH=/path/to/service-account.json
```

The credentials file is mounted read-only into the container — it is not baked into the image.

## Useful commands

```bash
docker-compose logs -f        # follow logs
docker-compose restart        # restart the container
docker-compose down           # stop
```

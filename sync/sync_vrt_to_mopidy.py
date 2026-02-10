#!/usr/bin/env python3
"""
VRT to Mopidy Sync Service
Continuously syncs VRT "De Jaren Nul" playlist to Mopidy
Only adds NEW songs as they appear on the radio to avoid interrupting playback
"""

import requests
import json
import time
import os
from datetime import datetime
from difflib import SequenceMatcher
from typing import List, Dict, Set, Optional

# Configuration
MOPIDY_HOST = os.getenv('MOPIDY_HOST', 'localhost')
MOPIDY_PORT = int(os.getenv('MOPIDY_PORT', '6680'))
CHECK_INTERVAL = 120  # Check every 2 minutes
PRUNE_INTERVAL = 86400  # Prune every 24 hours
STREAM_NAME = 'De Jaren Nul - 109'

# VRT GraphQL endpoint
GRAPHQL_URL = 'https://www.vrt.be/vrtnu-api/graphql/public/v1'
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


class MopidyClient:
    """Simple Mopidy JSON-RPC client"""

    def __init__(self, host: str, port: int):
        self.url = f"http://{host}:{port}/mopidy/rpc"
        self.id_counter = 0

    def _call(self, method: str, **params):
        """Make a JSON-RPC call to Mopidy"""
        self.id_counter += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self.id_counter,
            "method": method,
            "params": params
        }

        try:
            response = requests.post(self.url, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            return result.get('result')
        except Exception as e:
            print(f"Mopidy RPC error: {e}")
            return None

    def search(self, query: str) -> List[Dict]:
        """Search Spotify via Mopidy"""
        results = self._call('core.library.search', query={'any': [query]}, uris=['spotify:'])
        if results:
            for result in results:
                if result.get('tracks'):
                    return result['tracks']
        return []

    def add_track(self, uri: str):
        """Add track to end of tracklist"""
        self._call('core.tracklist.add', uris=[uri])

    def play(self):
        """Start playback"""
        self._call('core.playback.play')

    def stop(self):
        """Stop playback"""
        self._call('core.playback.stop')

    def next_track(self):
        """Skip to next track"""
        self._call('core.playback.next')

    def get_state(self):
        """Get current playback state"""
        return self._call('core.playback.get_state')

    def get_tracklist_length(self):
        """Get current tracklist length"""
        return self._call('core.tracklist.get_length')

    def get_current_track(self):
        """Get currently playing track"""
        return self._call('core.playback.get_current_track')

    def get_current_tl_track(self):
        """Get currently playing tracklist track (includes tlid)"""
        return self._call('core.playback.get_current_tl_track')

    def get_tl_tracks(self):
        """Get all tracklist tracks"""
        return self._call('core.tracklist.get_tl_tracks')

    def remove_tracks(self, criteria):
        """Remove tracks from tracklist"""
        return self._call('core.tracklist.remove', criteria=criteria)


def check_icecast_stream(icecast_host: str, mount: str = '/stream') -> bool:
    """Check if Icecast has an active source on the specified mount point"""
    try:
        response = requests.get(f"http://{icecast_host}:8000/status-json.xsl", timeout=5)
        response.raise_for_status()
        data = response.json()

        # Check if there's an active source
        icestats = data.get('icestats', {})
        source = icestats.get('source')

        # Handle single source (dict) or multiple sources (list)
        if isinstance(source, dict):
            return source.get('mount') == mount
        elif isinstance(source, list):
            return any(s.get('mount') == mount for s in source)

        return False
    except Exception as e:
        print(f"⚠️  Error checking Icecast status: {e}")
        return False


def fetch_vrt_playlist() -> List[Dict]:
    """Fetch current playlist from VRT GraphQL API"""
    headers = {
        'content-type': 'application/json',
        'x-vrt-client-name': 'WEB',
        'x-vrt-client-version': '1.5.15'
    }

    payload = {
        'operationName': 'component',
        'query': QUERY,
        'variables': {
            'componentId': COMPONENT_ID,
            'lazyItemCount': 30
        }
    }

    try:
        response = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        components = data.get('data', {}).get('component', {}).get('components', [])
        for component in components:
            if 'paginatedItems' in component:
                edges = component['paginatedItems']['edges']
                songs = []
                for edge in edges:
                    node = edge.get('node', {})
                    if node.get('title') and node.get('description'):
                        songs.append({
                            'title': node.get('title', '').strip(),
                            'artist': node.get('description', '').strip(),
                            'start_date': node.get('startDate', ''),
                            'active': node.get('active', False)
                        })
                return songs
    except Exception as e:
        print(f"Error fetching VRT playlist: {e}")

    return []


def song_key(song: Dict) -> str:
    """Generate unique key for a song"""
    return f"{song['artist']}::{song['title']}::{song['start_date']}"


def normalize_artist_name(artist: str) -> str:
    """
    Normalize artist name for comparison.

    Handles:
    - Case insensitivity
    - Featured artists: (feat. X), (ft. X), [feat. X]
    - Collaborations: & → and
    - Special characters and extra whitespace
    """
    import re

    if not artist:
        return ""

    # Lowercase
    artist = artist.lower()

    # Remove parenthetical/bracketed content
    artist = re.sub(r'\([^)]*\)', '', artist)
    artist = re.sub(r'\[[^\]]*\]', '', artist)

    # Remove featuring variations
    artist = re.sub(r'\b(feat\.?|ft\.?|featuring)\b.*', '', artist)

    # Normalize separators
    artist = artist.replace('&', 'and')
    artist = artist.replace(' vs. ', ' vs ')
    artist = artist.replace(' vs ', ' ')

    # Remove punctuation and extra whitespace
    artist = re.sub(r'[.,;]', '', artist)
    artist = re.sub(r'\s+', ' ', artist)

    return artist.strip()


def calculate_artist_similarity(artist1: str, artist2: str) -> float:
    """
    Calculate similarity between two artist names.
    Returns 0.0 (no match) to 1.0 (exact match).
    """
    norm1 = normalize_artist_name(artist1)
    norm2 = normalize_artist_name(artist2)

    if not norm1 or not norm2:
        return 0.0

    return SequenceMatcher(None, norm1, norm2).ratio()


def find_best_match(vrt_artist: str, vrt_title: str, search_results: List[Dict]) -> Optional[Dict]:
    """
    Find best matching track from search results using artist validation.

    Scoring:
    - Artist similarity: 70% weight
    - Title similarity: 30% weight
    - Minimum threshold: 0.6 (60%)
    """
    import re

    ARTIST_WEIGHT = 0.7
    TITLE_WEIGHT = 0.3
    THRESHOLD = 0.7
    MAX_RESULTS = 10

    best_score = 0.0
    best_track = None

    # Split VRT artist into individual names (handles &, and, feat., ,)
    vrt_artist_parts = re.split(r'\s+&\s+|\s+and\s+|\s*,\s*|\s+feat\.?\s+|\s+ft\.?\s+', vrt_artist.lower())
    vrt_artist_parts = [p.strip() for p in vrt_artist_parts if p.strip()]

    for track in search_results[:MAX_RESULTS]:
        # Get artist names from track
        track_artists = track.get('artists', [])
        if not track_artists:
            continue

        # For multi-artist tracks, join all Spotify artists and compare against full VRT artist
        all_spotify_artists = ' '.join([a.get('name', '') for a in track_artists])
        full_match_sim = calculate_artist_similarity(vrt_artist, all_spotify_artists)

        # Also try matching individual VRT artist parts against Spotify artists
        if len(vrt_artist_parts) > 1:
            # Calculate what percentage of VRT artists appear in Spotify artists
            matches = 0
            for vrt_part in vrt_artist_parts:
                for artist_obj in track_artists:
                    artist_name = artist_obj.get('name', '')
                    if artist_name:
                        sim = calculate_artist_similarity(vrt_part, artist_name)
                        if sim >= 0.7:  # Individual artist needs 70% match
                            matches += 1
                            break

            # Score based on percentage of artists matched
            multi_artist_sim = matches / len(vrt_artist_parts) if vrt_artist_parts else 0

            # Use the better of the two approaches
            max_artist_sim = max(full_match_sim, multi_artist_sim)
        else:
            # Single artist: just use direct comparison
            max_artist_sim = 0.0
            for artist_obj in track_artists:
                artist_name = artist_obj.get('name', '')
                if artist_name:
                    sim = calculate_artist_similarity(vrt_artist, artist_name)
                    max_artist_sim = max(max_artist_sim, sim)

        # Calculate title similarity
        track_title = track.get('name', '')
        title_sim = calculate_artist_similarity(vrt_title, track_title)  # Reuse normalization logic

        # Weighted score
        score = (max_artist_sim * ARTIST_WEIGHT) + (title_sim * TITLE_WEIGHT)

        if score > best_score:
            best_score = score
            best_track = track

    # Return best match if above threshold
    if best_score >= THRESHOLD:
        return best_track

    return None


def search_and_add_song(mopidy: MopidyClient, song: Dict) -> bool:
    """Search for a song on Spotify and add it to the playlist"""
    import re

    # Try primary search: artist + title
    query = f"{song['artist']} {song['title']}"
    results = mopidy.search(query)

    if results:
        track = find_best_match(song['artist'], song['title'], results)

        if track:
            uri = track.get('uri')
            if uri:
                mopidy.add_track(uri)
                # Enhanced logging
                artist_names = ', '.join(a['name'] for a in track.get('artists', []))
                print(f"  ✓ Added: {song['artist']} - {song['title']}")
                print(f"    Matched to: {artist_names} - {track.get('name')}")
                return True

    # Fallback 1: try with first artist only (for multi-artist tracks)
    first_artist = re.split(r'\s+&\s+|\s+and\s+|\s*,\s*|\s+feat\.?\s+', song['artist'])[0].strip()
    if first_artist != song['artist']:
        query = f"{first_artist} {song['title']}"
        results = mopidy.search(query)

        if results:
            track = find_best_match(song['artist'], song['title'], results)
            if track:
                uri = track.get('uri')
                if uri:
                    mopidy.add_track(uri)
                    artist_names = ', '.join(a['name'] for a in track.get('artists', []))
                    print(f"  ✓ Added (first artist): {song['artist']} - {song['title']}")
                    print(f"    Matched to: {artist_names} - {track.get('name')}")
                    return True

    # Fallback 2: try title-only search
    results = mopidy.search(song['title'])

    if results:
        track = find_best_match(song['artist'], song['title'], results)
        if track:
            uri = track.get('uri')
            if uri:
                mopidy.add_track(uri)
                artist_names = ', '.join(a['name'] for a in track.get('artists', []))
                print(f"  ✓ Added (title-only): {song['artist']} - {song['title']}")
                print(f"    Matched to: {artist_names} - {track.get('name')}")
                return True

    # No good match found
    print(f"  ✗ Not found: {song['artist']} - {song['title']}")
    print(f"    (No match above 70% confidence threshold)")
    return False


def prune_old_tracks(mopidy: MopidyClient, max_playlist_length: int = 50):
    """
    Smart playlist pruning - remove old tracks while keeping playback going

    Removes tracks that have already been played, keeping only:
    - Current track
    - Upcoming tracks in queue
    """
    tracklist_length = mopidy.get_tracklist_length()

    if tracklist_length <= max_playlist_length:
        print(f"  Playlist size ({tracklist_length}) is OK, no pruning needed")
        return False

    print(f"  Playlist too large ({tracklist_length} tracks), pruning old songs...")

    current_tl_track = mopidy.get_current_tl_track()
    if not current_tl_track:
        print("  No track currently playing, skipping prune")
        return False

    current_tlid = current_tl_track.get('tlid')

    tl_tracks = mopidy.get_tl_tracks()
    if not tl_tracks:
        return False

    # Remove tracks before the current track
    tracks_to_remove = []
    for tl_track in tl_tracks:
        tlid = tl_track.get('tlid')
        if tlid < current_tlid:
            tracks_to_remove.append({'tlid': [tlid]})

    if tracks_to_remove:
        print(f"  Removing {len(tracks_to_remove)} already-played tracks...")
        for criteria in tracks_to_remove:
            mopidy.remove_tracks(criteria)
        print(f"  ✓ Pruned! Playlist now has {mopidy.get_tracklist_length()} tracks")
        return True
    else:
        print("  No old tracks to remove")
        return False


def sync_to_mopidy(mopidy: MopidyClient, seen_songs: Set[str], is_initial: bool = False, prune: bool = False):
    """Smart sync logic - only add NEW songs"""
    if prune:
        print("\n🧹 Pruning old tracks...")
        prune_old_tracks(mopidy, max_playlist_length=50)

    vrt_songs = fetch_vrt_playlist()
    if not vrt_songs:
        print("No songs from VRT, skipping sync")
        return

    print(f"Fetched {len(vrt_songs)} songs from VRT")

    if is_initial:
        print("\n🎵 Initial playlist build - adding songs from oldest to newest...")
        songs_to_add = list(reversed(vrt_songs[:20]))

        for song in songs_to_add:
            if not song['title'] or not song['artist']:
                continue

            song_id = song_key(song)
            if search_and_add_song(mopidy, song):
                seen_songs.add(song_id)

        print("\nStarting playback...")
        mopidy.play()

    else:
        print("\n🔄 Checking for new songs...")
        new_songs_found = False

        for song in vrt_songs[:5]:
            if not song['title'] or not song['artist']:
                continue

            song_id = song_key(song)

            if song_id not in seen_songs:
                print(f"\n  🆕 New song detected!")
                if search_and_add_song(mopidy, song):
                    seen_songs.add(song_id)
                    new_songs_found = True

        if not new_songs_found:
            print("  No new songs to add")

    tracklist_length = mopidy.get_tracklist_length()
    current = mopidy.get_current_track()

    print(f"\n📊 Playlist: {tracklist_length} tracks")

    if current:
        name = current.get('name', 'Unknown')
        artists = current.get('artists', [])
        artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
        print(f"🎵 Now playing: {artist} - {name}")

    # Auto-resume playback if stopped/paused
    state = mopidy.get_state()
    if state in ['stopped', 'paused'] and tracklist_length > 0:
        print("⚠️  Playback is paused/stopped, auto-resuming...")
        mopidy.play()
        print("▶️  Playback resumed")

    # Check stream health: if Mopidy is playing but Icecast has no stream, skip the broken track
    # Skip this check on initial sync - GStreamer needs time to connect to Icecast after first play()
    elif state == 'playing' and tracklist_length > 0 and not is_initial:
        icecast_host = os.getenv('ICECAST_HOST', 'icecast')
        if not check_icecast_stream(icecast_host):
            broken_track = mopidy.get_current_track()
            broken_name = broken_track.get('name', 'Unknown') if broken_track else 'Unknown'
            broken_artists = broken_track.get('artists', []) if broken_track else []
            broken_artist = broken_artists[0].get('name', 'Unknown') if broken_artists else 'Unknown'
            print(f"⚠️  Stream is broken - skipping unavailable track: {broken_artist} - {broken_name}")

            mopidy.stop()
            time.sleep(1)
            mopidy.next_track()  # Skip the track that broke the pipeline
            time.sleep(1)
            mopidy.play()

            # Verify stream recovered
            time.sleep(4)
            if check_icecast_stream(icecast_host):
                print("✅ Stream recovered")
            else:
                print("⚠️  Stream still broken after skip, will retry next cycle")

    print("✓ Sync complete\n")


def main():
    """Main loop"""
    print(f"{'='*60}")
    print(f"VRT to Mopidy Sync Service")
    print(f"Stream: {STREAM_NAME}")
    print(f"Icecast stream: http://{MOPIDY_HOST}:8000/stream")
    print(f"Mopidy Web UI: http://{MOPIDY_HOST}:6680")
    print(f"Checking every {CHECK_INTERVAL} seconds")
    print(f"Pruning old tracks every {PRUNE_INTERVAL} seconds ({PRUNE_INTERVAL/3600:.1f} hours)")
    print(f"{'='*60}\n")

    mopidy = MopidyClient(MOPIDY_HOST, MOPIDY_PORT)

    print("Waiting for Mopidy to start...")
    for i in range(30):
        try:
            state = mopidy.get_state()
            if state is not None:
                print("✓ Mopidy is ready\n")
                break
        except:
            pass
        time.sleep(2)
    else:
        print("✗ Mopidy didn't start in time")
        return

    seen_songs: Set[str] = set()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Initial sync...")
    try:
        sync_to_mopidy(mopidy, seen_songs, is_initial=True, prune=False)
    except Exception as e:
        print(f"Error in initial sync: {e}")
        import traceback
        traceback.print_exc()

    last_prune_time = time.time()

    sync_counter = 0
    while True:
        try:
            time.sleep(CHECK_INTERVAL)
            sync_counter += 1

            current_time = time.time()
            should_prune = (current_time - last_prune_time) >= PRUNE_INTERVAL

            if should_prune:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Daily prune + checking for new songs...")
                last_prune_time = current_time
            else:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for new songs...")

            sync_to_mopidy(mopidy, seen_songs, is_initial=False, prune=should_prune)

        except KeyboardInterrupt:
            print("\nStopping...")
            break
        except Exception as e:
            print(f"Error in sync loop: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()

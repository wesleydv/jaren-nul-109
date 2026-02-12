"""
Google Sheets logger for tracking added songs.
Can be replaced with any other logging backend.
"""

import os


class SheetsLogger:
    HEADERS = ['Spotify Artist', 'Spotify Title', 'Times Added', 'URI']

    def __init__(self, credentials_path: str, spreadsheet_id: str):
        import gspread
        gc = gspread.service_account(filename=credentials_path)
        self._sheet = gc.open_by_key(spreadsheet_id).sheet1

        # Ensure header row exists
        first_row = self._sheet.row_values(1)
        if first_row != self.HEADERS:
            self._sheet.insert_row(self.HEADERS, 1)

        print(f"✓ Google Sheets logger connected (sheet: {spreadsheet_id})")

    def log_song(self, spotify_artist: str, spotify_title: str, spotify_uri: str):
        """Upsert a song by URI: increment Times Added if it exists, otherwise add a new row."""
        try:
            # Find existing row by Spotify URI (column D)
            all_values = self._sheet.get_all_values()
            for i, row in enumerate(all_values[1:], start=2):  # Skip header
                if len(row) >= 4 and row[3] == spotify_uri:
                    # Found — increment Times Added (column C)
                    times_added = int(row[2]) + 1 if len(row) >= 3 and row[2].isdigit() else 1
                    self._sheet.update_cell(i, 3, times_added)
                    return

            # Not found — append new row
            self._sheet.append_row([spotify_artist, spotify_title, 1, spotify_uri])
        except Exception as e:
            print(f"⚠️  Sheets logger error: {e}")


def create_logger() -> 'SheetsLogger | None':
    """Create a SheetsLogger from environment variables, or return None if not configured."""
    credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
    spreadsheet_id = os.getenv('GOOGLE_SHEETS_ID')

    if not credentials_path or not spreadsheet_id:
        return None

    if not os.path.exists(credentials_path):
        print(f"⚠️  Google Sheets credentials not found at {credentials_path}, logging disabled")
        return None

    try:
        return SheetsLogger(credentials_path, spreadsheet_id)
    except Exception as e:
        print(f"⚠️  Failed to connect to Google Sheets: {e}, logging disabled")
        return None

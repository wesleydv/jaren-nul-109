FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir gspread

COPY sync_vrt_to_spotify.py .
COPY sheets_logger.py .

# Run the sync service
CMD ["python", "-u", "sync_vrt_to_spotify.py"]

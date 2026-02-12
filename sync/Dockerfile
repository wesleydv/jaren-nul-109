FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir requests gspread

COPY sync_vrt_to_mopidy.py .
COPY sheets_logger.py .

# Run the sync service
CMD ["python", "-u", "sync_vrt_to_mopidy.py"]

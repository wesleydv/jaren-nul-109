FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir requests

COPY sync_vrt_to_mopidy.py .

# Run the sync service
CMD ["python", "-u", "sync_vrt_to_mopidy.py"]

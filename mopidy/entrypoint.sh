#!/bin/bash
set -e

# Generate mopidy.conf from template with environment variables using sed
sed -e "s|\${SPOTIFY_CLIENT_ID}|${SPOTIFY_CLIENT_ID}|g" \
    -e "s|\${SPOTIFY_CLIENT_SECRET}|${SPOTIFY_CLIENT_SECRET}|g" \
    /config/mopidy.conf.template > /config/mopidy.conf

# Execute the original entrypoint
exec /entrypoint.sh "$@"

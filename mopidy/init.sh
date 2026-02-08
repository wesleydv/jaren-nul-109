#!/bin/bash
# Generate mopidy.conf from template
sed -e "s|\${SPOTIFY_CLIENT_ID}|${SPOTIFY_CLIENT_ID}|g" \
    -e "s|\${SPOTIFY_CLIENT_SECRET}|${SPOTIFY_CLIENT_SECRET}|g" \
    /config/mopidy.conf.template > /config/mopidy.conf

# Make sure permissions are correct
chown mopidy:audio /config/mopidy.conf
chmod 644 /config/mopidy.conf

# Run the original entrypoint
exec /entrypoint.sh "$@"

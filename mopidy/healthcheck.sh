#!/bin/bash
# Health check for Mopidy: verify it's responsive

# Check if Mopidy HTTP is responding
if ! timeout 2 bash -c "echo > /dev/tcp/localhost/6680" 2>/dev/null; then
    echo "Mopidy HTTP not responding"
    exit 1
fi

# Check if we can query the RPC endpoint using Python (available in Mopidy image)
python3 -c "
import requests
import sys

try:
    response = requests.post(
        'http://localhost:6680/mopidy/rpc',
        json={'jsonrpc': '2.0', 'id': 1, 'method': 'core.playback.get_state'},
        timeout=5
    )
    if response.status_code == 200:
        result = response.json().get('result')
        # Accept 'playing', 'paused', or 'stopped' as healthy states
        if result in ['playing', 'paused', 'stopped']:
            sys.exit(0)
    print(f'Mopidy RPC unhealthy: {response.status_code}')
    sys.exit(1)
except Exception as e:
    print(f'Mopidy RPC error: {e}')
    sys.exit(1)
"

exit $?

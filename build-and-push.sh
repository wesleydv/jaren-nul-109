#!/bin/bash
set -e

echo "Building Docker images for amd64 (x86_64)..."
echo ""

cd "$(dirname "$0")"

# Build mopidy image for amd64
echo "📦 Building mopidy image..."
docker build --no-cache --platform linux/amd64 -t wesleydv938/jaren-nul-mopidy:latest ./mopidy
echo "✅ Mopidy image built"
echo ""

# Build sync image for amd64
echo "📦 Building sync image..."
docker build --no-cache --platform linux/amd64 -t wesleydv938/jaren-nul-sync:latest ./sync
echo "✅ Sync image built"
echo ""

# Push both images
echo "🚀 Pushing images to Docker Hub..."
docker push wesleydv938/jaren-nul-mopidy:latest
docker push wesleydv938/jaren-nul-sync:latest

echo ""
echo "✅ All done! Images are ready for deployment on x86_64/amd64 servers."
echo ""
echo "Images pushed:"
echo "  - wesleydv938/jaren-nul-mopidy:latest"
echo "  - wesleydv938/jaren-nul-sync:latest"

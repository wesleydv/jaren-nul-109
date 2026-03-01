#!/bin/bash
set -e

echo "Building Docker image for amd64 (x86_64)..."
echo ""

cd "$(dirname "$0")"

# Build sync image for amd64
echo "📦 Building sync image..."
docker build --no-cache --platform linux/amd64 -t wesleydv938/jaren-nul-sync:latest ./sync
echo "✅ Sync image built"
echo ""

# Push image
echo "🚀 Pushing image to Docker Hub..."
docker push wesleydv938/jaren-nul-sync:latest

echo ""
echo "✅ All done! Image is ready for deployment on x86_64/amd64 servers."
echo ""
echo "Image pushed:"
echo "  - wesleydv938/jaren-nul-sync:latest"

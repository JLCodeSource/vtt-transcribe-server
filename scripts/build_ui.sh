#!/usr/bin/env sh
# Build the Svelte app and copy it to vtt_server/static, where the wheel build picks it up.
# Needs Node 22+ (or run it in a container: docker run --rm -v "$PWD":/w -w /w node:22-slim sh scripts/build_ui.sh).
set -eu
cd "$(dirname "$0")/../frontend"
npm ci
npm run build
rm -rf ../vtt_server/static
cp -r dist ../vtt_server/static
echo "UI copied to vtt_server/static"

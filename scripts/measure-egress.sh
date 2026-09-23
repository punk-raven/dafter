#!/usr/bin/env bash
set -euo pipefail

ROOM="${1:?usage: measure-egress.sh <room-name>}"

LK_URL="${DAFTER_LIVEKIT_URL:-ws://127.0.0.1:7880}"
LK_API_KEY="${DAFTER_LIVEKIT_API_KEY:-devkey}"
LK_API_SECRET="${DAFTER_LIVEKIT_API_SECRET:-secret}"
MINIO_ALIAS="${MINIO_ALIAS:-local}"
BUCKET="dafter-recordings"

export LIVEKIT_URL="$LK_URL"
export LIVEKIT_API_KEY="$LK_API_KEY"
export LIVEKIT_API_SECRET="$LK_API_SECRET"

command -v lk >/dev/null 2>&1 || { echo "error: lk CLI not found. Install with: go install github.com/livekit/livekit-cli/cmd/lk@latest"; exit 1; }

mc_cmd() {
  docker compose exec -T minio mc "$@" 2>/dev/null
}

docker compose exec -T minio mc alias set "$MINIO_ALIAS" http://localhost:9000 minioadmin minioadmin >/dev/null 2>&1 || true

count_objects() {
  local prefix="$1"
  docker compose exec -T minio mc ls --recursive "${MINIO_ALIAS}/${BUCKET}/${prefix}" 2>/dev/null | wc -l || echo 0
}

wait_for_object() {
  local prefix="$1"
  local start_ms="$2"
  local timeout=60
  local elapsed=0

  while [ "$elapsed" -lt "$timeout" ]; do
    local n
    n=$(count_objects "$prefix")
    if [ "$n" -gt 0 ]; then
      local now_ms
      now_ms=$(date +%s%3N)
      local gap_ms=$(( now_ms - start_ms ))
      echo "$gap_ms"
      return 0
    fi
    sleep 0.5
    elapsed=$(( elapsed + 1 ))
  done
  echo "timeout"
  return 1
}

echo "=== Dafter POC: Egress capture-start gap measurement ==="
echo ""
echo "Room: $ROOM"
echo "LiveKit: $LK_URL"
echo ""

echo "--- Room composite egress ---"
prefix_composite="composite-${ROOM}-$(date +%s)"

start_ms=$(date +%s%3N)
lk egress start \
  --room "$ROOM" \
  --type room-composite \
  --filepath "${prefix_composite}/recording.mp4" \
  --output s3 \
  --s3-bucket "$BUCKET" \
  --s3-endpoint "http://127.0.0.1:9000" \
  --s3-access-key minioadmin \
  --s3-secret minioadmin \
  --s3-region us-east-1 \
  --s3-force-path-style 2>&1 | head -5

gap=$(wait_for_object "$prefix_composite" "$start_ms")
echo "Room composite capture-start gap: ${gap}ms"
echo ""

echo "--- Track egress ---"
prefix_track="track-${ROOM}-$(date +%s)"

start_ms=$(date +%s%3N)
lk egress start \
  --room "$ROOM" \
  --type track-composite \
  --filepath "${prefix_track}/recording.mp4" \
  --output s3 \
  --s3-bucket "$BUCKET" \
  --s3-endpoint "http://127.0.0.1:9000" \
  --s3-access-key minioadmin \
  --s3-secret minioadmin \
  --s3-region us-east-1 \
  --s3-force-path-style 2>&1 | head -5

gap=$(wait_for_object "$prefix_track" "$start_ms")
echo "Track egress capture-start gap: ${gap}ms"
echo ""

echo "=== Done. Check MinIO console at http://127.0.0.1:9001 ==="

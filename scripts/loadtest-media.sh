#!/usr/bin/env bash
set -euo pipefail

# Media-level load test: N concurrent video calls through the control plane
# and the LiveKit SFU.
#
# For every room the script creates a session on the control plane
# (POST /sessions) and joins the remaining participants (POST /sessions/{id}/join),
# so every token is minted by dafter and every room is a dafter room, then
# drives real WebRTC participants into that room with `lk load-test`: video
# publishers plus subscribers that receive every published track. All rooms
# are held concurrently for DURATION and the per-track packet loss and
# bitrate that lk reports are folded into one summary.
#
# lk mints its own join tokens from the API key: it has no flag to join with
# a supplied JWT, so the dafter-minted tokens are verified (signature-free
# decode of the room claim) but not used for the media connection.
#
# Requires: the dev stack (make dev), curl, and lk (livekit-cli) on PATH or
# in $HOME/go/bin. Everything else is bash and coreutils.
#
# Usage: ROOMS=100 DURATION=60s scripts/loadtest-media.sh
#
# Knobs (environment):
#   ROOMS             concurrent rooms                                (100)
#   DURATION          time every room is held once all are up         (60s)
#   PUBLISHERS        video publishers per room                       (2)
#   SUBSCRIBERS       subscribers per room, each receives all tracks  (2)
#   VIDEO_RESOLUTION  lk resolution: low, medium, high                (medium)
#   SIMULCAST         1 to publish simulcast layers                   (0)
#   LAYOUT            lk subscriber layout                            (speaker)
#   RAMP              rooms started per second                        (5)
#   DAFTER_URL        control plane                                   (http://127.0.0.1:8080)
#   LIVEKIT_URL       SFU signalling URL for the generator            (ws://127.0.0.1:7880)
#   LIVEKIT_API_KEY, LIVEKIT_API_SECRET                               (devkey / secret)
#   PROM_URL          Prometheus, for the live progress line          (http://127.0.0.1:9090)
#   TENANT, LANGUAGE, CHANNEL   session request fields                (t_9c21a4be, en-IN, webrtc)
#   OUT_DIR           per-room logs and the summary                   (./.loadtest-media/<timestamp>)

ROOMS=${ROOMS:-100}
DURATION=${DURATION:-60s}
PUBLISHERS=${PUBLISHERS:-2}
SUBSCRIBERS=${SUBSCRIBERS:-2}
VIDEO_RESOLUTION=${VIDEO_RESOLUTION:-medium}
SIMULCAST=${SIMULCAST:-0}
LAYOUT=${LAYOUT:-speaker}
RAMP=${RAMP:-5}
DAFTER_URL=${DAFTER_URL:-http://127.0.0.1:8080}
LIVEKIT_URL=${LIVEKIT_URL:-ws://127.0.0.1:7880}
LIVEKIT_API_KEY=${LIVEKIT_API_KEY:-devkey}
LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET:-secret}
PROM_URL=${PROM_URL:-http://127.0.0.1:9090}
TENANT=${TENANT:-t_9c21a4be}
LANGUAGE=${LANGUAGE:-en-IN}
CHANNEL=${CHANNEL:-webrtc}
OUT_DIR=${OUT_DIR:-.loadtest-media/$(date +%Y%m%d-%H%M%S)}

die() { printf 'loadtest-media: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Seconds from a Go-style duration: 60s, 2m, 1h30m, or a bare number.
duration_seconds() {
  local s=$1 total=0 n unit
  [[ $s =~ ^[0-9]+$ ]] && { echo "$s"; return; }
  while [[ $s =~ ^([0-9]+)([hms])(.*)$ ]]; do
    n=${BASH_REMATCH[1]} unit=${BASH_REMATCH[2]} s=${BASH_REMATCH[3]}
    case $unit in
      h) total=$((total + n * 3600)) ;;
      m) total=$((total + n * 60)) ;;
      s) total=$((total + n)) ;;
    esac
  done
  [ -z "$s" ] || die "cannot parse duration: $1"
  echo "$total"
}

# JSON field of a compact single-line object; the control plane's responses
# have no nested strings around the fields read here.
json_field() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" | head -n1; }

# Room claim of a JWT, from its payload, without verifying the signature.
jwt_room() {
  local payload=${1#*.}; payload=${payload%%.*}
  payload=$(printf '%s' "$payload" | tr '_-' '/+')
  case $(( ${#payload} % 4 )) in 2) payload+="==" ;; 3) payload+="=" ;; esac
  printf '%s' "$payload" | base64 -d 2>/dev/null | sed -n 's/.*"room":"\([^"]*\)".*/\1/p'
}

prom() {
  curl -fsS --max-time 2 -G "$PROM_URL/api/v1/query" --data-urlencode "query=$1" 2>/dev/null \
    | sed -n 's/.*"value":\[[0-9.]*,"\([^"]*\)"\].*/\1/p' | head -n1
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

LK=${LK_BIN:-}
if [ -z "$LK" ]; then
  for candidate in lk livekit-cli "$HOME/go/bin/lk" "$HOME/go/bin/livekit-cli"; do
    if have "$candidate" || [ -x "$candidate" ]; then LK=$candidate; break; fi
  done
fi
[ -n "$LK" ] || die "lk (livekit-cli) not found. Install it with:
    go install github.com/livekit/livekit-cli/v2/cmd/lk@v2.13.2
(newer tags need the portaudio submodule and do not build with go install)"
have curl || die "curl not found"
[ "$ROOMS" -ge 1 ] 2>/dev/null || die "ROOMS must be a positive integer"
[ "$PUBLISHERS" -ge 1 ] 2>/dev/null || die "PUBLISHERS must be at least 1"

duration_s=$(duration_seconds "$DURATION")
per_room=$((PUBLISHERS + SUBSCRIBERS))
total_participants=$((ROOMS * per_room))
expected_tracks_per_room=$((PUBLISHERS * SUBSCRIBERS))

if ! curl -fsS --max-time 3 -o /dev/null -X POST -H 'Content-Type: application/json' \
    -d "{\"tenantId\":\"$TENANT\",\"language\":\"$LANGUAGE\",\"channel\":\"$CHANNEL\"}" \
    "$DAFTER_URL/sessions"; then
  die "control plane at $DAFTER_URL does not accept POST /sessions; is the dev stack up (make dev)?"
fi

mkdir -p "$OUT_DIR"
OUT_DIR=$(cd "$OUT_DIR" && pwd)
rooms_tsv=$OUT_DIR/rooms.tsv
: >"$rooms_tsv"

echo "=== Dafter media load test ==="
echo "rooms:        $ROOMS x ($PUBLISHERS video publishers + $SUBSCRIBERS subscribers) = $total_participants participants"
echo "video:        $VIDEO_RESOLUTION, simulcast=$SIMULCAST, layout=$LAYOUT"
echo "hold:         $DURATION once every room is up (ramp $RAMP rooms/s)"
echo "control:      $DAFTER_URL"
echo "sfu:          $LIVEKIT_URL ($LK)"
echo "logs:         $OUT_DIR"
echo

# ---------------------------------------------------------------------------
# Phase 1: sessions and tokens through the control plane
# ---------------------------------------------------------------------------

create_body=$(printf '{"tenantId":"%s","language":"%s","channel":"%s"}' "$TENANT" "$LANGUAGE" "$CHANNEL")
create_failures=0 join_failures=0 tokens=0 bad_tokens=0
t0=$(date +%s.%N)
for ((i = 1; i <= ROOMS; i++)); do
  resp=$(curl -sS --max-time 5 -X POST -H 'Content-Type: application/json' -d "$create_body" "$DAFTER_URL/sessions") || resp=
  session=$(printf '%s' "$resp" | json_field sessionId)
  if [ -z "$session" ]; then
    create_failures=$((create_failures + 1))
    printf 'room %d: create failed: %s\n' "$i" "${resp:-no response}" >&2
    continue
  fi
  participant=$(printf '%s' "$resp" | json_field participantId)
  token=$(printf '%s' "$resp" | json_field token)
  tokens=$((tokens + 1))
  [ "$(jwt_room "$token")" = "$session" ] || bad_tokens=$((bad_tokens + 1))
  for ((j = 2; j <= per_room; j++)); do
    resp=$(curl -sS --max-time 5 -X POST -H 'Content-Type: application/json' -d '{"role":"participant"}' \
      "$DAFTER_URL/sessions/$session/join") || resp=
    token=$(printf '%s' "$resp" | json_field token)
    if [ -z "$token" ]; then
      join_failures=$((join_failures + 1))
      printf 'room %d (%s): join %d failed: %s\n' "$i" "$session" "$j" "${resp:-no response}" >&2
      continue
    fi
    tokens=$((tokens + 1))
    [ "$(jwt_room "$token")" = "$session" ] || bad_tokens=$((bad_tokens + 1))
  done
  printf '%s\t%s\n' "$session" "$participant" >>"$rooms_tsv"
done
t1=$(date +%s.%N)
rooms_created=$(wc -l <"$rooms_tsv")
printf 'control plane: %d/%d sessions created, %d tokens minted (%d joins failed, %d tokens with the wrong room) in %.1fs\n' \
  "$rooms_created" "$ROOMS" "$tokens" "$join_failures" "$bad_tokens" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')"
[ "$rooms_created" -gt 0 ] || die "no session could be created"

# ---------------------------------------------------------------------------
# Phase 2: media into every room
# ---------------------------------------------------------------------------

lk_args=(load-test --url "$LIVEKIT_URL" --api-key "$LIVEKIT_API_KEY" --api-secret "$LIVEKIT_API_SECRET"
  --video-publishers "$PUBLISHERS" --subscribers "$SUBSCRIBERS"
  --video-resolution "$VIDEO_RESOLUTION" --layout "$LAYOUT" --num-per-second 10)
[ "$SIMULCAST" = 1 ] || lk_args+=(--no-simulcast)

# Rooms start RAMP per second; each one's hold is stretched so that all of
# them end together, which makes the fully concurrent window exactly DURATION.
ramp_s=$(( (rooms_created + RAMP - 1) / RAMP ))
end_at=$(( $(date +%s) + ramp_s + duration_s ))

pids=()
cleanup() {
  if [ ${#pids[@]} -gt 0 ]; then
    kill "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "starting $rooms_created rooms ($RAMP/s, all up in ~${ramp_s}s, then held ${duration_s}s)"
n=0
while IFS=$'\t' read -r session participant; do
  n=$((n + 1))
  hold=$(( end_at - $(date +%s) ))
  [ "$hold" -ge "$duration_s" ] || hold=$duration_s
  "$LK" "${lk_args[@]}" --room "$session" --identity-prefix "$participant" --duration "${hold}s" \
    >"$OUT_DIR/$session.log" 2>&1 &
  pids+=($!)
  if (( n % RAMP == 0 )); then sleep 1; fi
done <"$rooms_tsv"

# Progress: generator processes alive, SFU gauges from Prometheus, and the
# livekit container from docker stats. Peaks feed the summary.
peak_rooms=0 peak_participants=0 peak_subscribed=0 peak_cpu=0 peak_gen_rss=0 peak_gen_cpu=0
docker_stats=
started=$(date +%s)
while :; do
  alive=0
  for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive=$((alive + 1)); done
  [ "$alive" -gt 0 ] || break
  elapsed=$(( $(date +%s) - started ))
  rooms_now=$(prom 'livekit_room_total'); participants_now=$(prom 'livekit_participant_total')
  subscribed_now=$(prom 'sum(livekit_track_subscribed_total)')
  cpu_now=$(prom 'rate(process_cpu_seconds_total{job="livekit"}[30s]) * 100')
  gen=$(ps -o rss=,pcpu= -p "$(IFS=,; echo "${pids[*]}")" 2>/dev/null | awk '{r+=$1; c+=$2} END {printf "%d %.0f", r/1024, c}')
  gen_rss=${gen% *}; gen_cpu=${gen#* }
  printf '[%4ds] generators=%d/%d  sfu rooms=%s participants=%s subscribed=%s cpu=%s%%  generator rss=%sMB cpu=%s%%\n' \
    "$elapsed" "$alive" "$rooms_created" "${rooms_now:--}" "${participants_now:--}" "${subscribed_now:--}" \
    "${cpu_now:+$(printf '%.0f' "$cpu_now")}" "$gen_rss" "$gen_cpu"
  peak_rooms=$(awk -v a="$peak_rooms" -v b="${rooms_now:-0}" 'BEGIN{print (b>a)?b:a}')
  peak_participants=$(awk -v a="$peak_participants" -v b="${participants_now:-0}" 'BEGIN{print (b>a)?b:a}')
  peak_subscribed=$(awk -v a="$peak_subscribed" -v b="${subscribed_now:-0}" 'BEGIN{print (b>a)?b:a}')
  peak_cpu=$(awk -v a="$peak_cpu" -v b="${cpu_now:-0}" 'BEGIN{printf "%.0f", (b>a)?b:a}')
  peak_gen_rss=$(( gen_rss > peak_gen_rss ? gen_rss : peak_gen_rss ))
  peak_gen_cpu=$(( gen_cpu > peak_gen_cpu ? gen_cpu : peak_gen_cpu ))
  # One docker stats sample in the middle of the fully concurrent window.
  if [ -z "$docker_stats" ] && [ "$elapsed" -ge $((ramp_s + duration_s / 2)) ] && have docker; then
    docker_stats=$(docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} net={{.NetIO}}' 2>/dev/null \
      | grep -E 'livekit|control' || true)
  fi
  sleep 5
done
trap - EXIT INT TERM

# ---------------------------------------------------------------------------
# Summary, from what lk reported per room
# ---------------------------------------------------------------------------

# lk prints two lipgloss tables per room: "Track loading" (one row per
# subscribed track: tester, track, kind, packets, bitrate, loss) and
# "Subscriber summaries" (one row per subscriber plus a Total row). Borders
# and ANSI colour are stripped, then the rows are parsed on the "|" columns.
parse_log() {
  sed -e 's/\x1b\[[0-9;]*m//g' -e 's/│/|/g' -e 's/[┌┐└┘├┤┬┴┼─]//g' "$1"
}

rooms_ok=0 rooms_partial=0 rooms_failed=0 connect_failures=0 sub_failures=0
tracks_subscribed=0 tracks_expected=0 packets=0 dropped=0 bitrate_bps=0
track_rows=$OUT_DIR/tracks.tsv
: >"$track_rows"
while IFS=$'\t' read -r session participant; do
  log=$OUT_DIR/$session.log
  cf=$(grep -c 'could not connect' "$log" || true)
  sf=$(grep -c 'track subscription failed' "$log" || true)
  connect_failures=$((connect_failures + cf)); sub_failures=$((sub_failures + sf))
  total=$(parse_log "$log" | awk -F'|' '$2 ~ /^ *Total *$/ {gsub(/ /,"",$3); gsub(/ /,"",$5); print $3, $5; exit}')
  if [ -z "$total" ]; then
    rooms_failed=$((rooms_failed + 1))
    printf '%s\tfailed\t-\t-\t%s\n' "$session" "$(grep -m1 -E 'could not connect|error|panic' "$log" || echo 'no summary')" >>"$OUT_DIR/failed.tsv"
    continue
  fi
  got=${total% *}; got=${got%%/*}; want=${total% *}; want=${want##*/}
  tracks_subscribed=$((tracks_subscribed + got)); tracks_expected=$((tracks_expected + want))
  if [ "$got" -eq "$want" ] && [ "$cf" -eq 0 ]; then rooms_ok=$((rooms_ok + 1)); else rooms_partial=$((rooms_partial + 1)); fi
  # Per-track rows: packets, bitrate (normalised to bps), dropped.
  parse_log "$log" | awk -F'|' -v room="$session" '
    NF >= 7 && $4 ~ /video|audio/ {
      pk=$5; gsub(/ /,"",pk); br=$6; gsub(/ /,"",br); lost=$7; sub(/ *\(.*/,"",lost); gsub(/ /,"",lost)
      bps=br; if (br ~ /mbps/) { sub(/mbps/,"",bps); bps*=1000000 } else if (br ~ /kbps/) { sub(/kbps/,"",bps); bps*=1000 } else { sub(/bps/,"",bps) }
      printf "%s\t%s\t%s\t%d\t%d\t%d\n", room, $3, $4, pk, bps, lost
    }' | tr -d ' ' >>"$track_rows"
done <"$rooms_tsv"

if [ -s "$track_rows" ]; then
  read -r packets dropped bitrate_bps min_bps max_bps max_loss_pct < <(awk -F'\t' '
    { p+=$4; d+=$6; b+=$5; if (min=="" || $5<min) min=$5; if ($5>max) max=$5
      if ($4+$6 > 0) { l=100*$6/($4+$6); if (l>ml) ml=l } }
    END { printf "%d %d %d %d %d %.3f\n", p, d, b, min, max, ml }' "$track_rows")
else
  min_bps=0 max_bps=0 max_loss_pct=0
fi
loss_pct=$(awk -v p="$packets" -v d="$dropped" 'BEGIN { if (p+d>0) printf "%.3f", 100*d/(p+d); else print "0" }')
participants_connected=$(( rooms_created * per_room - connect_failures ))
fmt_bps() { awk -v b="$1" 'BEGIN { if (b>=1000000) printf "%.2f Mbps", b/1000000; else printf "%.1f kbps", b/1000 }'; }

{
  echo
  echo "=== Media load test summary ==="
  printf 'rooms:                 %d requested, %d created on the control plane, %d fully up, %d partial, %d failed\n' \
    "$ROOMS" "$rooms_created" "$rooms_ok" "$rooms_partial" "$rooms_failed"
  printf 'participants:          %d connected of %d (%d connect failures)\n' "$participants_connected" "$total_participants" "$connect_failures"
  printf 'tracks:                %d video published, %d/%d subscribed (%d subscription failures)\n' \
    "$((rooms_created * PUBLISHERS))" "$tracks_subscribed" "$tracks_expected" "$sub_failures"
  printf 'packets:               %d received, %d lost, %s%% loss overall, worst track %s%%\n' "$packets" "$dropped" "$loss_pct" "$max_loss_pct"
  printf 'bitrate (subscribed):  %s total, per track min %s, max %s\n' "$(fmt_bps "$bitrate_bps")" "$(fmt_bps "$min_bps")" "$(fmt_bps "$max_bps")"
  printf 'control plane:         %d create failures, %d join failures, %d tokens minted, %d with the wrong room\n' \
    "$create_failures" "$join_failures" "$tokens" "$bad_tokens"
  printf 'sfu peak (prometheus): rooms=%s participants=%s subscribed tracks=%s cpu=%s%% (100 = one core)\n' \
    "$peak_rooms" "$peak_participants" "$peak_subscribed" "$peak_cpu"
  printf 'generator peak:        %d lk processes, rss=%dMB, cpu=%d%% (100 = one core)\n' "$rooms_created" "$peak_gen_rss" "$peak_gen_cpu"
  if [ -n "$docker_stats" ]; then
    echo "docker stats (mid-run):"
    printf '  %s\n' "$docker_stats"
  fi
  echo "per-room logs:         $OUT_DIR"
  if [ -s "$OUT_DIR/failed.tsv" ]; then
    echo "failed rooms:"
    cut -f1,5 "$OUT_DIR/failed.tsv" | sed 's/^/  /'
  fi
} | tee "$OUT_DIR/summary.txt"

[ "$rooms_ok" -eq "$ROOMS" ] && [ "$connect_failures" -eq 0 ]

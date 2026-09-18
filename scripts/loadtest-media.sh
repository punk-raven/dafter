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
# decode of the room claim) but not used for the media connection. lk's
# publishers do not subscribe; every subscriber receives every published
# track in its room, which is the equivalent of the participants of a call
# watching each other.
#
# The generator, the SFU and the whole docker stack share one box, so the
# script refuses to start with less than MEM_MIN_MB available and aborts the
# run, killing every lk process, if MemAvailable falls under MEM_FLOOR_MB.
# RAMP runs a series of sizes in turn, tearing down between them, and stops
# at the first size that fails: this finds the ceiling of a box without
# guessing at it.
#
# Requires: the dev stack (make dev), curl, and lk (livekit-cli) on PATH or
# in $HOME/go/bin. Everything else is bash and coreutils.
#
# Usage: ROOMS=100 DURATION=60s scripts/loadtest-media.sh
#        RAMP="10 25 50" scripts/loadtest-media.sh
#
# Knobs (environment):
#   ROOMS             concurrent rooms                                (100)
#   RAMP              sizes to run in turn instead of ROOMS, e.g. "10 25 50"
#   DURATION          time every room is held once all are up         (60s)
#   PUBLISHERS        video publishers per room                       (1)
#   SUBSCRIBERS       subscribers per room, each receives all tracks  (1)
#   VIDEO_RESOLUTION  lk resolution: low, medium, high                (low)
#   SIMULCAST         1 to publish simulcast layers                   (0)
#   LAYOUT            lk subscriber layout                            (speaker)
#   ROOMS_PER_SECOND  lk processes launched per second                (2)
#   LOSS_MAX_PCT      overall packet loss above which a size fails    (5)
#   MEM_MIN_MB        MemAvailable required to start a size           (3072)
#   MEM_FLOOR_MB      MemAvailable under which the run is aborted     (1500)
#   DAFTER_URL        control plane                                   (http://127.0.0.1:8080)
#   LIVEKIT_URL       SFU signalling URL for the generator            (ws://127.0.0.1:7880)
#   LIVEKIT_API_KEY, LIVEKIT_API_SECRET                               (devkey / secret)
#   PROM_URL          Prometheus, for the live progress line          (http://127.0.0.1:9090)
#   TENANT, LANGUAGE, CHANNEL   session request fields                (t_9c21a4be, en-IN, webrtc)
#   OUT_DIR           per-room logs and the summaries                 (./.loadtest-media/<timestamp>)
#
# Exit status: 0 when every size held cleanly, 1 when a size failed,
# 2 when the memory floor aborted the run.

ROOMS=${ROOMS:-100}
RAMP=${RAMP:-}
DURATION=${DURATION:-60s}
PUBLISHERS=${PUBLISHERS:-1}
SUBSCRIBERS=${SUBSCRIBERS:-1}
VIDEO_RESOLUTION=${VIDEO_RESOLUTION:-low}
SIMULCAST=${SIMULCAST:-0}
LAYOUT=${LAYOUT:-speaker}
ROOMS_PER_SECOND=${ROOMS_PER_SECOND:-2}
LOSS_MAX_PCT=${LOSS_MAX_PCT:-5}
MEM_MIN_MB=${MEM_MIN_MB:-3072}
MEM_FLOOR_MB=${MEM_FLOOR_MB:-1500}
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

mem_available_mb() { awk '/^MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo; }

# CPU ticks (user+system) consumed so far by the given pids, from /proc.
cpu_ticks() {
  local pid t=0 f
  for pid in "$@"; do
    [ -r "/proc/$pid/stat" ] || continue
    read -r -a f <"/proc/$pid/stat" 2>/dev/null || continue
    t=$((t + f[13] + f[14]))
  done
  echo "$t"
}
# Busy and total ticks of the whole box, from /proc/stat.
host_ticks() { awk '/^cpu / {print $2+$3+$4+$6+$7+$8, $2+$3+$4+$5+$6+$7+$8}' /proc/stat; }
CLK_TCK=$(getconf CLK_TCK 2>/dev/null || echo 100)

fmt_bps() { awk -v b="$1" 'BEGIN { if (b>=1000000) printf "%.2f Mbps", b/1000000; else printf "%.1f kbps", b/1000 }'; }

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
[ "$PUBLISHERS" -ge 1 ] 2>/dev/null || die "PUBLISHERS must be at least 1"
[ "$SUBSCRIBERS" -ge 1 ] 2>/dev/null || die "SUBSCRIBERS must be at least 1"
[ "$ROOMS_PER_SECOND" -ge 1 ] 2>/dev/null || die "ROOMS_PER_SECOND must be at least 1"

if [ -n "$RAMP" ]; then
  read -r -a sizes <<<"$RAMP"
else
  sizes=("$ROOMS")
fi
for size in "${sizes[@]}"; do
  [ "$size" -ge 1 ] 2>/dev/null || die "room count must be a positive integer: $size"
done

duration_s=$(duration_seconds "$DURATION")
per_room=$((PUBLISHERS + SUBSCRIBERS))

if ! curl -fsS --max-time 3 -o /dev/null -X POST -H 'Content-Type: application/json' \
    -d "{\"tenantId\":\"$TENANT\",\"language\":\"$LANGUAGE\",\"channel\":\"$CHANNEL\"}" \
    "$DAFTER_URL/sessions"; then
  die "control plane at $DAFTER_URL does not accept POST /sessions; is the dev stack up (make dev)?"
fi

mkdir -p "$OUT_DIR"
OUT_DIR=$(cd "$OUT_DIR" && pwd)

echo "=== Dafter media load test ==="
echo "sizes:        ${sizes[*]} rooms x ($PUBLISHERS video publishers + $SUBSCRIBERS subscribers)"
echo "video:        $VIDEO_RESOLUTION, simulcast=$SIMULCAST, layout=$LAYOUT"
echo "hold:         $DURATION once every room is up (launching $ROOMS_PER_SECOND rooms/s)"
echo "limits:       loss <= $LOSS_MAX_PCT%, start with >= ${MEM_MIN_MB}MB available, abort under ${MEM_FLOOR_MB}MB"
echo "control:      $DAFTER_URL"
echo "sfu:          $LIVEKIT_URL ($LK)"
echo "logs:         $OUT_DIR"

# ---------------------------------------------------------------------------
# One size: sessions through the control plane, media into every room,
# then a summary from what lk reported per room.
# ---------------------------------------------------------------------------

pids=()
kill_generators() {
  if [ ${#pids[@]} -gt 0 ]; then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
    pids=()
  fi
}
trap kill_generators EXIT INT TERM

abort_memory() {
  echo
  echo "ABORTED: memory floor (MemAvailable ${1}MB < ${MEM_FLOOR_MB}MB), killing every lk process" | tee -a "$run_dir/summary.txt"
  kill_generators
  exit 2
}

# Sets size_ok (1/0) and size_reason; writes $run_dir/summary.txt.
run_size() {
  local rooms=$1
  run_dir=$OUT_DIR/$rooms
  mkdir -p "$run_dir"
  local rooms_tsv=$run_dir/rooms.tsv
  : >"$rooms_tsv"
  local total_participants=$((rooms * per_room))

  echo
  echo "=== $rooms rooms x ($PUBLISHERS + $SUBSCRIBERS) = $total_participants participants ==="

  local mem
  mem=$(mem_available_mb)
  if [ "$mem" -lt "$MEM_MIN_MB" ]; then
    size_ok=0 size_reason="only ${mem}MB available before start, need ${MEM_MIN_MB}MB"
    echo "skipped: $size_reason" | tee "$run_dir/summary.txt"
    return
  fi

  # Phase 1: sessions and tokens through the control plane.
  local create_body create_failures=0 join_failures=0 tokens=0 bad_tokens=0 t0 t1
  create_body=$(printf '{"tenantId":"%s","language":"%s","channel":"%s"}' "$TENANT" "$LANGUAGE" "$CHANNEL")
  t0=$(date +%s.%N)
  local i j resp session participant token
  for ((i = 1; i <= rooms; i++)); do
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
  local rooms_created
  rooms_created=$(wc -l <"$rooms_tsv")
  printf 'control plane: %d/%d sessions created, %d tokens minted (%d joins failed, %d tokens with the wrong room) in %.1fs\n' \
    "$rooms_created" "$rooms" "$tokens" "$join_failures" "$bad_tokens" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')"
  if [ "$rooms_created" -eq 0 ]; then
    size_ok=0 size_reason="no session could be created"
    echo "failed: $size_reason" | tee "$run_dir/summary.txt"
    return
  fi

  # Phase 2: media into every room.
  local lk_args=(load-test --url "$LIVEKIT_URL" --api-key "$LIVEKIT_API_KEY" --api-secret "$LIVEKIT_API_SECRET"
    --video-publishers "$PUBLISHERS" --subscribers "$SUBSCRIBERS"
    --video-resolution "$VIDEO_RESOLUTION" --layout "$LAYOUT" --num-per-second 10)
  [ "$SIMULCAST" = 1 ] || lk_args+=(--no-simulcast)

  # Rooms launch ROOMS_PER_SECOND per second; each one's hold is stretched so
  # that all of them end together, which makes the fully concurrent window
  # exactly DURATION.
  local ramp_s end_at n hold
  ramp_s=$(( (rooms_created + ROOMS_PER_SECOND - 1) / ROOMS_PER_SECOND ))
  end_at=$(( $(date +%s) + ramp_s + duration_s ))
  echo "starting $rooms_created rooms ($ROOMS_PER_SECOND/s, all up in ~${ramp_s}s, then held ${duration_s}s)"
  n=0
  while IFS=$'\t' read -r session participant; do
    n=$((n + 1))
    hold=$(( end_at - $(date +%s) ))
    [ "$hold" -ge "$duration_s" ] || hold=$duration_s
    "$LK" "${lk_args[@]}" --room "$session" --identity-prefix "$participant" --duration "${hold}s" \
      >"$run_dir/$session.log" 2>&1 &
    pids+=($!)
    if (( n % ROOMS_PER_SECOND == 0 )); then
      sleep 1
      mem=$(mem_available_mb)
      [ "$mem" -ge "$MEM_FLOOR_MB" ] || abort_memory "$mem"
    fi
  done <"$rooms_tsv"

  # Progress: generator processes alive, SFU gauges from Prometheus, CPU of
  # the lk processes and of the box from /proc, the livekit container from
  # docker stats. Memory is checked every 2s; the line prints every 10s.
  local peak_rooms=0 peak_participants=0 peak_subscribed=0 peak_sfu_cpu=0
  local peak_gen_rss=0 peak_gen_cpu=0 peak_host_cpu=0 min_mem=999999 docker_stats= tick=0
  local started alive pid elapsed rooms_now participants_now subscribed_now sfu_cpu
  local gen_rss gen_cpu host_cpu prev_gen prev_host cur_gen cur_host prev_t cur_t
  started=$(date +%s)
  prev_gen=$(cpu_ticks "${pids[@]}"); prev_host=$(host_ticks); prev_t=$started
  while :; do
    alive=0
    for pid in "${pids[@]}"; do kill -0 "$pid" 2>/dev/null && alive=$((alive + 1)); done
    [ "$alive" -gt 0 ] || break
    mem=$(mem_available_mb)
    [ "$mem" -lt "$min_mem" ] && min_mem=$mem
    [ "$mem" -ge "$MEM_FLOOR_MB" ] || abort_memory "$mem"
    if (( tick % 5 == 0 )); then
      elapsed=$(( $(date +%s) - started ))
      rooms_now=$(prom 'livekit_room_total'); participants_now=$(prom 'livekit_participant_total')
      subscribed_now=$(prom 'sum(livekit_track_subscribed_total)')
      sfu_cpu=$(prom 'rate(process_cpu_seconds_total{job="livekit"}[30s]) * 100')
      cur_t=$(date +%s); cur_gen=$(cpu_ticks "${pids[@]}"); cur_host=$(host_ticks)
      gen_cpu=$(awk -v a="$prev_gen" -v b="$cur_gen" -v dt="$((cur_t - prev_t))" -v hz="$CLK_TCK" \
        'BEGIN { if (dt>0) printf "%d", 100*(b-a)/hz/dt; else print 0 }')
      host_cpu=$(awk -v p="$prev_host" -v c="$cur_host" 'BEGIN {
        split(p,a," "); split(c,b," "); dt=b[2]-a[2]; if (dt>0) printf "%d", 100*(b[1]-a[1])/dt; else print 0 }')
      prev_gen=$cur_gen prev_host=$cur_host prev_t=$cur_t
      gen_rss=$(ps -o rss= -p "$(IFS=,; echo "${pids[*]}")" 2>/dev/null | awk '{r+=$1} END {printf "%d", r/1024}')
      printf '[%4ds] lk=%d/%d  sfu rooms=%s participants=%s subscribed=%s cpu=%s%%  lk cpu=%s%% rss=%sMB  host cpu=%s%% mem_avail=%sMB\n' \
        "$elapsed" "$alive" "$rooms_created" "${rooms_now:--}" "${participants_now:--}" "${subscribed_now:--}" \
        "${sfu_cpu:+$(printf '%.0f' "$sfu_cpu")}" "$gen_cpu" "$gen_rss" "$host_cpu" "$mem"
      peak_rooms=$(awk -v a="$peak_rooms" -v b="${rooms_now:-0}" 'BEGIN{print (b>a)?b:a}')
      peak_participants=$(awk -v a="$peak_participants" -v b="${participants_now:-0}" 'BEGIN{print (b>a)?b:a}')
      peak_subscribed=$(awk -v a="$peak_subscribed" -v b="${subscribed_now:-0}" 'BEGIN{print (b>a)?b:a}')
      peak_sfu_cpu=$(awk -v a="$peak_sfu_cpu" -v b="${sfu_cpu:-0}" 'BEGIN{printf "%.0f", (b>a)?b:a}')
      peak_gen_rss=$(( gen_rss > peak_gen_rss ? gen_rss : peak_gen_rss ))
      peak_gen_cpu=$(( gen_cpu > peak_gen_cpu ? gen_cpu : peak_gen_cpu ))
      peak_host_cpu=$(( host_cpu > peak_host_cpu ? host_cpu : peak_host_cpu ))
      # One docker stats sample in the middle of the fully concurrent window.
      if [ -z "$docker_stats" ] && [ "$elapsed" -ge $((ramp_s + duration_s / 2)) ] && have docker; then
        docker_stats=$(docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} net={{.NetIO}}' 2>/dev/null \
          | grep -E 'livekit|control' || true)
      fi
    fi
    tick=$((tick + 1))
    sleep 2
  done
  pids=()

  # Summary. lk prints two lipgloss tables per room: "Track loading" (one row
  # per subscribed track: tester, track, kind, packets, bitrate, loss) and
  # "Subscriber summaries" (one row per subscriber plus a Total row). Borders
  # and ANSI colour are stripped, then the rows are parsed on the "|" columns.
  parse_log() {
    sed -e 's/\x1b\[[0-9;]*m//g' -e 's/│/|/g' -e 's/[┌┐└┘├┤┬┴┼─]//g' "$1"
  }
  local rooms_ok=0 rooms_partial=0 rooms_failed=0 connect_failures=0 sub_failures=0
  local tracks_subscribed=0 tracks_expected=0 packets=0 dropped=0 bitrate_bps=0 min_bps=0 max_bps=0 max_loss_pct=0
  local track_rows=$run_dir/tracks.tsv log cf sf total got want
  : >"$track_rows"
  rm -f "$run_dir/failed.tsv"
  while IFS=$'\t' read -r session participant; do
    log=$run_dir/$session.log
    cf=$(grep -c 'could not connect' "$log" || true)
    sf=$(grep -c 'track subscription failed' "$log" || true)
    connect_failures=$((connect_failures + cf)); sub_failures=$((sub_failures + sf))
    total=$(parse_log "$log" | awk -F'|' '$2 ~ /^ *Total *$/ {gsub(/ /,"",$3); gsub(/ /,"",$5); print $3, $5; exit}')
    if [ -z "$total" ]; then
      rooms_failed=$((rooms_failed + 1))
      printf '%s\tfailed\t-\t-\t%s\n' "$session" "$(grep -m1 -E 'could not connect|error|panic' "$log" || echo 'no summary')" >>"$run_dir/failed.tsv"
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
  fi
  local loss_pct participants_connected
  loss_pct=$(awk -v p="$packets" -v d="$dropped" 'BEGIN { if (p+d>0) printf "%.3f", 100*d/(p+d); else print "0" }')
  participants_connected=$(( rooms_created * per_room - connect_failures ))

  size_ok=1 size_reason="held ${duration_s}s"
  if [ "$rooms_ok" -ne "$rooms" ] || [ "$connect_failures" -ne 0 ]; then
    size_ok=0 size_reason="$rooms_ok/$rooms rooms fully up, $connect_failures connect failures"
  elif awk -v l="$loss_pct" -v m="$LOSS_MAX_PCT" 'BEGIN { exit !(l > m) }'; then
    size_ok=0 size_reason="packet loss $loss_pct% > $LOSS_MAX_PCT%"
  fi

  {
    echo
    echo "=== Media load test summary: $rooms rooms ==="
    printf 'result:                %s (%s)\n' "$([ "$size_ok" = 1 ] && echo PASS || echo FAIL)" "$size_reason"
    printf 'rooms:                 %d requested, %d created on the control plane, %d fully up, %d partial, %d failed\n' \
      "$rooms" "$rooms_created" "$rooms_ok" "$rooms_partial" "$rooms_failed"
    printf 'participants:          %d connected of %d (%d connect failures)\n' "$participants_connected" "$total_participants" "$connect_failures"
    printf 'tracks:                %d video published, %d/%d subscribed (%d subscription failures)\n' \
      "$((rooms_created * PUBLISHERS))" "$tracks_subscribed" "$tracks_expected" "$sub_failures"
    printf 'packets:               %d received, %d lost, %s%% loss overall, worst track %s%%\n' "$packets" "$dropped" "$loss_pct" "$max_loss_pct"
    printf 'bitrate (subscribed):  %s total, per track min %s, max %s\n' "$(fmt_bps "$bitrate_bps")" "$(fmt_bps "$min_bps")" "$(fmt_bps "$max_bps")"
    printf 'control plane:         %d create failures, %d join failures, %d tokens minted, %d with the wrong room\n' \
      "$create_failures" "$join_failures" "$tokens" "$bad_tokens"
    printf 'sfu peak (prometheus): rooms=%s participants=%s subscribed tracks=%s cpu=%s%% (100 = one core)\n' \
      "$peak_rooms" "$peak_participants" "$peak_subscribed" "$peak_sfu_cpu"
    printf 'generator peak:        %d lk processes, rss=%dMB, cpu=%d%% (100 = one core)\n' "$rooms_created" "$peak_gen_rss" "$peak_gen_cpu"
    printf 'host peak:             cpu=%d%% of all cores, lowest MemAvailable %dMB\n' "$peak_host_cpu" "$min_mem"
    if [ -n "$docker_stats" ]; then
      echo "docker stats (mid-run):"
      printf '  %s\n' "$docker_stats"
    fi
    echo "per-room logs:         $run_dir"
    if [ -s "$run_dir/failed.tsv" ]; then
      echo "failed rooms:"
      cut -f1,5 "$run_dir/failed.tsv" | sed 's/^/  /'
    fi
  } | tee "$run_dir/summary.txt"
}

# Between sizes: close this size's rooms on the SFU so the next size starts
# from an empty server and the SFU gauges in Grafana show one size at a time.
teardown_size() {
  local rooms_tsv=$run_dir/rooms.tsv waited=0 p
  [ -s "$rooms_tsv" ] || return 0
  cut -f1 "$rooms_tsv" | xargs -P 8 -I{} "$LK" room delete --url "$LIVEKIT_URL" \
    --api-key "$LIVEKIT_API_KEY" --api-secret "$LIVEKIT_API_SECRET" {} >/dev/null 2>&1 || true
  while [ "$waited" -lt 30 ]; do
    p=$(prom 'livekit_participant_total')
    [ -n "$p" ] && [ "${p%.*}" -eq 0 ] 2>/dev/null && break
    sleep 2; waited=$((waited + 2))
  done
}

# ---------------------------------------------------------------------------
# Run every size in turn; stop at the first that does not hold.
# ---------------------------------------------------------------------------

ceiling=0 failed_size= results=()
for size in "${sizes[@]}"; do
  run_size "$size"
  results+=("$size: $([ "$size_ok" = 1 ] && echo PASS || echo FAIL) ($size_reason)")
  if [ "$size_ok" = 1 ]; then
    ceiling=$size
  else
    failed_size=$size
  fi
  [ "$size" = "${sizes[-1]}" ] || teardown_size
  [ "$size_ok" = 1 ] || break
done

{
  echo
  echo "=== Ramp result ==="
  printf '  %s\n' "${results[@]}"
  if [ -z "$failed_size" ]; then
    echo "every size held: ceiling >= $ceiling rooms x ($PUBLISHERS + $SUBSCRIBERS) with $VIDEO_RESOLUTION video"
  else
    echo "ceiling: $ceiling rooms x ($PUBLISHERS + $SUBSCRIBERS) with $VIDEO_RESOLUTION video; $failed_size rooms failed"
  fi
  echo "logs: $OUT_DIR"
} | tee "$OUT_DIR/ramp.txt"

[ -z "$failed_size" ]

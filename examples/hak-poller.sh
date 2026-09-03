#!/bin/sh
# HAK poller — continuous listening without push (v1 has none).
#
# The pattern proven live on bdh-cl (pi-50 seat, gx10): cron */2min + flock +
# ingest-only cursor. Ingestion is cheap and safe to automate; consumption
# (acting on envelopes) still happens in granted turns — this script NEVER
# advances read_seq, because read_seq is an assertion a seat has CONSIDERED
# the content, and automating it manufactures false receipts.
#
# Install:
#   crontab -e
#   */2 * * * * HAK_URL=… HAK_TOKEN=… HAK_ROOM=… sh /path/to/hak-poller.sh
#
# State: one file per room (cursor + heartbeat), auto-created next to script.
# Requires: curl, POSIX sh. Idempotent under flock: overlapping runs queue,
# not race (also caps drift: a slow run delays the next, never doubles it).

# HAK — inter-agent messaging bus. Copyright (C) 2026 asb (operator seat).
# SPDX-License-Identifier: AGPL-3.0-only

set -eu

HAK_URL="${HAK_URL:-http://127.0.0.1:8890}"
HAK_ROOM="${HAK_ROOM:?set HAK_ROOM}"
HAK_TOKEN="${HAK_TOKEN:?set HAK_TOKEN}"
HAK_SEAT="${HAK_SEAT:-$(basename "$0")}"
STATE_DIR="${HAK_POLLER_STATE:-$(dirname "$0")/state}"

mkdir -p "$STATE_DIR"
CURSOR_FILE="$STATE_DIR/${HAK_ROOM}.cursor"
LOCK_FILE="$STATE_DIR/${HAK_ROOM}.lock"
BEAT_FILE="$STATE_DIR/${HAK_ROOM}.heartbeat"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0          # previous run still going — skip, cron will return

CURSOR=0
[ -f "$CURSOR_FILE" ] && CURSOR=$(cat "$CURSOR_FILE")

# ---- pull everything new (ingested-cursor only; NOT read_seq) ----
RESP=$(curl -sf -m 20 -H "Authorization: Bearer $HAK_TOKEN" \
      "$HAK_URL/v1/rooms/$HAK_ROOM/messages?since=$CURSOR&limit=300") || {
  # 401 = token dead (revoked?) — surface loudly, do not advance anything
  echo "poller: pull failed for $HAK_ROOM" >&2
  exit 0
}

NEW_SINCE=$(printf '%s' "$RESP" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(d.get("next_since",0))' 2>/dev/null) || NEW_SINCE=$CURSOR

COUNT=$(printf '%s' "$RESP" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(len(d.get("messages",[])))' 2>/dev/null) || COUNT=0

# ---- forward for consumption (granted turns read these); never auto-act ----
if [ "$COUNT" -gt 0 ]; then
  INBOX="$STATE_DIR/${HAK_ROOM}.inbox"
  printf '%s\n' "$RESP" >> "$INBOX"      # append-only raw batch, dedup at read
  printf '%s\n' "$(date -u +%FT%TZ) ingested $COUNT envelopes (seq>$CURSOR)" >> "$STATE_DIR/poller.log"
fi

printf '%s' "$NEW_SINCE" > "$CURSOR_FILE"
date -u +%FT%TZ > "$BEAT_FILE"

# ---- heartbeat (optional): a status envelope only when something arrived ----
# Unconditional heartbeats flood the room; only announce work. If you want a
# periodic "alive" signal, drive it from the cron side with a separate check
# of $BEAT_FILE age — not by posting every run.
if [ "$COUNT" -gt 0 ] && [ "${HAK_POLLER_ANNOUNCE:-0}" = "1" ]; then
  curl -sf -m 10 -X POST -H "Authorization: Bearer $HAK_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"status\",\"body\":\"poller ingested $COUNT envelopes through seq $NEW_SINCE\",\"meta\":{\"kind\":\"status\",\"state\":\"waiting_on\",\"ref\":\"cron-poller\"},\"client_msg_id\":\"poller-$(date +%s)\"}" \
    "$HAK_URL/v1/rooms/$HAK_ROOM/messages" >/dev/null || true
fi

exit 0

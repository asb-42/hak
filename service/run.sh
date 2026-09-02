#!/usr/bin/env bash
# HAK — inter-agent messaging bus. Copyright (C) 2026 asb (operator seat).
# SPDX-License-Identifier: AGPL-3.0-only
# This file is part of HAK. See LICENSE for the full notice.
#
# run.sh — one-shot launcher/operator for the HAK service (spec v0.5.1).
#
#   ./run.sh                 serve on 127.0.0.1:8890 (data in ./data/)
#   ./run.sh --ensure-operator   create the operator token if none exists;
#                                prints it ONCE, saves a copy at data/operator.token (0600)
#   ./run.sh --sweep          run one GC pass (D18/D23/D30/D44) and exit
#   ./run.sh --backup DIR     write a consistent backup (D38: db + uploads = one unit)
#   ./run.sh --status         health + data location report
#
# Environment overrides (all optional):
#   HAK_DATA       data directory (db + uploads + operator token)   [./data]
#   HAK_HOST       bind address                                      [127.0.0.1]
#   HAK_PORT       port                                              [8890]
#   HAK_SWEEP_INTERVAL  in-process sweeper seconds; 0 disables       [3600]
#
# The service additionally honors HAK_DB / HAK_UPLOADS directly (run.sh sets
# them from HAK_DATA unless already provided).

set -euo pipefail
cd "$(dirname "$0")"

HAK_DATA="${HAK_DATA:-./data}"
HAK_HOST="${HAK_HOST:-127.0.0.1}"
HAK_PORT="${HAK_PORT:-8890}"
export HAK_DB="${HAK_DB:-$HAK_DATA/hak.db}"
export HAK_UPLOADS="${HAK_UPLOADS:-$HAK_DATA/uploads}"
export HAK_SWEEP_INTERVAL="${HAK_SWEEP_INTERVAL:-3600}"

mkdir -p "$HAK_DATA" "$HAK_UPLOADS"
chmod 700 "$HAK_DATA" 2>/dev/null || true

say() { printf '[run.sh] %s\n' "$*"; }
die() { printf '[run.sh] ERROR: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "$2"; }
need python3 "python3 (3.11+) not found"
need curl "curl not found (used by --status)"

python3 - <<'PY' || die "python dependencies missing (need: fastapi, uvicorn, pydantic, python-multipart)"
import importlib
for mod in ("fastapi", "uvicorn", "pydantic", "multipart"):
    importlib.import_module(mod)
print("deps ok")
PY

TOKEN_FILE="$HAK_DATA/operator.token"

has_live_operator_token() {
  [ -f "$TOKEN_FILE" ] || return 1
  python3 - "$TOKEN_FILE" <<'PY'
import hashlib, os, sqlite3, sys
tok = open(sys.argv[1]).read().strip()
if not tok:
    sys.exit(1)
con = sqlite3.connect(os.environ["HAK_DB"])
row = con.execute("SELECT revoked_at FROM tokens WHERE token_hash=?",
                  (hashlib.sha256(tok.encode()).hexdigest(),)).fetchone()
con.close()
sys.exit(0 if row and row[0] is None else 1)
PY
}

# tokens for --ensure-operator: keep the operator secret on disk once (0600),
# because run.sh is the host-local recovery path (D24) — the host shell is the
# trust root on a LAN-only service. Remove the file to force re-issuance.
ensure_operator() {
  if has_live_operator_token; then
    say "operator token already present and live ($TOKEN_FILE)"
    return 0
  fi
  say "no live operator token — bootstrapping (D24 host-local recovery)"
  local out
  out="$(python3 hak.py --ensure-operator)" || die "bootstrap failed"
  printf '%s\n' "$out" > "$TOKEN_FILE"     # stdout is exactly the raw secret
  chmod 600 "$TOKEN_FILE"
  say "operator token saved to $TOKEN_FILE (0600). It is shown once:"
  sed 's/^/    /' "$TOKEN_FILE"
}

case "${1:-serve}" in
  --ensure-operator)
    ensure_operator
    ;;
  --sweep)
    exec python3 hak.py --sweep
    ;;
  --backup)
    DIR="${2:-}"
    [ -n "$DIR" ] || die "usage: ./run.sh --backup <dir>"
    exec python3 hak.py --backup "$DIR"
    ;;
  --status)
    URL="http://$HAK_HOST:$HAK_PORT/v1/health"
    if [ -f "$TOKEN_FILE" ]; then
      TOK="$(cat "$TOKEN_FILE")"
      R="$(curl -sf -m 5 -H "Authorization: Bearer $TOK" "$URL" 2>/dev/null || true)"
    else
      R=""
    fi
    if [ -n "$R" ]; then
      say "service UP: $R"
    else
      say "service not answering (or no operator token) at $URL"
    fi
    say "data dir: $HAK_DATA (db: $HAK_DB, uploads: $HAK_UPLOADS)"
    ;;
  serve)
    ensure_operator
    say "starting HAK on $HAK_HOST:$HAK_PORT (db: $HAK_DB)"
    exec python3 -m uvicorn hak:app --host "$HAK_HOST" --port "$HAK_PORT" --log-level warning
    ;;
  *)
    die "unknown command '$1' (use: serve | --ensure-operator | --sweep | --backup <dir> | --status)"
    ;;
esac

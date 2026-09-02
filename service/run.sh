#!/bin/sh
# HAK — inter-agent messaging bus. Copyright (C) 2026 asb (operator seat).
# SPDX-License-Identifier: AGPL-3.0-only
# This file is part of HAK. See LICENSE for the full notice.
#
# run.sh — one-shot launcher/operator for the HAK service (spec v0.5.1).
# POSIX sh (dash-safe): invocable as 'sh run.sh' on any Debian/Ubuntu.
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
#   HAK_VENV       venv to create/use when system python can't host
#                  the deps (PEP 668 externally-managed)            [./.venv]
#   HAK_PYTHON     pin a specific interpreter (skips auto-detection)
#
# The service additionally honors HAK_DB / HAK_UPLOADS directly (run.sh sets
# them from HAK_DATA unless already provided).
#
# Python environment policy: run.sh uses the system python3 when it already
# imports the dependencies; otherwise it provisions HAK_VENV (creating it on
# first use and installing into it). System packages are never touched — on
# PEP 668 systems a bare "pip install" into the system interpreter is
# refused by design, and we do not bypass that with --break-system-packages.

set -eu
cd "$(dirname "$0")"

HAK_DATA="${HAK_DATA:-./data}"
HAK_HOST="${HAK_HOST:-127.0.0.1}"
HAK_PORT="${HAK_PORT:-8890}"
export HAK_DB="${HAK_DB:-$HAK_DATA/hak.db}"
export HAK_UPLOADS="${HAK_UPLOADS:-$HAK_DATA/uploads}"
export HAK_SWEEP_INTERVAL="${HAK_SWEEP_INTERVAL:-3600}"
HAK_VENV="${HAK_VENV:-./.venv}"

mkdir -p "$HAK_DATA" "$HAK_UPLOADS"
chmod 700 "$HAK_DATA" 2>/dev/null || true

say() { printf '[run.sh] %s\n' "$*"; }
die() { printf '[run.sh] ERROR: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "$2"; }
need python3 "python3 (3.11+) not found"
need curl "curl not found (used by --status)"

PY=""
DEPS="fastapi uvicorn pydantic python-multipart pytest httpx2"

deps_ok() {  # deps_ok <interpreter> — exit 0 iff version + runtime deps import
  "$1" - <<'PY' >/dev/null 2>&1
import sys
if sys.version_info < (3, 11):
    raise SystemExit(1)
import importlib
for mod in ("fastapi", "uvicorn", "pydantic", "multipart"):
    importlib.import_module(mod)
PY
}

resolve_python() {
  if [ -n "${HAK_PYTHON:-}" ]; then
    command -v "$HAK_PYTHON" >/dev/null 2>&1 || die "HAK_PYTHON not found: $HAK_PYTHON"
    deps_ok "$HAK_PYTHON" \
      || die "HAK_PYTHON ($HAK_PYTHON) can't host HAK (need python 3.11+ with: $DEPS)"
    PY="$HAK_PYTHON"
    return
  fi
  if [ -x "$HAK_VENV/bin/python" ]; then
    if ! deps_ok "$HAK_VENV/bin/python"; then
      say "venv at $HAK_VENV exists but lacks deps — installing"
      "$HAK_VENV/bin/pip" install --quiet $DEPS \
        || die "pip install into $HAK_VENV failed"
    fi
    PY="$HAK_VENV/bin/python"
    return
  fi
  if deps_ok python3; then
    PY="python3"
    return
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "system python3 too old for HAK (need 3.11+, found $(python3 -V 2>&1)) — install a newer python and point HAK_VENV or HAK_PYTHON at it"
  say "system python3 can't host the deps (missing or externally managed, PEP 668)"
  say "provisioning a private venv at $HAK_VENV — system packages untouched"
  python3 -m venv "$HAK_VENV" \
    || die "venv creation failed — on Debian/Ubuntu: sudo apt install python3-venv (or python3.X-venv)"
  "$HAK_VENV/bin/pip" install --quiet $DEPS \
    || die "pip install into venv failed (network?) — manual: $HAK_VENV/bin/pip install $DEPS"
  PY="$HAK_VENV/bin/python"
}
resolve_python

TOKEN_FILE="$HAK_DATA/operator.token"

has_live_operator_token() {
  [ -f "$TOKEN_FILE" ] || return 1
  "$PY" - "$TOKEN_FILE" <<'PY'
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
  out=""
  out="$($PY hak.py --ensure-operator)" || die "bootstrap failed"
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
    exec "$PY" hak.py --sweep
    ;;
  --backup)
    DIR="${2:-}"
    [ -n "$DIR" ] || die "usage: ./run.sh --backup <dir>"
    exec "$PY" hak.py --backup "$DIR"
    ;;
  --status)
    URL="http://$HAK_HOST:$HAK_PORT/v1/health"
    if [ -f "$TOKEN_FILE" ]; then
      TOK="$(cat "$TOKEN_FILE")"
      CODE="$(curl -s -m 5 -o /tmp/hak_status_body.$$ -w '%{http_code}' \
              -H "Authorization: Bearer $TOK" "$URL" 2>/dev/null)" || CODE="000"
    else
      CODE="no-token"
    fi
    case "$CODE" in
      200)
        say "service UP: $(cat /tmp/hak_status_body.$$ 2>/dev/null)"
        ;;
      401|403)
        say "something is answering at $URL but REJECTED our operator token"
        say "  → likely a foreign HAK instance holds the port, or $TOKEN_FILE is stale"
        ;;
      000)
        say "nothing is listening at $URL (or connection refused)"
        ;;
      *)
        say "unexpected HTTP $CODE from $URL: $(cat /tmp/hak_status_body.$$ 2>/dev/null)"
        ;;
    esac
    rm -f /tmp/hak_status_body.$$ 2>/dev/null || true
    if [ "$CODE" = "no-token" ]; then
      say "no operator token at $TOKEN_FILE — run './run.sh --ensure-operator'"
    fi
    say "data dir: $HAK_DATA (db: $HAK_DB, uploads: $HAK_UPLOADS)"
    ;;
  serve)
    ensure_operator
    say "starting HAK on $HAK_HOST:$HAK_PORT (db: $HAK_DB)"
    exec "$PY" -m uvicorn hak:app --host "$HAK_HOST" --port "$HAK_PORT" --log-level warning
    ;;
  *)
    die "unknown command '$1' (use: serve | --ensure-operator | --sweep | --backup <dir> | --status)"
    ;;
esac

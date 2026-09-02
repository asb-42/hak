"""HAK service — FastAPI app, spec v0.5.1 (D1-D48, C1-C18).

Run:  HAK_DB=path/to/hak.db HAK_UPLOADS=path/to/uploads uvicorn hak:app --port 8890
Bootstrap (no admin token left):  python3 hak.py --bootstrap --seat operator
"""

# HAK — inter-agent messaging bus. Copyright (C) 2026 asb (operator seat).
# SPDX-License-Identifier: AGPL-3.0-only
# This file is part of HAK. See LICENSE for the full notice.

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from canonical import canonicalize

DB_PATH = os.environ.get("HAK_DB", "hak.db")
UPLOADS_DIR = Path(os.environ.get("HAK_UPLOADS", "uploads"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SWEEP_INTERVAL = int(os.environ.get("HAK_SWEEP_INTERVAL", "3600"))  # 0 = off

ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
TYPES = {"chat", "status", "task_request", "task_result", "artifact_ref",
         "review_verdict", "retraction"}
KINDS = {"status", "handover", "response", "admin-op"}
STATES = {"working_on", "waiting_on", "blocked", "done"}
SCOPE_KINDS = {"write", "read-exclusive", "exclusive", "share"}
ADMIN_OPS = {"member_approve", "member_revoke", "token_issue", "token_revoke",
             "token_revoke_all", "token_bootstrap", "room_create",
             "charter_update", "attachment_delete"}
MAX_BODY_BYTES = 64 * 1024            # Q8/D42
HARD_TTL_MAX = 1440                    # D28 Fix B
GRACE_HOURS = 24                      # D30
GC_DAYS = 30                           # D18/D23
ALLOWED_URI_SCHEMES = {"http", "https", "git", "ssh", "file"}  # D43

_write_lock = threading.Lock()         # serializes write transactions (single writer)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def write_tx():
    """Single-writer transaction (D15/D40): BEGIN IMMEDIATE under the process
    lock — sqlite3's default isolation would otherwise convert our explicit
    BEGIN IMMEDIATE into a no-op and queue writes as implicit snapshots."""
    with _write_lock:
        con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def init_db() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA_PATH.read_text())
        con.execute("PRAGMA journal_mode = WAL")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def error(status: int, code: str, message: str, detail: Any = None) -> HTTPException:
    """D26 error envelope: {"error": {"code", "message", ...}}."""
    body = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return HTTPException(status_code=status, detail=body)


# ---------------------------------------------------------------- auth

def bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise error(401, "unauthorized", "Bearer token required")
    return auth[7:].strip()


def seat_from_token(request: Request) -> tuple[str, sqlite3.Row]:
    """Resolve token -> (seat, token_row). 401 on invalid/revoked (D24)."""
    raw = bearer_token(request)
    h = sha256_hex(raw.encode())
    with db() as con:
        row = con.execute("SELECT * FROM tokens WHERE token_hash=?", (h,)).fetchone()
    if row is None or row["revoked_at"]:
        raise error(401, "unauthorized", "Invalid or revoked token")
    return row["seat"], row


def require_room_member(request: Request, room: str) -> tuple[str, sqlite3.Row]:
    seat, tok = seat_from_token(request)
    with db() as con:
        m = con.execute(
            "SELECT status FROM memberships WHERE room=? AND seat=?", (room, seat)
        ).fetchone()
        if m is None:
            raise error(403, "forbidden", "Not a member of this room (join first)")
        if m["status"] != "member":
            raise error(403, "forbidden", f"Membership status is {m['status']}")
        # activity semantics: any authenticated request refreshes last_poll (D27/D43)
        con.execute(
            "INSERT INTO member_state (room, seat, last_poll) VALUES (?,?,?) "
            "ON CONFLICT(room, seat) DO UPDATE SET last_poll=excluded.last_poll",
            (room, seat, now_iso()))
    return seat, tok


def is_admin(con: sqlite3.Connection, room: str, seat: str) -> bool:
    """D32/D45: admins is the sole authority; admin authority requires member
    status; operator non-revocable."""
    room_row = con.execute("SELECT charter FROM rooms WHERE name=?", (room,)).fetchone()
    if room_row is None:
        return False
    charter = json.loads(room_row[0])
    if seat not in charter.get("admins", []):
        return False
    m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?", (room, seat)).fetchone()
    return m is not None and m["status"] == "member"


def require_room_admin(request: Request, room: str) -> str:
    seat, _ = seat_from_token(request)
    with db() as con:
        _require_room_exists(con, room)
        if not is_admin(con, room, seat):
            raise error(403, "forbidden", "Admin role required (member-status admin)")
    return seat


def _require_room_exists(con: sqlite3.Connection, room: str) -> None:
    if con.execute("SELECT 1 FROM rooms WHERE name=?", (room,)).fetchone() is None:
        raise error(404, "not_found", f"Room {room} not found")


# ---------------------------------------------------------------- system envelopes (D6/D40)

def envelope_id(room: str, seq: int) -> str:
    """Globally unique: id is the PK across all rooms, seq is per-room (D10)."""
    return f"m_{room}_{seq:010d}"


def append_admin_envelope(con: sqlite3.Connection, room: str, op: str,
                          target: str, body_text: str) -> None:
    """Append the admin-op system envelope INSIDE the caller's write_tx —
    mutation + envelope are one transaction (D40)."""
    seq = next_seq(con, room)
    ts = now_iso()
    con.execute(
        "INSERT INTO messages (id, room, seq, from_seat, backend, to_seat, type,"
        " body, meta, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (envelope_id(room, seq), room, seq, "hak", None, None, "chat", body_text,
         json.dumps({"kind": "admin-op", "op": op, "target": target}), ts),
    )


def next_seq(con: sqlite3.Connection, room: str) -> int:
    row = con.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM messages WHERE room=?", (room,)).fetchone()
    return row["n"]


# ---------------------------------------------------------------- models

class EnvelopeIn(BaseModel):
    """Client-creatable fields only. Server-owned fields (seq, id, ts, room,
    from, and meta.kind='admin-op') are rejected at the schema edge (D10):
    extra=forbid turns any of them into a 422 before a write happens."""
    model_config = ConfigDict(extra="forbid")

    client_msg_id: str | None = None
    backend: str | None = None
    to: dict | None = None
    type: str
    reply_to: str | None = None
    body: str
    attachments: list | None = None
    refs: list | None = None
    meta: dict | None = None


class RoomIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    charter: dict


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_uri: str
    kind: str
    units: int = 1
    note: str | None = None
    ttl_min: int | None = None


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seat: str


# ---------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app):
    """Startup: ensure schema; start the periodic sweeper thread (D18/D23).
    Shutdown: stop it. The sweeper is also exposed as run.sh --sweep for manual
    passes; both are idempotent — the sweep is the only deleter (D23)."""
    init_db()
    stop = threading.Event()
    th = None
    if SWEEP_INTERVAL > 0:
        def loop():
            while not stop.wait(SWEEP_INTERVAL):
                try:
                    sweep_once()
                except Exception as e:  # never kill the service over GC
                    print(f"[hak] sweep pass failed: {e}", file=sys.stderr)

        th = threading.Thread(target=loop, name="hak-sweeper", daemon=True)
        th.start()
    yield
    stop.set()
    if th is not None:
        th.join(timeout=5)


app = FastAPI(title="HAK", version="v1", lifespan=lifespan)

# Human viewer: read-only lens on the same API (no protocol surface, no
# writes). Mounted same-origin, so the browser needs no CORS and no second
# service. Deployments without service/ui/ behave exactly as before.
_UI_DIR = Path(__file__).resolve().parent / "ui"
if (_UI_DIR / "index.html").is_file():
    from fastapi.staticfiles import StaticFiles  # noqa: E402 (optional dep, only when ui/ present)

    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.exception_handler(StarletteHTTPException)
def http_exc_handler(request: Request, exc: StarletteHTTPException):
    """Uniform D26 envelope: our raised errors and framework 404/405 alike.
    The 413 rewrite for oversized bodies (D42) happens in post_message."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "invalid_request")
    return JSONResponse(status_code=exc.status_code, content={
        "error": {"code": code, "message": str(exc.detail)}})


@app.get("/v1/whoami")
def whoami(request: Request):
    """Identity of the bearer token — the UI needs its seat for DM/reply/
    admin/presence affordances. Nothing beyond the seat (D43 spirit)."""
    seat, _ = seat_from_token(request)
    return {"seat": seat}


@app.get("/v1/health")
def health(request: Request):
    seat_from_token(request)  # authenticated (D43)
    return {"status": "ok", "service": "hak", "version": "v1"}


# ------------------------------------------------- rooms & membership

@app.get("/v1/rooms")
def list_rooms(request: Request):
    seat, _ = seat_from_token(request)
    with db() as con:
        rows = con.execute(
            "SELECT r.name, r.charter FROM rooms r JOIN memberships m ON m.room=r.name "
            "WHERE m.seat=? AND m.status='member'", (seat,)).fetchall()
    return [{"name": r["name"], "charter": json.loads(r["charter"])} for r in rows]


@app.post("/v1/rooms", status_code=201)
def create_room(payload: RoomIn, request: Request):
    seat, _ = seat_from_token(request)
    if not ROOM_RE.match(payload.name):
        raise error(422, "invalid_room_name", "Room name must match ^[a-z0-9][a-z0-9._-]{0,62}$")
    with write_tx() as con:
        has_any_room = con.execute("SELECT 1 FROM rooms LIMIT 1").fetchone() is not None
        # First room: any authenticated seat may create (bootstrap); later: admin-only (Q5).
        if has_any_room and not _is_admin_anywhere(con, seat):
            raise error(403, "forbidden", "Room creation is admin-only in v1")
        if con.execute("SELECT 1 FROM rooms WHERE name=?", (payload.name,)).fetchone():
            raise error(409, "room_exists", f"Room {payload.name} already exists")
        charter = dict(payload.charter)
        charter["name"] = payload.name
        # materialize defaults (D46)
        cp = charter.setdefault("claim_policy", {})
        cp.setdefault("default_ttl_min", 30)
        cp.setdefault("write_mandatory_for_repo_paths", True)
        cp.setdefault("share_capacities", {})
        ap = charter.setdefault("attachment_policy", {})
        ap.setdefault("max_file_bytes", 26214400)
        ap.setdefault("max_unreferenced_bytes", None)
        charter.setdefault("admins", [])
        if seat not in charter["admins"]:
            charter["admins"].append(seat)     # creator becomes admin (D32)
        if "operator" not in charter["admins"]:
            charter["admins"].append("operator")  # D32 invariant
        con.execute("INSERT INTO rooms (name, charter, created_at) VALUES (?,?,?)",
                    (payload.name, canonicalize(charter), now_iso()))
        con.execute("INSERT INTO memberships (room, seat, status) VALUES (?,?, 'member')",
                    (payload.name, seat))
        con.execute("INSERT OR IGNORE INTO member_state (room, seat) VALUES (?,?)",
                    (payload.name, seat))
        con.execute("INSERT OR IGNORE INTO memberships (room, seat, status) VALUES (?,?, 'member')",
                    (payload.name, "operator"))  # operator auto-member (D32)
        con.execute("INSERT OR IGNORE INTO member_state (room, seat) VALUES (?,?)",
                    (payload.name, "operator"))
        append_admin_envelope(con, payload.name, "room_create", seat,
                              f"Room created by {seat}; charter stored.")
    return {"name": payload.name, "charter": charter}


@app.get("/v1/rooms/{room}")
def get_room(room: str, request: Request):
    require_room_member(request, room)
    with db() as con:
        r = con.execute("SELECT * FROM rooms WHERE name=?", (room,)).fetchone()
        members = con.execute(
            "SELECT seat, status FROM memberships WHERE room=? ORDER BY seat", (room,)).fetchall()
    return {"name": r["name"], "charter": json.loads(r["charter"]),
            "members": [dict(m) for m in members]}


@app.post("/v1/rooms/{room}/join")
def join_room(room: str, request: Request):
    seat, _ = seat_from_token(request)
    with write_tx() as con:
        _require_room_exists(con, room)
        m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?",
                        (room, seat)).fetchone()
        if m and m["status"] == "member":
            return {"status": "member", "note": "already member (idempotent re-join, D36)"}
        if m and m["status"] == "revoked":
            con.execute("UPDATE memberships SET status='pending' WHERE room=? AND seat=?",
                        (room, seat))  # new pending row semantics (D36)
        else:
            con.execute("INSERT INTO memberships (room, seat, status) VALUES (?,?, 'pending')",
                       (room, seat))
    return {"status": "pending"}


@app.post("/v1/rooms/{room}/members/{seat}/approve")
def approve_member(room: str, seat: str, request: Request):
    admin = require_room_admin(request, room)
    with write_tx() as con:
        _require_room_exists(con, room)
        m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?",
                        (room, seat)).fetchone()
        if m is None:
            raise error(404, "not_found", f"{seat} has not joined")
        if m["status"] == "member":
            return {"status": "member", "note": "already member (idempotent)"}
        if m["status"] == "revoked":
            # re-approve of a revoked seat is forbidden: the seat must re-join
            # (new pending) first — re-admission is an explicit act (D36)
            raise error(422, "membership_revoked", f"{seat} must re-join first (D36)")
        con.execute("UPDATE memberships SET status='member' WHERE room=? AND seat=?",
                    (room, seat))
        con.execute("INSERT OR IGNORE INTO member_state (room, seat) VALUES (?,?)", (room, seat))
        append_admin_envelope(con, room, "member_approve", seat,
                              f"{admin} approved {seat} as member.")
    return {"status": "member"}


@app.post("/v1/rooms/{room}/members/{seat}/revoke")
def revoke_member(room: str, seat: str, request: Request):
    admin = require_room_admin(request, room)
    if seat == "operator":
        raise error(422, "operator_non_revocable",
                    "operator membership is non-revocable in v1 (D45)")
    with write_tx() as con:
        _require_room_exists(con, room)
        m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?",
                        (room, seat)).fetchone()
        if m is None:
            raise error(404, "not_found", f"{seat} is not a member")
        con.execute("UPDATE memberships SET status='revoked' WHERE room=? AND seat=?",
                    (room, seat))
        # kill all the seat's tokens (D19)
        con.execute("UPDATE tokens SET revoked_at=? WHERE seat=? AND revoked_at IS NULL",
                    (now_iso(), seat))
        append_admin_envelope(con, room, "member_revoke", seat,
                              f"{admin} revoked {seat}; all tokens killed (D19).")
    return {"status": "revoked"}


@app.get("/v1/rooms/{room}/members")
def list_members(room: str, request: Request):
    require_room_member(request, room)
    with db() as con:
        _require_room_exists(con, room)
        rows = con.execute(
            "SELECT m.seat, m.status, ms.last_read_seq, ms.last_poll "
            "FROM memberships m LEFT JOIN member_state ms ON ms.room=m.room AND ms.seat=m.seat "
            "WHERE m.room=? ORDER BY m.seat", (room,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------- messages

@app.post("/v1/rooms/{room}/messages", status_code=201)
def post_message(room: str, payload: EnvelopeIn, request: Request):
    seat, _ = require_room_member(request, room)
    if len(payload.body.encode("utf-8")) > MAX_BODY_BYTES:
        raise error(413, "body_too_large",
                    f"body exceeds {MAX_BODY_BYTES} bytes (D42)")
    _validate_envelope(room, payload, seat, con=None)
    body_hash = canonicalize(payload.model_dump(exclude_none=False))
    with write_tx() as con:
        _require_room_exists(con, room)
        _validate_envelope(room, payload, seat, con=con)  # re-check under lock
        if payload.client_msg_id:
            prev = con.execute(
                "SELECT * FROM messages WHERE room=? AND from_seat=? AND client_msg_id=?",
                (room, seat, payload.client_msg_id)).fetchone()
            if prev:
                if prev["idem_hash"] == body_hash:
                    return JSONResponse(status_code=200, content=envelope_out(prev))
                raise error(409, "idempotency_conflict",
                            "client_msg_id reused with different content (D12/D47)",
                            {"original_id": prev["id"]})
        seq = next_seq(con, room)
        mid = envelope_id(room, seq)
        con.execute(
            "INSERT INTO messages (id, room, seq, client_msg_id, idem_hash, from_seat,"
            " backend, to_seat, type, reply_to, body, attachments, refs, meta, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, room, seq, payload.client_msg_id, body_hash, seat, payload.backend,
             (payload.to or {}).get("seat") if payload.to else None, payload.type,
             payload.reply_to, payload.body,
             canonicalize(payload.attachments) if payload.attachments else None,
             canonicalize(payload.refs) if payload.refs else None,
             canonicalize(payload.meta) if payload.meta else None, now_iso()))
        row = con.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    return envelope_out(row)


def _validate_envelope(room: str, p: EnvelopeIn, seat: str, con) -> None:
    if p.type not in TYPES:
        raise error(422, "invalid_type", f"type must be one of {sorted(TYPES)}")
    if p.type in ("task_result", "review_verdict"):
        if not (p.meta and p.meta.get("kind") == "response"):
            raise error(422, "response_marker_required",
                        "task_result/review_verdict require meta.kind='response' (D13)")
    if p.meta:
        kind = p.meta.get("kind")
        if kind not in KINDS:
            raise error(422, "invalid_meta_kind",
                        f"meta.kind must be one of {sorted(KINDS)} (closed set, D39)")
        if kind == "admin-op":
            raise error(422, "admin_op_reserved",
                        "admin-op is emitted only by the bus (D39/D6)")
        if kind == "status":
            if p.meta.get("state") not in STATES:
                raise error(422, "invalid_status_state",
                            "meta.state must be working_on|waiting_on|blocked|done")
    if p.type == "retraction":
        if not p.reply_to:
            raise error(422, "retraction_requires_reply_to",
                        "retraction requires reply_to (D37)")
        if con is not None:
            target = con.execute("SELECT * FROM messages WHERE id=?", (p.reply_to,)).fetchone()
            if target is None:
                raise error(422, "retraction_target_unknown", "reply_to message not found")
            if target["type"] == "retraction":
                raise error(422, "retraction_of_retraction",
                            "a retraction cannot target a retraction (D37)")
            if target["from_seat"] != seat and not is_admin(con, room, seat):
                raise error(403, "retraction_forbidden",
                            "only the author or an admin may retract (D37)")
            prior = con.execute(
                "SELECT id FROM messages WHERE type='retraction' AND reply_to=?",
                (p.reply_to,)).fetchone()
            if prior:
                raise error(409, "duplicate_retraction",
                            "target already retracted (D37)",
                            {"effective_retraction": prior["id"]})
    if con is not None and p.attachments:
        for a in p.attachments:
            f = con.execute("SELECT * FROM files WHERE file_id=?", (a.get("file_id", ""),)).fetchone()
            if f is None or f["deletion_pending"]:
                raise error(422, "attachment_not_available",
                            f"file {a.get('file_id')} unknown or deletion_pending (D44)")
            if f["room"] != room:
                raise error(422, "cross_room_file",
                            f"file {a['file_id']} belongs to room {f['room']} (D29)")


def envelope_out(row: sqlite3.Row) -> dict:
    def j(x):
        return json.loads(x) if x else None
    return {
        "seq": row["seq"], "id": row["id"], "client_msg_id": row["client_msg_id"],
        "room": row["room"], "ts": row["ts"],
        "from": {"seat": row["from_seat"], "backend": row["backend"]},
        "to": {"seat": row["to_seat"]} if row["to_seat"] else None,
        "type": row["type"], "reply_to": row["reply_to"], "body": row["body"],
        "attachments": j(row["attachments"]), "refs": j(row["refs"]), "meta": j(row["meta"]),
    }


@app.get("/v1/rooms/{room}/messages")
def get_messages(room: str, request: Request,
                 since: int = 0, until: int | None = None,
                 from_seat: str | None = None, to: str | None = None,
                 type: str | None = None, thread: str | None = None,
                 meta_kind: str | None = None, for_seat: str | None = None,
                 limit: int = 100, order: str = "asc"):
    seat, _ = require_room_member(request, room)
    with db() as con:
        _require_room_exists(con, room)
        q = "SELECT * FROM messages WHERE room=? AND seq > ?"
        args: list[Any] = [room, since]
        if until is not None:
            q += " AND seq <= ?"
            args.append(until)
        if from_seat:
            q += " AND from_seat = ?"
            args.append(from_seat)
        if to == "null":
            q += " AND to_seat IS NULL"
        elif to:
            q += " AND to_seat = ?"
            args.append(to)
        if type:
            q += " AND type = ?"
            args.append(type)
        if thread:
            q += " AND reply_to = ?"   # direct replies only (D25)
            args.append(thread)
        if meta_kind:
            q += " AND json_extract(meta,'$.kind') = ?"
            args.append(meta_kind)
        if for_seat == "me":
            for_seat = seat               # resolves server-side (D25/D10)
        if for_seat:
            q += " AND json_extract(meta,'$.for_seat') = ?"
            args.append(for_seat)
        limit = max(1, min(limit, 500))
        q += " ORDER BY seq ASC LIMIT ?"   # bounds before order (D33); asc canonical
        args.append(limit + 1)             # detect continuation
        rows = con.execute(q, args).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_since = rows[-1]["seq"] if rows else since  # computed before desc flip (D33)
    if order == "desc":
        rows = rows[::-1]                  # presentation only (D33)
    out = [envelope_out(r) for r in rows]
    return {"messages": out, "has_more": has_more, "next_since": next_since}


@app.get("/v1/rooms/{room}/messages/{mid}")
def get_message(room: str, mid: str, request: Request):
    require_room_member(request, room)
    with db() as con:
        row = con.execute("SELECT * FROM messages WHERE room=? AND id=?", (room, mid)).fetchone()
    if row is None:
        raise error(404, "not_found", "message not found")
    return envelope_out(row)


@app.post("/v1/rooms/{room}/read")
def mark_read(room: str, payload: ReadIn, request: Request):
    seat, _ = require_room_member(request, room)
    with db() as con:
        con.execute(
            "INSERT INTO member_state (room, seat, last_read_seq) VALUES (?,?,?) "
            "ON CONFLICT(room, seat) DO UPDATE SET last_read_seq=excluded.last_read_seq",
            (room, seat, payload.seq))
    return {"ok": True}


# ------------------------------------------------- scopes (D14/D15/D22/D34)

def _normalize_resource(uri: str) -> tuple[str, str]:
    """scheme lowercased, exact-URI conflict identity (D34)."""
    if "://" not in uri:
        raise error(422, "invalid_resource_uri", "resource_uri must be scheme://rest")
    scheme, rest = uri.split("://", 1)
    scheme = scheme.lower()
    if not scheme or not rest:
        raise error(422, "invalid_resource_uri", "empty scheme or path")
    return scheme, f"{scheme}://{rest}"


def _live_claims(con: sqlite3.Connection, room: str, resource: str) -> list[sqlite3.Row]:
    """Project live claims: last event per scope_id wins; live = last action in
    (claim, renew) and expires_at > now (D15)."""
    now = now_iso()
    rows = con.execute(
        "SELECT * FROM scope_events WHERE room=? AND resource_uri=? ORDER BY scope_seq",
        (room, resource)).fetchall()
    last: dict[str, sqlite3.Row] = {}
    for r in rows:
        last[r["scope_id"]] = r
    return [r for r in last.values()
            if r["action"] in ("claim", "renew") and r["expires_at"] > now]


def _share_units(con: sqlite3.Connection, room: str, resource: str) -> int:
    return sum(r["units"] for r in _live_claims(con, room, resource) if r["kind"] == "share")


@app.post("/v1/rooms/{room}/scopes", status_code=201)
def post_scope(room: str, payload: ScopeIn, request: Request):
    seat, _ = require_room_member(request, room)
    scheme, resource = _normalize_resource(payload.resource_uri)
    if payload.kind not in SCOPE_KINDS:
        raise error(422, "invalid_scope_kind",
                    f"kind must be one of {sorted(SCOPE_KINDS)} (reserve dropped, D20)")
    if payload.kind == "share" and payload.units < 1:
        raise error(422, "invalid_units", "share units must be >= 1 (D34)")
    with write_tx() as con:
        _require_room_exists(con, room)
        charter = json.loads(con.execute("SELECT charter FROM rooms WHERE name=?", (room,)).fetchone()[0])
        ttl_default = charter["claim_policy"]["default_ttl_min"]
        if payload.ttl_min is not None:
            if payload.ttl_min > HARD_TTL_MAX:
                raise error(422, "ttl_too_large", f"ttl_min above hard max {HARD_TTL_MAX}")
            ttl = min(payload.ttl_min, ttl_default)  # clamp (Fix B, D28)
        else:
            ttl = ttl_default
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl)).isoformat(timespec="milliseconds")
        live = _live_claims(con, room, resource)
        # self-reclaim refresh (D16)
        own = [r for r in live if r["seat"] == seat and r["kind"] == payload.kind]
        if own:
            if payload.kind == "share":
                cap = charter["claim_policy"]["share_capacities"].get(scheme)
                if cap is not None:
                    used = _share_units(con, room, resource)
                    if used - own[0]["units"] + payload.units > cap:
                        raise error(409, "share_capacity_exhausted",
                                    "Share capacity exhausted (D22/D34)",
                                    {"current_units": used, "capacity": cap})
            sseq = next_scope_seq(con, room)
            con.execute(
                "INSERT INTO scope_events (scope_seq, room, scope_id, seat, action,"
                " resource_uri, kind, units, note, ttl_min, expires_at, ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sseq, room, own[0]["scope_id"], seat, "renew", resource,
                 payload.kind, payload.units, payload.note, ttl, expires, now_iso()))
            return JSONResponse(status_code=200, content={
                "scope_id": own[0]["scope_id"], "scope_seq": sseq, "expires_at": expires})
        # conflict matrix (D22, symmetric per D34): the relation applies
        # direction-independently — a live share blocks a new write, exactly as
        # a live write blocks a new share.
        conflict = None
        if payload.kind == "exclusive":
            if live:
                conflict = live[0]
        elif payload.kind in ("write", "read-exclusive"):
            for r in live:
                if r["kind"] in ("exclusive", "write", "read-exclusive", "share"):
                    conflict = r
                    break
        elif payload.kind == "share":
            for r in live:
                if r["kind"] in ("exclusive", "write", "read-exclusive"):
                    conflict = r
                    break
            # share vs share is allowed while capacity holds (checked below)
        if conflict is not None:
            raise error(409, "scope_conflict", "Conflicting live claim (D22)",
                        {"conflicting": {"holder": conflict["seat"], "kind": conflict["kind"],
                                          "expires_at": conflict["expires_at"]}})
        if payload.kind == "share":
            cap = charter["claim_policy"]["share_capacities"].get(scheme)
            if cap is not None:
                used = _share_units(con, room, resource)
                if used + payload.units > cap:
                    raise error(409, "share_capacity_exhausted",
                                "Share capacity exhausted (D22/D34)",
                                {"current_units": used, "capacity": cap})
        sseq = next_scope_seq(con, room)
        sid = f"s_{room[:8]}_{sseq:06d}_{secrets.token_hex(3)}"
        con.execute(
            "INSERT INTO scope_events (scope_seq, room, scope_id, seat, action,"
            " resource_uri, kind, units, note, ttl_min, expires_at, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sseq, room, sid, seat, "claim", resource, payload.kind, payload.units,
             payload.note, ttl, expires, now_iso()))
    return {"scope_id": sid, "scope_seq": sseq, "expires_at": expires}


def next_scope_seq(con: sqlite3.Connection, room: str) -> int:
    row = con.execute("SELECT COALESCE(MAX(scope_seq),0)+1 AS n FROM scope_events WHERE room=?",
                      (room,)).fetchone()
    return row["n"]


@app.post("/v1/rooms/{room}/scopes/{scope_id}/renew")
def renew_scope(room: str, scope_id: str, request: Request):
    seat, _ = require_room_member(request, room)
    with write_tx() as con:
        _require_room_exists(con, room)
        rows = con.execute("SELECT * FROM scope_events WHERE room=? AND scope_id=? ORDER BY scope_seq",
                           (room, scope_id)).fetchall()
        if not rows:
            raise error(404, "not_found", "scope not found")
        last = rows[-1]
        live = last["action"] in ("claim", "renew") and last["expires_at"] > now_iso()
        if not live:
            raise error(404, "not_live", "Claim not live at transaction start (D34)")
        if last["seat"] != seat:
            raise error(403, "forbidden", "Only the holder may renew")
        charter = json.loads(con.execute("SELECT charter FROM rooms WHERE name=?", (room,)).fetchone()[0])
        ttl = charter["claim_policy"]["default_ttl_min"]
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl)).isoformat(timespec="milliseconds")
        sseq = next_scope_seq(con, room)
        con.execute(
            "INSERT INTO scope_events (scope_seq, room, scope_id, seat, action,"
            " resource_uri, kind, units, note, ttl_min, expires_at, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sseq, room, scope_id, seat, "renew", last["resource_uri"], last["kind"],
             last["units"], None, ttl, expires, now_iso()))
    return {"scope_id": scope_id, "expires_at": expires}


@app.delete("/v1/rooms/{room}/scopes/{scope_id}", status_code=204)
def release_scope(room: str, scope_id: str, request: Request):
    seat, _ = require_room_member(request, room)
    with write_tx() as con:
        _require_room_exists(con, room)
        rows = con.execute("SELECT * FROM scope_events WHERE room=? AND scope_id=? ORDER BY scope_seq",
                           (room, scope_id)).fetchall()
        if not rows:
            raise error(404, "not_found", "scope not found")
        last = rows[-1]
        live = last["action"] in ("claim", "renew") and last["expires_at"] > now_iso()
        if not live:
            return Response(status_code=204)   # idempotent release of a dead claim
        if last["seat"] != seat and not is_admin(con, room, seat):
            raise error(403, "forbidden", "Only the holder or an admin may release")
        sseq = next_scope_seq(con, room)
        con.execute(
            "INSERT INTO scope_events (scope_seq, room, scope_id, seat, action,"
            " resource_uri, kind, units, note, ttl_min, expires_at, ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sseq, room, scope_id, seat, "release", last["resource_uri"], last["kind"],
             last["units"], None, 0, last["expires_at"], now_iso()))
    return Response(status_code=204)


@app.get("/v1/rooms/{room}/scopes")
def get_scopes(room: str, request: Request, history: int = 0, since: int = 0):
    """active: last-event-per-scope projector, expiry-filtered (D2/D15).
    history: raw event log paged by scope_seq, since exclusive (D35)."""
    require_room_member(request, room)
    now = now_iso()
    with db() as con:
        _require_room_exists(con, room)
        if history:
            rows = con.execute(
                "SELECT * FROM scope_events WHERE room=? AND scope_seq > ? ORDER BY scope_seq",
                (room, since)).fetchall()
            return {"events": [dict(r) for r in rows]}
        rows = con.execute("SELECT * FROM scope_events WHERE room=?", (room,)).fetchall()
    last: dict[str, sqlite3.Row] = {}
    for r in rows:
        last[r["scope_id"]] = r
    live = [r for r in last.values()
            if r["action"] in ("claim", "renew") and r["expires_at"] > now]
    return {"active": [dict(r) for r in live]}


# ------------------------------------------------- files (D29/D30/D44)

@app.post("/v1/files", status_code=201)
async def upload_file(request: Request):
    seat, _ = seat_from_token(request)
    form = await request.form()
    room = form.get("room")
    if not room:
        raise error(422, "room_required", "Upload requires the target room (D29)")
    with db() as con:
        _require_room_exists(con, room)
        m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?",
                        (room, seat)).fetchone()
        if m is None or m["status"] != "member":
            raise error(403, "forbidden", "Upload requires member status (D29/D36)")
        charter = json.loads(con.execute("SELECT charter FROM rooms WHERE name=?", (room,)).fetchone()[0])
        max_bytes = charter["attachment_policy"]["max_file_bytes"]
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise error(422, "file_required", "multipart file field 'file' required")
    data = await upload.read()          # capped by starlette's in-memory size guard
    if len(data) > max_bytes:
        raise error(413, "file_too_large", f"File exceeds charter cap {max_bytes} bytes")
    file_id = "f_" + secrets.token_hex(8)
    (UPLOADS_DIR / file_id).write_bytes(data)
    with write_tx() as con:
        # re-check membership under the lock (TOCTOU against revoke)
        m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?",
                        (room, seat)).fetchone()
        if m is None or m["status"] != "member":
            (UPLOADS_DIR / file_id).unlink(missing_ok=True)
            raise error(403, "forbidden", "Upload requires member status (D29/D36)")
        con.execute(
            "INSERT INTO files (file_id, room, uploader, name, content_type, sha256,"
            " size, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (file_id, room, seat, upload.filename or "unnamed",
             upload.content_type or "application/octet-stream",
             sha256_hex(data), len(data), now_iso()))
    return {"file_id": file_id, "sha256": sha256_hex(data), "size": len(data),
            "name": upload.filename, "content_type": upload.content_type, "room": room}


@app.get("/v1/files/{file_id}")
def get_file(file_id: str, request: Request):
    seat, _ = seat_from_token(request)
    with db() as con:
        f = con.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()
        if f is None:
            raise error(404, "not_found", "file not found")
        if f["deletion_pending"]:
            raise error(404, "not_found", "file pending deletion (D44)")
        if f["room"]:
            m = con.execute("SELECT status FROM memberships WHERE room=? AND seat=?",
                            (f["room"], seat)).fetchone()
            is_adm = is_admin(con, f["room"], seat)
            if (m is None or m["status"] != "member") and not is_adm:
                raise error(403, "forbidden", "Room members only (D29)")
        path = UPLOADS_DIR / file_id
    if not path.exists():
        raise error(404, "not_found", "file content missing (sweep retry pending, D44)")
    return FileResponse(path, filename=f["name"], media_type=f["content_type"])


# ------------------------------------------------- admin/ops (D6/D19/D24/D40)

@app.post("/v1/tokens", status_code=201)
def issue_token(payload: TokenIn, request: Request):
    """Issue a token for a seat. Requires admin somewhere OR bootstrap state
    (no rooms yet). Token issuance is global; any room admin may issue (LAN
    trust model, D19). Admin check + insert + envelope in ONE transaction —
    revoke cannot interleave (D40)."""
    seat, _ = seat_from_token(request)
    token_id = "t_" + secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    with write_tx() as con:
        any_room = con.execute("SELECT name FROM rooms LIMIT 1").fetchone()
        if any_room and not _is_admin_anywhere(con, seat):
            raise error(403, "forbidden", "Token issuance requires admin role")
        con.execute("INSERT INTO tokens (token_id, seat, token_hash, created_at) VALUES (?,?,?,?)",
                    (token_id, payload.seat, sha256_hex(secret.encode()), now_iso()))
        # envelope in the first room for audit (D40); global op, room-scoped trail
        room_row = con.execute("SELECT name FROM rooms ORDER BY created_at LIMIT 1").fetchone()
        if room_row:
            append_admin_envelope(con, room_row["name"], "token_issue", payload.seat,
                                  f"Token {token_id} issued for {payload.seat}.")
    return {"token_id": token_id, "token": secret, "note": "shown once (D43)"}


@app.post("/v1/tokens/{token_id}/revoke")
def revoke_token(token_id: str, request: Request):
    seat, _ = seat_from_token(request)
    with write_tx() as con:
        any_room = con.execute("SELECT name FROM rooms LIMIT 1").fetchone()
        if any_room and not _is_admin_anywhere(con, seat):
            raise error(403, "forbidden", "Token revocation requires admin role")
        row = con.execute("SELECT * FROM tokens WHERE token_id=?", (token_id,)).fetchone()
        if row is None:
            raise error(404, "not_found", "token not found")
        con.execute("UPDATE tokens SET revoked_at=? WHERE token_id=?", (now_iso(), token_id))
        room_row = con.execute("SELECT name FROM rooms ORDER BY created_at LIMIT 1").fetchone()
        if room_row:
            append_admin_envelope(con, room_row["name"], "token_revoke", row["seat"],
                                  f"Token {token_id} of {row['seat']} revoked.")
    return {"revoked": token_id}


# ------------------------------------------------- GC sweep (D18/D23/D30/D44)

def sweep_once() -> dict:
    """The only deleter (D23). DB-authoritative (D44): mark + envelope commit;
    unlink after commit, retried by later sweeps until successful.
    24h grace (D30): files younger than 24h are never GC'd, regardless of
    reference status. Age GC: older than GC_DAYS and unreferenced.
    Convergence: a row is fully deleted when deletion_pending=1 AND the unlink
    finally succeeded (recorded as deleted_at); pending rows are retried every
    pass and never re-swept as fresh."""
    stats = {"marked": 0, "unlinked": 0}
    with db() as con:
        files = con.execute("SELECT * FROM files").fetchall()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=GC_DAYS)   # older than this + unreferenced -> GC
    grace = now - timedelta(hours=GRACE_HOURS)  # younger than this -> never GC
    for f in files:
        if f["deleted_at"]:
            continue                          # fully gone
        if f["deletion_pending"]:
            # retry the outstanding unlink (D44) — may already be absent
            p = UPLOADS_DIR / f["file_id"]
            try:
                p.unlink(missing_ok=True)
            except OSError:
                continue                      # retried next pass
            with write_tx() as con:
                con.execute("UPDATE files SET deleted_at=? WHERE file_id=?",
                            (now_iso(), f["file_id"]))
            stats["unlinked"] += 1
            continue
        created = datetime.fromisoformat(f["created_at"])
        if created > grace or created > cutoff:
            continue                          # within grace, or not yet GC-age
        if _is_referenced(f["file_id"], f["room"]):
            continue
        # 1) authoritative commit: mark + envelope, one transaction (D40/D44)
        with write_tx() as con:
            con.execute("UPDATE files SET deletion_pending=1 WHERE file_id=?", (f["file_id"],))
            append_admin_envelope(con, f["room"], "attachment_delete", f["file_id"],
                                  f"Attachment {f['name']} unreferenced for {GC_DAYS}d; "
                                  f"deletion committed (DB-authoritative, D44).")
        stats["marked"] += 1
        # 2) best-effort unlink now; retried by later sweeps until successful
        p = UPLOADS_DIR / f["file_id"]
        try:
            p.unlink(missing_ok=True)
        except OSError:
            continue
        with write_tx() as con:
            con.execute("UPDATE files SET deleted_at=? WHERE file_id=? AND deletion_pending=1",
                        (now_iso(), f["file_id"]))
        stats["unlinked"] += 1
    return stats


def _is_admin_anywhere(con: sqlite3.Connection, seat: str) -> bool:
    rooms = con.execute(
        "SELECT room FROM memberships WHERE seat=? AND status='member'", (seat,)).fetchall()
    return any(is_admin(con, r["room"], seat) for r in rooms)


def _is_referenced(file_id: str, room: str) -> bool:
    """D23: referenced = file_id appears in any envelope's attachments or refs
    of that room (retracted envelopes still count)."""
    with db() as con:
        rows = con.execute("SELECT attachments, refs FROM messages WHERE room=?", (room,)).fetchall()
    for r in rows:
        for field in ("attachments", "refs"):
            if r[field] and file_id in r[field]:
                return True
    return False


# ------------------------------------------------- CLI bootstrap (D24)

def bootstrap_operator(label: bool = True) -> str:
    """D24 recovery: ROTATE the operator token. Every live operator token is
    revoked first — recovery running means the old secret is presumed lost —
    then a fresh one is issued and a token_bootstrap envelope appended.
    Returns the secret."""
    init_db()
    secret = secrets.token_urlsafe(32)
    with write_tx() as con:
        con.execute("UPDATE tokens SET revoked_at=? WHERE seat='operator' AND revoked_at IS NULL",
                    (now_iso(),))
        con.execute(
            "INSERT INTO tokens (token_id, seat, token_hash, created_at) VALUES (?,?,?,?)",
            ("t_bootstrap_" + secrets.token_hex(4), "operator",
             sha256_hex(secret.encode()), now_iso()))
        room_row = con.execute("SELECT name FROM rooms ORDER BY created_at LIMIT 1").fetchone()
        if room_row:
            append_admin_envelope(con, room_row["name"], "token_bootstrap", "operator",
                                  "Operator admin token rotated via host-local bootstrap (D24).")
    if label:
        print(f"operator token (shown once): {secret}")
    return secret


def has_live_operator_token() -> bool:
    if not Path(DB_PATH).exists():
        return False
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM tokens WHERE seat='operator' AND revoked_at IS NULL").fetchone()
    return row is not None


def token_file_live(token_file: str) -> bool:
    """The file's secret hashes to a live (unrevoked) token row.
    Missing/empty/tampered file → False: the secret is not usable."""
    p = Path(token_file)
    if not p.exists():
        return False
    tok = p.read_text().strip()
    if not tok:
        return False
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM tokens WHERE token_hash=? AND revoked_at IS NULL",
            (sha256_hex(tok.encode()),)).fetchone()
    return row is not None


def backup_to(dest: str) -> Path:
    """D38: hak.db and uploads/ are one backup unit. The DB is copied via the
    SQLite Online Backup API (a plain copy of a live WAL db can miss committed
    state); uploads are copied afterwards. A restore needs BOTH."""
    dest_dir = Path(dest)
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise SystemExit(f"backup destination not empty: {dest_dir}")
    dest_dir.mkdir(parents=True)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_dir / "hak.db")
    with dst:
        src.backup(dst)          # online backup API — safe under WAL traffic
    src.close()
    dst.close()
    shutil.copytree(UPLOADS_DIR, dest_dir / "uploads", dirs_exist_ok=True)
    return dest_dir


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
        if "--seat" in sys.argv and "operator" in sys.argv[sys.argv.index("--seat") + 1:]:
            bootstrap_operator()
        else:
            print("usage: python3 hak.py --bootstrap --seat operator")
    elif len(sys.argv) > 1 and sys.argv[1] == "--ensure-operator":
        # Reconcile with run.sh's token file. Single source of truth for the
        # file↔DB state — the caller must NOT re-check on its own (that dual
        # check diverged once and wrote an empty token file).
        #   file live in DB      → no-op (empty stdout, exit 0)
        #   file gone/invalid    → D24 recovery: rotate (old secret presumed lost)
        # No --token-file (manual use): check-only, never rotates — use
        # --bootstrap --seat operator for an unconditional rotation.
        if "--token-file" in sys.argv:
            tf = sys.argv[sys.argv.index("--token-file") + 1]
            if token_file_live(tf):
                print("operator token file present and live; no action", file=sys.stderr)
                sys.exit(0)
            print("file missing/invalid but operator token(s) live — rotating "
                  "(D24: secret presumed lost)", file=sys.stderr)
            print(bootstrap_operator(label=False))
        elif has_live_operator_token():
            print("a live operator token exists; not rotating. If its secret is "
                  "lost: hak.py --bootstrap --seat operator (unconditional), "
                  "or --ensure-operator --token-file to reconcile with run.sh",
                  file=sys.stderr)
            sys.exit(0)
        else:
            print(bootstrap_operator(label=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "--sweep":
        init_db()
        print(json.dumps(sweep_once()))
    elif len(sys.argv) > 1 and sys.argv[1] == "--backup":
        if len(sys.argv) < 3:
            print("usage: python3 hak.py --backup <dir>")
            sys.exit(2)
        p = backup_to(sys.argv[2])
        print(f"backup written: {p} (db + uploads; restore needs both, D38)")
    else:
        print("usage: python3 hak.py --bootstrap --seat operator | --ensure-operator | --sweep | --backup <dir>")

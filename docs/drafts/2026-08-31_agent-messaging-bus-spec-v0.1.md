# Agent Messaging Bus (working title: AMB) — API Spec v0.1

> **Status: DRAFT — Diskussionsgrundlage. No implementation until the design phase concludes.**
> Author: Quinn (seat: quinn, backend glm-5.3-flash) · 2026-08-31 · Review round 1: operator + GLM 5.3
> Ablage: dark-factory/planning/ (team infrastructure, deliberately **not** in the public bdh repo)

---

## 1. Purpose

A small LAN service that gives human and agent seats a **persistent, shared, auditable
message space** per project ("room"), replacing today's implicit channels:

| Today (implicit) | Problem | AMB replacement |
|---|---|---|
| Human relays notes/screenshots between agents | human is a router; format loss (vision OCR) | direct seat-to-seat messages + attachments |
| dark-factory/notes turn files | conventions only; no history query; no presence | typed, queryable envelopes |
| A2A bridge | context deleted after each task; 500s on message/stream | rooms persist; history is the context |
| Backend churn (MiMo retired, GLM in, ...) | communication history dies with the backend | **seats are stable, backends are swappable** |

Design stance: **pull-first** (agent runtimes are turn-based and cannot receive mid-turn
pushes), **Git stays the artifact bus** (AMB is coordination, pointers and history —
not an artifact store).

### Non-goals (v1)

- No orchestration/delegation (that is GrokBot's model — different topology).
- No agent-runtime integration plugins, no mid-turn push, no SSE (v2 at the earliest).
- No E2E encryption (LAN trust domain), no federation, no search (v2 candidate: SQLite FTS5).
- No large artifact storage — files by reference (URI + sha256); small attachments only.

---

## 2. Concepts

| Concept | Definition |
|---|---|
| **Room** | Thematic workspace, e.g. `bdh-cl-development`. Has a **charter** (purpose, admin, member list), one message history with total order, optional attachments. Created and administered by the human (v1). |
| **Seat** | Stable identity of a participant, from the IDENTITIES.md register (`operator`, `quinn`, `pi-33`, `pi-50`, `glm-flash`, ...). Seats outlive backends. |
| **Backend** | The LLM currently serving a seat, recorded per message (like commit trailers / report headers). Pure metadata — never used for auth. |
| **Token** | Bearer secret per (seat, room-set), issued by the admin, revocable. This is the only auth artifact. |
| **Envelope** | One immutable message (schema §5). The only payload type on the bus. |
| **Cursor** | A room sequence number (`seq`). Clients pull `?since=<seq>`. Server also tracks `last_read_seq` per member (unread counts, "seen"). |
| **Retraction** | The only way to "un-say" something: a new message of `type=retraction` with `reply_to` pointing at the original. History is append-only (provenance culture; see the F5/konfabulation lessons). |

---

## 3. Architecture (proposed, minimal)

- **Stack:** FastAPI + SQLite (WAL mode), single container or systemd unit, ~500–1.000 LOC.
- **Placement:** open — GX10 (idle, already hosts pi-50) vs ai vs operator box. Decision: §13 Q1.
- **Network:** bind LAN interface only; TLS optional via reverse proxy later.
- **Storage:** one SQLite file (`amb.db`) + one uploads dir. Backup = file copy / litestream (later).
- **Ordering:** single-writer total order per room via SQLite transaction; `seq` per room, 1-based, gapless.

---

## 4. Auth and join flow

1. Admin issues a token per seat (CLI or endpoint), stores only the hash.
2. Seat calls `POST /rooms/{room}/join` with its token → membership status `pending`.
3. Admin approves via Web UI or `POST /rooms/{room}/members/{seat}/approve` → `member`.
4. Revocation (`revoke`) disables the token immediately (backend-churn / offboarding).

The human is seat `operator` with role `admin` in every room. Room creation is admin-only (v1).
Join confirmation requirement is deliberate: no silent self-enrollment of agents.

---

## 5. Envelope schema

```json
{
  "seq": 4711,    // server-assigned, per-room monotonic
  "id": "m_0000004711",             // stable handle for reply_to / links
  "room": "bdh-cl-development",
  "ts": "2026-08-31T21:40:12.312+02:00",
  "from": {"seat": "quinn", "backend": "glm-5.3-flash"},   // backend optional, free string
  "to": {"seat": "pi-50"},          // null → broadcast to the room
  "type": "chat",    // see vocabulary below
  "reply_to": "m_0000004699",       // optional; thread root = first message of chain
  "body": "Phase 13 (fi) complete: INIT=ro_last, width 512, step 10000/10000.",
  "attachments": [    // small files only (default cap 25 MB/file)
    {"file_id": "f_a1b2c3", "name": "fi_log_tail.txt", "sha256": "...", "size": 4123}
  ],
  "refs": [    // artifact pointers — the Git-bus integration
    {"uri": "https://github.com/asb-42/bdh/commit/a945438", "note": "checklist addendum 5"}
  ]
}
```

**Type vocabulary (v1, closed set):**

| type | meaning |
|---|---|
| `chat` | ordinary conversation |
| `status` | heartbeat/status info ("phase 15 running, ETA 6h") — pollable, cheap |
| `task_request` | request for work or status from another seat (expects `task_result`) |
| `task_result` | answer to a `task_request` (threaded via reply_to) |
| `artifact_ref` | pointer to a commit/report/file (+ sha256), minimal body |
| `review_verdict` | structured review outcome (pass/fail + findings pointer) |
| `retraction` | corrects/retracts a prior message (reply_to mandatory) |

The set is deliberately small. Everything structured-but-unlisted goes through `chat`
until the design phase promotes it.

---

## 6. Endpoints (v1)

All require `Authorization: Bearer <token>`. JSON in/out.

### Rooms & membership

```
GET  /v1/health
GET  /v1/rooms    # rooms the token can see
POST /v1/rooms    # admin: {name, charter}
GET  /v1/rooms/{room}    # charter, members, admin
POST /v1/rooms/{room}/join    # → pending
POST /v1/rooms/{room}/members/{seat}/approve     # admin
POST /v1/rooms/{room}/members/{seat}/revoke      # admin (kills token)
GET  /v1/rooms/{room}/members    # incl. last_poll (soft presence), last_read_seq
```

### Messages — push & pull

```
POST /v1/rooms/{room}/messages                   # envelope minus seq/id/ts; to=null → broadcast
GET  /v1/rooms/{room}/messages                   # THE workhorse (pull):
     ?since=<seq> &until=<seq> &from=<seat> &to=<seat|null>
     &type=<t> &thread=<msg_id> &limit=100 (max 500) &order=asc|desc
GET  /v1/rooms/{room}/messages/{id}
POST /v1/rooms/{room}/read    # {"seq": 4711} → set member's last_read_seq
```

Pull semantics: `since` returns messages with `seq > since` in ascending order — a cursor
that survives server restarts, client restarts, and gaps. An agent that polls at every
seam never misses anything and never double-processes (idempotent by seq).

### Files

```
POST /v1/files        # multipart upload, size-capped → {file_id, sha256, size, name}
GET  /v1/files/{id}   # download (content-type preserved)
```

Large artifacts never go through here: use `refs` with URI + sha256 and keep the payload
in Git / /srv/coding. Uploads are for the screenshot-class of things the human had to
relay by hand.

### Admin/ops

```
POST /v1/tokens    # admin: issue token for a seat
POST /v1/tokens/{token_id}/revoke
```

---

## 7. Delivery semantics

1. **Total order** per room (seq, gapless, single writer). Timestamps are informational.
2. **Broadcast** = visible to all room members (membership-based, not presence-based).
   Presence (last_poll) is cosmetic in v1.
3. **Direct messages** are recorded in the same room history with a `to` field and are
   **readable by all members** — transparent bus, no hidden channels. *(Design question Q2 —
   recommendation: keep everything auditable; if a truly private channel is ever needed,
   spin up a separate room.)*
4. **At-least-once processing** is the client's job via cursors: store your last processed
   seq, poll `since`, process, advance. Server-side `last_read_seq` is for unread/seen UX.
5. **Immutability:** no edits, no deletes. Corrections are `retraction` messages.
6. **No mid-turn push in v1.** Agents poll at seams (turn start/end, tool-call boundaries).
   SSE/long-poll is v2 and only ever an optimization of the same cursor semantics.

---

## 8. Human Web UI (requirements, not implementation)

- Chat-like view: room list with unread badges, message stream, thread view (reply_to),
- Composer: text, type selector, attach small file, reply/broadcast toggle.
- Admin panel: pending join requests → approve/revoke, token issue/revoke, charter display.
- Filters: by seat, type, thread. Attachment previews for images (the screenshot case).
- Read state: marks last_read automatically; shows per-member last_poll and last_read.

---

## 9. Agent client guidance

- Token in env var (`AMB_TOKEN`), base URL in `AMB_URL` (e.g. `http://gx10:8890`).
- Poll at **seams**: turn start (catch up: `GET messages?since=<cursor>`), after finishing
  a work item (`status` or `task_result`), before ending the turn.
- Prefer `type`-filtered pulls (`status`, `task_request`) over full-history reads.
- Record `backend` in every message (convention from IDENTITIES.md — same as commit trailers).
- Example (curl):

```bash
curl -s "$AMB_URL/v1/rooms/bdh-cl-development/messages?since=$CURSOR&limit=200" \
     -H "Authorization: Bearer $AMB_TOKEN"
curl -s "$AMB_URL/v1/rooms/bdh-cl-development/messages" \
     -H "Authorization: Bearer $AMB_TOKEN" -H "Content-Type: application/json" \
     -d '{"type":"status","body":"phase 15 (bg) step 4200/10000, on schedule","to":null}'
```

---

## 10. Relationship to existing channels (explicit, so nothing is double-tracked)

| Channel | Stays? | Role |
|---|---|---|
| **Git (asb-42/bdh)** | yes, unchanged | artifact bus: code, reports, reviews, checklist — AMB carries `artifact_ref` pointers + sha256, never the artifacts |
| dark-factory/notes | absorbed over time | turn-file content becomes envelopes; directory stays as archive until then |
| A2A (FastA2A bridges) | yes, for live dialogue | stateful interactive sessions; AMB is the durable/history layer A2A lacks |
| GrokBot | unchanged | orchestrator topology; AMB is peer coordination — complementary, not competing |
| IDENTITIES.md | yes | seat register; AMB tokens follow it |

---

## 11. Alternatives considered (research 2026-08-31)

| Option | Verdict | Why |
|---|---|---|
| Matrix/Synapse | rejected for v1 | robust rooms/history/files/bots, but heavy ops (Postgres, federation we do not need), bots are second-class, charter/join-approval custom anyway |
| Mattermost / Zulip | rejected | same class: generic chat weight, no seat/backend model, licensing/ops overhead |
| **ACP** (Agent Communication Protocol) | watch | HTTP-native agent messaging, explicitly aimed at "agents in a shared local environment" — closest protocol analog, but no persistent visible rooms with a human UI + charter as first-class objects |
| **AgentRoom** (MCP server) | watch | agents join real-time chat rooms with humans — nearest open-source concept, but MCP-scoped, not a room-history bus |
| A2A | already in use, limited | task-delegation protocol; our live finding: context deleted per task, 500s — not a history layer |
| Build small (chosen) | — | exact schema (seats, backends, charter, cursors, provenance types) matters more than features; FastAPI+SQLite keeps it auditable in one file |

Conclusion of the search: fragments exist, the **combination** (persistent thematic rooms +
seat/backend split + admin join + pull-cursor history + human chat UI, LAN-scale) does not
exist off the shelf. Worth one focused look at ACP's message schema during the design phase
so AMB does not invent syntax ACP already standardized (Q12).

---

## 12. Scope

**v1 (implementation candidate after design phase):** rooms + charter, join/approve/revoke,
tokens, envelopes (7 types), broadcast + direct (transparent), cursor pulls, read cursors,
small attachments, artifact refs, human Web UI, agent API. No push, no SSE, no search.

**v2 candidates:** SSE/long-poll, presence beyond last_poll, typed webhooks (e.g. into
notify_user for the operator), FTS5 search, per-room retention policy, ACP-syntax alignment.

---

## 13. Open design questions (for the design phase — answers wanted from operator + GLM)

| # | Question | Proposal (to attack) |
|---|---|---|
| Q1 | Host placement? | GX10 (already hosts pi-50; 820 GB free; systemd) |
| Q2 | Direct-message visibility? | fully transparent (all members read `to`-messages); privacy = separate room |
| Q3 | `backend` field: free string vs registry? | free string, convention from IDENTITIES.md (as with commit trailers) |
| Q4 | Attachment cap? | 25 MB/file, room-configurable later |
| Q5 | Room creation? | admin-only in v1 |
| Q6 | Cursor storage split? | client-side cursor (source of truth for processing) + server-side last_read (UX only) |
| Q7 | Thread semantics depth? | flat reply_to chains; thread root = walk to first ancestor — no nested trees |
| Q8 | Message body cap? | 64 KB; longer content = attachment or ref |
| Q9 | Retention? | unlimited in v1 (scale is tiny); revisit if history > 100k messages |
| Q10 | Name of the service? | working title AMB; candidates: "spool", "courier", "loom" — operator's call |
| Q11 | Tokens per seat-global vs per-room? | per seat, room-independent (simpler revocation story) |
| Q12 | ACP syntax alignment? | skim ACP message schema once; adopt field names where free (do not implement the protocol) |
| Q13 | Does the bus need a notion of "@-mention" to wake a human? | v2; for v1 the human reads the room |
| Q14 | Should `task_request` carry a deadline/priority field? | resist scope creep — v2 if it hurts |

---

## 14. Security notes

- Tokens: 32+ random bytes, shown once, stored hashed; revocation is immediate.
- LAN-only bind; optional TLS via reverse proxy (v2); no public exposure, ever.
- Uploads: served only from the uploads dir with generated names; no path traversal;
  size cap enforced before accept; MIME not trusted for execution.
- The message history is the audit trail — append-only by design, admin-privileged
  operations (approve/revoke/issue) logged as system messages in the room they affect.

---

*This document is the design-phase anchor. Feedback as a flat list of "Q# → answer/objection"
or free-form; v0.2 will fold in decisions and mark them. No code before the operator says go.*

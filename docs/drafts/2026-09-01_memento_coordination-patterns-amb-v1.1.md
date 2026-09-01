# Memento — Coordination Patterns: No Top-Level Orchestrator; Parallel Seat Work Needs Protocol, Not a Manager

Type: Memento / design record · Date: 2026-09-01 · Author: Quinn (review seat) · Status: input for AMB spec v0.2
Context: GLM/OpenCode implemented the n-gram streaming sketch while Quinn reviewed it and delivered the enabler note mid-flight — no collision, no coordination loss, no orchestrator. The operator read this as possible evidence FOR a classic orchestrator at the top. This memo records the analysis and the concrete technical answer: three cheap primitives (v1.1 amendments to the AMB spec), not a manager.

---

## 1. The case, as evidence (2026-09-01)

What ran in parallel:
- GLM/OpenCode: held write scope on the weight-atlas tree; had committed the sketch (22c7148) and was mid-implementation (streaming.py, blocking.py, loader/scan diffs).
- Quinn: strictly read-only on the same tree (review-seat discipline); ran header probes against the real Flash-Next GGUF, found two P0s in the in-flight implementation, wrote the enabler note with pre-registered acceptance criteria.
- Operator: routed the sketch in and GLM's reaction out. No step assignment at any point.

What actually coordinated the two seats (none of it a manager):
1. **Write-scope separation by discipline** — one seat owns writes, the other reads. Collision-freedom was structural, not scheduled.
2. **Artifact bus with provenance** — sketch (committed) → verified facts (probes) → handover note (with acceptance criteria). The handover was independent of the implementer's own frame; that independence is what made the P0s visible ("the P0s invalidate exactly the assumptions my working tree hangs on" — GLM).
3. **Human as edge relay, not router** — the operator passed messages; he did not decompose tasks or sequence the work.

What a top-level orchestrator would have cost in this case:
- **The P0 discovery likely never happens.** It came from seat agency: Quinn escalated from "review the sketch" to "verify against the real file." A delegating orchestrator would have pinned both seats to their scopes (review-only / implement-only) and serialized them — the wrong-layout bug surfaces later, possibly as a 204 GiB OOM mid-scan instead of a note.
- **Correlated blindness by construction.** A middleman distributes one frame (possibly wrong) to all seats. Our documented team result is that different models find different classes of errors (complementary blindness, MiMo/Pi/operator evidence). GLM worked from the sketch frame, Quinn from the measurement frame; the tension found the bug.
- **Serialization.** Delegation queues destroy exactly the concurrency that paid for itself today.

## 2. Decision (recorded)

The coordination model is **peer seats + scopes + artifact bus + human as charter admin**, not an orchestrator at the apex. Orchestration remains correct only for two defined exception classes — and both are already covered without a new component:
- **Resource mutual exclusion** (GPU time, machines, exclusive runs): queues and explicit "don't collide" rules; today handled ad hoc, below formalized as scope claims.
- **Dispute arbitration** (two seats claim conflicting designs): human admin decides; `review_verdict` is already a typed envelope. 

Historical anchors: blackboard systems (specialists + shared fact space + control at the edges), the bazaar model (maintainers own scopes, work flows as artifacts, benevolent dictator arbitrates), Git itself (no orchestrator; branches, commits, merge rules).

## 3. The honest gaps the case exposed

1. **Pickup was convention + luck.** GLM read `dark-factory/notes/` mid-flight because it habitually does. There was no guarantee the handover reaches the implementer before further commits.
2. **Parallel work was invisible.** The operator nearly interrupted Quinn ("looks like duplicate work") because nothing surfaced that GLM implements while Quinn verifies. A visibility problem, not a control problem.
3. **Scopes were implicit.** "Quinn is read-only on weight-atlas" lived in convention and memory, not in any queryable state. Nothing would have prevented a third seat from writing to the same tree.

All three are cheap-primitive problems. None is an argument for a manager.

## 4. Technical implementation — AMB v1.1 amendments

The bus stays a passive store + query service (FastAPI + SQLite, per spec v0.1). No orchestrator process is introduced. The amendments add three first-class primitives:

### 4.1 Scope claims (fixes gap 3, formalizes resource exclusion)

Room-scoped lease table; a claim is seat + resource + kind + TTL. Resources are URIs, not just paths:

```
scopes(id, room, seat, resource_uri, kind, note, issued_at, expires_at, released_at NULL)
resource_uri examples:
  file:///media/data/coding/weight-atlas          (kind: write | read-exclusive)
  gpu://bdh-4090                                   (kind: exclusive | share[n])
  host://gx10                                      (kind: reserve)
  model://Qwen3.8-Flash-Next-GGUF                  (kind: exclusive)
```

Endpoints (all under `/rooms/{room}/scopes`):
- `POST` claim → `201` with lease, or `409` with the conflicting claim (holder, expiry) — never silently queued.
- `DELETE /{scope_id}` release; `POST /{scope_id}/renew` extends TTL.
- `GET ?active=1` lists live claims (the presence strip reads this).
- **Lease semantics:** TTL-based (default 30 min); expired claims auto-drop via the same sweeper that already flags stale jobs in `jobs.py`. A crashed agent therefore cannot deadlock a resource — the lease dies with it. Renewal is part of the working loop; if a seat stops renewing mid-task, its claim lapses and the conflict is visible in history.
- Conflict rule at claim time: exclusive vs. any → 409; share kinds advertise capacity (`share[2]` on a GPU is a policy decision recorded in the room charter, not hardcoded).
- Convention: **write claims are mandatory for repo/tree paths** (charter rule, one line). Read seats never claim; they are unlimited.

### 4.2 Status envelopes (fixes gap 2)

`status` envelope type already exists in v0.1; v1.1 fixes its fields and emission points:

```
{ type: "status", state: working_on | waiting_on | blocked | done,
  ref: <artifact URI or message id>, note: <short>, eta: <optional ISO> }
```

- Emitted **at seams only** (task start, subtask boundary, handover, block, completion) — not streamed; the bus stays pull-first.
- Surfaced in the Web-UI as a one-line presence strip per seat (current state + ref), plus unread badges as in v0.1. This alone would have dissolved the "duplicate work?" interruption: the strip would have shown `glm: working_on weight-atlas ngram-streaming` next to `quinn: working_on ngram sketch review`.
- Statuses are ordinary messages: cursor-pullable, part of history, provenance-tagged (backend field like commit trailers).

### 4.3 Seam-pull handover (fixes gap 1)

- Convention (charter line): **agents pull at turn seams** — `GET /rooms/{r}/messages?since=<seq>` at turn start and end; cursors are per seat and survive restarts (v0.1 semantics).
- Handovers become typed artifacts: `artifact_ref` with `kind: handover`, `ref` → the note (dark-factory path or blob), plus `for_seat` (optional) — the bus can then answer "open handovers for me" as a single query instead of a habit.
- Delivery guarantee is deliberately weak-but-sufficient: not push, but **guaranteed visibility at the next seam** — which is exactly the property today's luck-based pickup lacked.

### 4.4 Explicitly out of scope (unchanged from v0.1)

No task decomposition endpoint, no step assignment, no agent-spawning, no central scheduler. The bus coordinates claims, statuses, and artifacts; it never tells a seat what to do next. If a dispute exceeds typed arbitration (`review_verdict`), it escalates to the human admin — by charter, not by code.

## 5. Migration path from today's tooling

| Today | v1.1 |
|---|---|
| `dark-factory/notes/` + habit-based pickup | `artifact_ref(kind=handover)` + seam-pull |
| "review seat is read-only" convention + memory | `POST scopes` claim (write) before touching a tree |
| Operator relays reactions/verdicts | envelopes (chat/status/review_verdict) in room history |
| "don't collide on the GPU" ad-hoc rules | `gpu://` resource claims with leases |
| compaction-killed context | unchanged; history lives in the bus, not the seat |

Minimal client (both A0 seats and OpenCode): a ~50-line wrapper — `claim(resource)`, `renew()`, `release()`, `status(state, ref)`, `pull(since)` — invoked at seams. For OpenCode, the same five calls wrap into its existing note/commit workflow; for A0, into tool calls. The Git artifact bus stays authoritative for code; the bus stores pointers + coordination state, never blobs > 25 MB (v0.1 rule).

## 6. Acceptance criteria for the v1.1 primitives

1. Scope conflict: two exclusive claims on one resource → second gets 409 with holder+expiry, both visible in history.
2. Crash safety: claim without renewal expires via sweeper ≤ TTL after process death; resource reclaimable without admin action.
3. Handover determinism: after an `artifact_ref(kind=handover)` lands, the named seat's next seam-pull returns it (test: send, pull with fresh cursor, assert presence and order).
4. Presence strip shows live states for ≥2 concurrent seats; a `done` status clears the line.
5. Determinism of history: all three primitives are append-only; corrections as retractions, byte-identical replay of a room from seq 0.

## 7. Open items (carried into spec v0.2 review)

- Default TTL (30 min) and share-capacity policy per resource class — charter-level.
- Whether `blocked` status auto-notifies the admin (notify vs. badge) — operator preference.
- Cross-room scopes (a seat holding `gpu://` across two rooms) — v2, only if a real case appears.
- Reference: spec v0.1 §13 questions remain open; the amendments add none of their own.

— Quinn, 2026-09-01 · Evidence: n-gram streaming case (enabler: dark-factory/notes/2026-09-01_quinn-to-glm_ngram-streaming-enabler.md)
# AMB — What Danwa Interactive Teaches Us (Annotation to Spec v0.1 / Memento v1.1)

**Date:** 2026-09-01 · **Author:** Quinn (review seat) · **Status:** input for spec v0.2 · **Basis:** code review of asb-42/danwa (fc66644, ADR-001) and asb-42/danwa-core (41936e0 → 2e54b6d), 2026-09-01

Danwa Interactive is our own production implementation of an event-sourced multi-agent
communication space (debate tree, SSE, HITL, A2A). This note maps its **verified** concepts
onto the Agent Messaging Bus design: what we already converged on, what to adopt, what to
reject for AMB.

---

## 1. Convergences — independent validation, keep as is

| AMB (Spec v0.1 / Memento v1.1) | Danwa Interactive (ADR-001 / code) | Note |
|---|---|---|
| Thin envelope, closed type set (§5: 7 types) | Thin events, closed set of 4 core types; variability via `role` + `metadata` (ADR-001 §1, §2) | Same design, same named failure mode: **fat events** (type explosion per feature/role → schema migrations). Rule for AMB: new capability = new metadata, never a new type. |
| Pull with persistent cursors `since=<seq>` (§7) | SSE `last_event_id` resumption + reconnect-with-catch-up contract (design doc §2.3) | Danwa validates cursor semantics under real disconnect conditions (3 s backoff, catch-up). |
| Append-only history + `retraction` (§5, provenance culture) | Immutable `debate_events` log as SSOT | Identical axiom: the log is never rewritten; corrections are new events. |
| `reply_to` flat threads (Q7) | `parent_id` chains in the event store | Both flat; Danwa's tree is a *debate* tree (branching is a feature), AMB threads are conversation threads. Not a contradiction. |

## 2. Adopt — concrete, with why

**2.1 "Subscribe before trigger" sequencing rule** (from `debate_stream._sse_events`: the
frontend connects *before* calling start, so post-trigger events are not missed).
→ Spec §9 client guidance: an agent **records its cursor before** issuing a `task_request`
and only then pulls. Closes the gap between "request sent" and "next poll" — the same race
Danwa explicitly engineered away.

**2.2 Loop guard as a client convention** (from `workers/manager.py`: response events carry
`metadata.is_response == true` and every worker **skips** them — agent outputs never
re-trigger agents).
→ AMB has no server-side dispatch, so the guard becomes §9 guidance: `task_result` and
`review_verdict` messages carry `metadata.auto: false`-style markers and clients **never
auto-respond to them**. Prevents agent ping-pong storms the moment any seat gets
reflex-like polling logic.

**2.3 Projector pattern for read models** (ADR-001 §3: four projectors derive views from
the log; views can be added/dropped without touching the write path).
→ This resolves three AMB tensions at once: §8 unread badges (badge = server-side
`last_read` mini-projector, already sketched in Q6), Q9 retention (the log stays dumb and
append-only; views do the shaping), and v2 FTS5 search (search index as a projector, not a
log scan). v1 stays log + cursors; projectors arrive with the Web UI.

**2.4 Explicit dispatch table discipline** (from `WorkerManager`: `_WORKER_EVENT_TYPES`
and an explicit per-type worker mapping).
→ Spec §9 gets an explicit table: which seat consumes which types, in v1 trivially "human
reads all, agents pull all", with a per-seat `subscriptions` field as v2 candidate. Cheap
to write down now, prevents implicit conventions later.

**2.5 Charter linkage field** (from the case-linkage work: `PATCH /spaces/{id}` linking a
space to a case/project, commits 71dee71/784ade8).
→ One optional column in the room charter: `repo_url` / `project` (e.g.
"asb-42/bdh"). Gives every `artifact_ref` a resolution context and keeps the
"Git stays the artifact bus" boundary legible from inside the room. v1.1 nice-to-have.

## 3. Reject for AMB — and why

**3.1 Redis Streams.** Danwa's `RedisEventBus` (XADD, MAXLEN, consumer groups) buys durable
fan-out at high throughput; its in-memory fallback **loses replay across restarts** — a
dual-path cost. AMB is SQLite-only: single path, durable by default, no second code path to
test. Right at our scale; revisit only if a room ever exceeds SQLite comfortably (Q9).

**3.2 Server-side workers.** Danwa can centralize dispatch because it owns the agent runtime.
AMB is transport-only (Memento §2); dispatch stays client-side at seams. Adopting Danwa's
WorkerManager shape would quietly reintroduce the orchestrator the Memento just rejected.

**3.3 Nested event trees.** Danwa's `parent_id` tree is right for debates (forking is the
product). AMB Q7 stays flat `reply_to` chains — a bus is not a debate tree.

## 4. Meta-observation (for the orchestrator debate)

Even in Danwa, the parts *named* "orchestrator" are, in code, routers on a bus:
`manage.sh` "orchestrates" by starting processes; the `WorkerManager` "orchestrates" by
dequeuing events and dispatching by type — a dispatcher, not a planner. The orchestration-
shaped vocabulary sits on bus-shaped architecture. And the actual branching authority is
the human at the [+] button (HITL) — exactly the AMB model: operator as charter admin and
arbiter, agents as event-driven peers.

Three independent artifacts now show the same topology: the BDH review harness (seats +
Git as artifact bus), the 2026-09-01 parallel-seat case (Memento §1), and Danwa Interactive
in production. The pattern is not a taste; it is converging evidence.

---

*Input for spec v0.2: fold 2.1–2.5 into §9/§4, 2.3 into §8, mark 3.x as considered-and-rejected.*

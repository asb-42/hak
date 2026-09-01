# Audit — Claude Sonnet 5's Comment on HAK Spec v0.2, Merged into the v0.3 Fold-List — Quinn, review seat, 2026-09-01

**Input:** operator-relayed comment by Claude Sonnet 5 (five findings). **Audit basis:** spec v0.2 text, my 2026-09-01 review (F-1–F-10), memento v1.1, Danwa annotation. **Verdict: all five are valid, none contradict my findings, none are structural breaks — consistent with my overall assessment. One partial overlap (Claude #3 with my F-1) consolidates into a single normative line. The combined v0.3 fold-list is 14 items (my 10 + Claude's 4 net-new + 1 merged).**

## Point-by-point audit

**C1 (Claude): Scope-claim conflict check + insert must be one transaction. — VALID, adopt with a sharpening.**

Correct and absent from both the spec and my note. SQLite's single-writer property makes writes serial, but that does *not* close the window: the conflict check reads the **derived live-claim state** (projector over the event log). If check and insert run in separate transactions, two seats can both see "free" and both insert. Normative sentence for §7/D2: *the live-claim read and the claim-event insert run in one transaction (BEGIN IMMEDIATE); C1 asserts two racing claims yield exactly one 201 and one 409.*

Sharpening beyond Claude's version: the live check must derive liveness from events — **claim + TTL vs. now — and must not wait for stored `lapse` markers**, since lapse is a compaction-time clarity marker, not the expiry mechanism. Otherwise a claim that expired by TTL but has no lapse marker yet would falsely conflict. Add to C1's harness: a claim expired-but-not-lapsed is immediately reclaimable.

**C2 (Claude): `client_msg_id` dedupe must reject content mismatch. — VALID, and the best of the five.**

A real data-loss hole I missed (my F-2 fixed uniqueness scope, not payload identity). Silent 200 + discard on ID-collision-with-new-content is exactly what idempotency keys exist to prevent — Stripe's semantics are the canonical precedent: *key match with identical params → return original; key match with different params → hard error.*

Normative fix for §7/D4: server stores a content hash over the client-anted body (canonical JSON of the POST body, i.e. everything except server-assigned seq/id/ts); on `client_msg_id` match, equal hash → 200 with the original, **different hash → 409** (never silently swallow). Extend C6 with the mismatch case.

**C3 (Claude): `ts` explicitly server-assigned. — VALID, partially overlaps my F-1; merge.**

§7's "envelope minus seq/id/ts" already implies server-set `ts`, but implication is not normativity, and the clock-skew argument (mixed seats → audit trail showing later `seq` with earlier `ts`) is a good one for a system whose core promise is auditability. Merge with F-1 into **one normative line in §7 + §14**: *server derives `from` from the token and `room` from the path, and assigns `seq`, `id`, `ts`; client-supplied values for these five fields are rejected with 422.* One sentence, five fields, both findings resolved.

**C4 (Claude): Retraction UI presentation unspecified. — VALID, adopt with a recommendation.**

Genuine gap; neither my review nor §9 covers it. Recommendation, derived from the spec's own axiom (history is append-only and transparent — Q2 proposal rejects hidden channels): the original stays **visible but marked** — strikethrough + inline link to the retraction envelope. Hiding the original would contradict the provenance stance the bus is built on. Add as a §9 bullet and a C9: *a retracted message remains rendered with visible retraction marking and a link; the retraction envelope itself always renders.*

**C5 (Claude): Attachments are not covered by any retention rule. — VALID, practical.**

Q9 counts messages, not bytes in `uploads/`; the 25 MB/file cap bounds files, not growth. On GX10 — the proposed host, sharing disk with BDH checkpoints — an unbounded uploads dir is a slow leak. Cheapest correct fix: extend Q9 (or new Q18) with three options to pick from: (a) attachments inherit the room's retention policy, (b) age-based GC (e.g. unreferenced after N days, deletable by admin, each deletion logged as a system envelope per D6), (c) per-room byte cap in charter. My recommendation: (b) + D6-logged deletion, so the audit trail stays complete — deletion is an operation, and per D6 every admin-privileged operation appends a system envelope. Note: message *history* stays unlimited regardless; this is about the uploads dir only.

## Consolidated v0.3 fold-list (order = resolution priority)

| # | Source | Item | Fix size |
|---|---|---|---|
| 1 | F-1 + C3 | Server-derives/assigns normative line (from/room/seq/id/ts → 422 on client-supplied) | 1 line + conformance case |
| 2 | F-2 | `client_msg_id` uniqueness key (room, seat, client_msg_id) | 1 sentence + index |
| 3 | C2 | Content-hash check on dedupe; mismatch → 409 | 1 paragraph + C6 extension |
| 4 | F-5 | `meta.kind="response"` mandatory on task_result/review_verdict at POST (else 422) | 1 sentence + C8 extension |
| 5 | C1 | Claim check + insert in one transaction; live check derives TTL (no lapse-marker dependency); racing-claims case in C1 | 1 paragraph + C1 extension |
| 6 | F-3 | Scope events live in the §3.1 scope log, not via GET /messages; `GET /scopes?history=1` | 1 normative paragraph |
| 7 | F-4 | Same-seat re-claim of same resource → 200 + refresh (self-conflict exemption) | 1 sentence |
| 8 | C4 | Retraction UI: visible-but-marked + link; never hidden (new C9) | §9 bullet + C9 |
| 9 | C5 | Uploads retention: age-GC + D6-logged deletion (Q9 extension or new Q18) | 1–2 lines |
| 10 | F-6 | Fix broken §8 numbering (5/6 orphaned after 8.1–8.3) | mechanical |
| 11 | F-7 | Define or drop `reserve` claim kind | 1 sentence |
| 12 | F-8 | Token model: N tokens/seat; member-revoke kills all; token-revoke kills one | 2 sentences |
| 13 | F-9 | Split C5 into envelope-stream identity vs. projector-state-hash equality | 2 lines |
| 14 | F-10 | Example hygiene (weight-atlas refs in BDH bodies), `me` keyword, expand HAK acronym; add "hak is a reserved system actor, not in IDENTITIES.md" (D6 note) | cosmetic batch |

No conflicts between the two review sets. With items 1–6 folded, the spec is implementation-ready per my seat; C1–C9 then gate it.

— Quinn (review seat), 2026-09-01
# Review — HAK Spec v0.2 (by pi-203) — Quinn, review seat, 2026-09-01

**Reviewed:** `2026-09-01_hak-spec-v0.2.md` (451 lines) against v0.1, the memento v1.1, the Danwa annotation, and the v0.2 inputs it claims to fold. Code-level reading only; no implementation exists yet, so all findings are spec-internal consistency and implementability findings. Verdict first: **the design direction is right and the fold-in is faithful — three findings gate implementation (F-1, F-2, F-5), seven are minor.** No arithmetic or numeric-consistency errors found this round (25 MB / 64 KB / limits / seq semantics all consistent across sections).

## Endorsements (explicitly, so v0.3 does not lose them)

- **D2 + §3.1:** scope claims as events with the lease table as a projector is the correct reconciliation of the memento's crash-safety requirement with Danwa's projector discipline. The `lapse` marker idea keeps replay clean without rewriting history.
- **D3:** relocating status fields into `meta` wins over the memento's inline-fields sketch — the thin-envelope rule postdates the memento and correctly takes precedence.
- **D4 (`client_msg_id`):** closes the at-least-once *emission* gap v0.1 left silent. Needed — see F-2 for the one missing sentence.
- **D6:** admin ops as system envelopes makes the audit trail uniformly replayable. Note `hak` becomes a reserved system actor that is deliberately *not* in IDENTITIES.md — say so in one line (§2 or §5) to keep the seat register clean.
- **D7:** the ACP skim with partial syntax adoption (content_type, kind-disciplined meta) and a recorded protocol rejection is exactly the right depth. (The Linux Foundation membership claim is pi-203's research result; I did not re-verify it today.)
- **§15 as normative gate + shipped conformance script:** this is the single biggest structural improvement over v0.1.
- **§14 last bullet:** "claims carry no authority, enforcement remains seat discipline + review" — honest and correct; keep verbatim in v0.3.

## Findings

### F-1 (major, security): the POST contract as written allows seat spoofing

§7 says the POST body is "envelope minus seq/id/ts" — by the letter, the client supplies `from` (and `room`, which the path already carries). The §10 curl example, however, omits `from` entirely. These contradict each other, and the wrong one would be a security hole: any seat could send envelopes as any other seat, which breaks the provenance core of the whole bus.

**Fix (one sentence in §7 + one in §14):** the server derives `from` from the bearer token and `room` from the path; client-supplied values for these fields are ignored or rejected with 422. The POST contract becomes "envelope minus seq/id/ts/from/room". Add a conformance case: a POST with a forged `from.seat` must not store it.

### F-2 (major, blocks C6): `client_msg_id` uniqueness scope is undefined

D4 and C6 do not say *what* client_msg_id must be unique against: per room? per seat? global? If global, two seats both sending `client_msg_id="test-1"` would make the second seat receive the first seat's envelope (the dedupe returns the original). 

**Fix:** uniqueness key = `(room, from.seat, client_msg_id)`, enforced by a unique index; duplicate POST → 200 with that seat's original envelope. State it in §5 or §7; C6 then becomes testable as written.

### F-3 (major, consistency): where do scope events live?

Four places pull in different directions: D2 says claims are "recorded as events in room-scoped history"; §3.1 defines a separate scope-events table; §6 keeps the envelope type set closed with no scope type; C1 says conflicts are "visible in history/events" without saying which. An implementer must guess whether scope ops appear as pullable envelopes (needing a type) or live in a parallel log.

**Fix (one normative paragraph):** scope events live in the separate append-only scope log (§3.1) with the room's seq for ordering (or their own — pick one), are **not** visible via `GET /messages` (type set stays closed), and get a provenance window: `GET /scopes?history=1` returning past claims. C1 then says "visible in the scope log + the 409 response".

### F-4 (medium, operational): self-reclaim before TTL expiry

C2 covers the case after expiry. But the equally common case — a seat crashes, restarts *before* TTL, and wants its resource back — is locked out: re-claiming 409s against itself and `renew` needs the `scope_id` the crashed process lost.

**Fix (pick one):** (a) same-seat re-claim of the same resource returns 200 and refreshes the existing claim (self-conflict exemption); or (b) add `POST /scopes/renew-by-resource {resource_uri}`. (a) is simpler and safe: only the token holder can do it.

### F-5 (medium, blocks C8): the loop guard has a bypass — `meta.kind="response"` is not mandatory on emission

§8.2's MUST is on receivers and keyed on `meta.kind="response"`. But nothing requires the *sender* to set that kind on `task_result`/`review_verdict` (§5 table says "marker only"; §6 is parenthetical). A client that forgets `meta` emits an unmarked response, and a reflex-polling seat will respond to it — the exact storm C8 exists to prevent. The guard is only as strong as the marker's mandatoriness.

**Fix:** server-side validation at POST: `task_result` and `review_verdict` MUST carry `meta.kind="response"`, otherwise 422. Add to C8: the reference client also verifies an unmarked `task_result` is rejected.

### F-6 (minor, doc structure): §8 numbering is broken

Delivery-semantics items 5 ("Immutability") and 6 ("No mid-turn push") appear *after* subsections 8.1–8.3, orphaned from the list they belong to. Move 8.1–8.3 after item 6 (or fold them in as items); as-is, readers lose the list thread exactly where the normative content starts.

### F-7 (minor): `reserve` claim kind is undefined

It appears in the kind enum (§7) with no definition anywhere. Define it (e.g., announces exclusive intent with a grace window before upgrade) or drop it from v1 — undefined enum values will be guessed differently by every client.

### F-8 (minor): token model 1:1 vs 1:N, and two revocation paths

`POST /v1/tokens` implies multiple tokens per seat are possible; Q11's proposal and `members/{seat}/revoke` ("kills token") read 1:1. Define: N tokens per seat are allowed; member-revoke kills **all** of the seat's tokens; token-revoke kills one. One sentence each.

### F-9 (minor, test design): C5's "byte-identical" conflates two assertions

Byte-identical *envelope re-serialization* is near-vacuous (re-reading the log satisfies it). The meaningful claim is projector-state equality after rebuild from the log alone. Split C5: (a) envelope stream byte-identical on re-serialization, (b) projector state hash equal before/after rebuild. Same lesson as the weight-atlas review's tolerance finding: name exactly what an assertion covers, or the cheap half will be the one that gets implemented.

### F-10 (cosmetic): example hygiene and one reserved keyword

- The §5 and §10 status examples point `meta.ref` at `file:///media/data/coding/weight-atlas` while the bodies describe BDH ladder phases — copy-paste noise that teaches the wrong ref convention in the two examples most likely to be copied.
- §10 uses `for_seat=me`: either define `me` as a reserved value resolving to the token's seat, or write the literal seat name.
- D1 renames the service to HAK but never expands the acronym — add one line (or an intentional "(no expansion)"), READMEs will ask.

## Suggested resolution order for v0.3

F-1, F-2, F-5 are one-sentence-to-one-paragraph fixes and gate implementation (spoofing, dedupe scope, guard bypass). F-3 needs one normative paragraph. F-4/F-6–F-10 are mechanical. With those folded, from my seat this spec is implementation-ready, and the C1–C8 conformance script is the right gate to hold it to.

— Quinn (review seat), 2026-09-01
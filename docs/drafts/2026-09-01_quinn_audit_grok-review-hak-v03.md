---
title: Quinn Audit — Grok Review of HAK Spec v0.3 + Charter Schema Draft
author: quinn (backend glm-5.3-flash via B.AI)
date: 2026-09-01
input: operator-relayed Grok review (assumed 4.6) on hak-spec-v0.3.md (pi-203) + Grok's charter schema draft
verdict: Grok's structural assessment correct; all 10 residual points valid but mostly one-liner gaps, several pre-answered in the text; 3 net-new findings; charter schema adoptable with 2 fixes + 1 boundary note
---

# Audit: Grok Review of HAK v0.3

## 1. Fold verification (my 14-item list vs. the v0.3 text)

**All 14 landed.** Located in text: D10 five server-owned fields (§5 POST contract, C10) · D11 uniqueness key (§5, C6) · D12 content-hash 409 (§5/§7, C6) · D13 mandatory response marker (§5/§7/§8.2, C8b) · D14 scope log + gapless scope_seq + history window (§3.1/§7, C1) · D15 BEGIN IMMEDIATE + event-derived liveness incl. expired-not-lapsed clause (§7, C1) · D16 self-reclaim (§7, C2) · D17 visible-but-marked rendering (§9, C9) · D18 uploads age-GC (§7/§14) · D19 N tokens (§4/§7) · D20 reserve dropped (§7 kind enum) · M items: §8 renumbered (items 1–6 then 8.1–8.3) ✓, C5 split C5a/C5b ✓, example hygiene ✓ (BDH bodies, BDH refs), `me` reserved value defined ✓ (§7), acronym note ✓ (header: HAK is a name, not an acronym), hak reserved-actor line ✓ (§2/§5). Provenance column traces every item to review/audit; my endorsements and the §14 no-authority bullet survive.

## 2. Grok's ten residual points, audited against the text

- **G1 clocks/latency — valid, one line; one option rejected.** Add: writer-block timeout/latency expectation under WAL + BEGIN IMMEDIATE, and `ts` = server **wall clock** (NTP-disciplined), informational. Grok's monotonic-clock alternative is muddled: a monotonic source cannot emit ISO-8601 wall timestamps, and seq is already the sole order of record. Adopt the documentation ask, drop the monotonic option.
- **G2 share-capacity counting — valid, and sharper than stated.** The real gap is the **conflict matrix**: the spec defines only exclusive vs. any → 409. read-exclusive vs. write and read-exclusive vs. read-exclusive are undefined → net-new N-1 below.
- **G3 attachment lifecycle — valid; half already answered.** §14 says size cap enforced **before accept** = upload-side only (Grok missed this; download-side enforcement is moot since stored files cannot exceed the cap). Still open: definition of unreferenced (no envelope/refs pointer to the file_id), GC trigger (D18 says deletable by admin — pick automatic sweep with system-envelope logging per deletion, or strictly admin-triggered; propose the sweep), in-flight download behavior (note: rare at this scale).
- **G4 token/membership — 3 of 4 sub-items pre-answered, one real gap.** 401-immediate: §14 revocation is immediate (add the normative sentence). Pending seat cannot post: follows from membership-based visibility (add one line). hak holds no token: §5 exists only as the from.seat of system envelopes (make explicit). **The last-admin-token recovery path is the only genuinely uncovered failure mode in the entire review** → net-new N-2.
- **G5 filter completeness — valid, minor.** EOF-vs-empty: one line (since ≥ max seq → empty 200, not error). `me` spoofing: **already structurally safe** — `me` resolves to the token's seat and the token is the only identity carrier (D10); add the sentence. Thread filter subtree-vs-direct: genuinely unspecified; propose **direct replies only in v1**, subtree = client-side loop over thread pages (keeps the endpoint dumb).
- **G6 error envelope — valid.** Propose uniform shape: `{error: {code, message, detail}}` — machine code, human message, typed detail. The 409-scope conflicting-body pattern (holder, kind, expires_at) is the model; keep it.
- **G7 presence — valid, one line.** Update rule: any authenticated request refreshes last_poll (cheapest; presence is cosmetic per §8.2); document staleness semantics.
- **G8 charter schema — audited separately, §3 below.**
- **G9 SQLite ops — valid as one-liners.** WAL checkpointing: PASSIVE default fine at this scale. Vacuum: not needed (append-only). Uploads↔db desync: bound it by making the GC sweep the only deleter (scan dir, check db references, log envelope per deletion).
- **G10 client hygiene — no action.** Five-call surface matches §10 and memento §5 exactly.

## 3. Charter schema draft — adoptable with two fixes and one boundary note

- **Fix A (correctness, the real catch):** the draft lets `dispatch.human.emits` include `admin-op`. But `meta.kind="admin-op"` is **reserved for system envelopes from seat hak** (D6/§5). A human POSTing admin-op-kinded messages must be invalid — humans trigger admin ops via admin endpoints, the **bus** emits the envelopes. Correct value: `[chat]` only. Otherwise implementers would allow exactly the envelope class D6 reserves.
- **Fix B (decide the fork):** the description says longer TTL requests are rejected **or** clamped — unresolved. Proposal: clamp to the charter max and return the actual expires_at (leases are cut at charter max anyway; C2 covers expiry; no surprise), reject only values above the hard schema max.
- **Boundary note:** the draft offers direct SQLite as a v1 charter-update path. That bypasses the D6 envelope guarantee — charter mutation must go **through the service** (admin endpoint, or a CLI subcommand that talks to the server), never direct DB writes.
- Minor: `admins` items reference IDENTITIES.md, which is outside the service boundary — name validation stays convention-level in v1, fine as documented. `share_capacities` startswith prefix matching: adopt, document the exact rule. Membership not in charter, mutation-as-admin-op-envelope, GET returning derived state: all consistent.

## 4. Net-new findings (not in Grok's list)

- **N-1 Conflict matrix gap (sharpened from G2).** Write the full matrix into §7 before implementation: exclusive vs. anything → 409 · write vs. write → 409 · write vs. read-exclusive → 409 (both directions) · read-exclusive vs. read-exclusive → 409 (else read-exclusive is meaningless) · write vs. share[n] → 409 · share vs. share → per charter capacity; exhausted capacity → 409 carrying the current count.
- **N-2 Admin recovery path (from G4d).** Proposal: a host-local CLI bootstrap (`hak --bootstrap --seat operator`) regenerating the admin token, requiring shell access to the host (GX10), appending a system envelope `admin-op: token_bootstrap`. On a LAN-only single-process service the host shell **is** the trust root — closes the lockout case without new endpoints. One paragraph in §14.
- **N-3 Operator-go completeness.** Grok names Q1/Q2/Q15/Q18 as the go gates — correct, but **Q16** (blocked → notify vs. badge) is equally an operator preference and belongs in the go message. All of §16 closes with the same go.

## 5. Recommendation

Grok's conclusion stands after audit: **v0.3 is implementation-ready at design level** (the conformance script ships with the implementation; C1–C10 gate it). Fold G1–G9 + N-1–N-3 as a one-page v0.4-lite — or record the answers in the implementation notes; **every item is a line, none is a redesign**. Adopt the charter schema with Fix A/B and the boundary note. The operator go on §16 closes the design phase.

**Status: audit complete. No conflicts with my v0.2/v0.3 review lineage; three net-new findings; charter schema adoptable with Fix A/B.**

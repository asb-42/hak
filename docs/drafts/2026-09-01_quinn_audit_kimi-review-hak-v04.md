---
title: Quinn Audit — Kimi Review of HAK Spec v0.4 (Turn 1 + Turn 2)
author: quinn (backend glm-5.3-flash via B.AI)
date: 2026-09-01
input: operator-relayed Kimi 2.x review, two turns; hak-spec-v0.4.md (pi-203, 841 lines, consolidated)
verdict: v0.4 folds the complete Grok-audit lineage (D21-D28+M2, spot-verified); Kimi's four still-open claims — three confirmed, one wrong damage model, two corrections; best new finding = admin-op transaction atomicity; everything foldable as one-page v0.5-lite or into the operator go
---

# Audit: Kimi Review of HAK v0.4

## 1. Fold verification (Grok-audit lineage vs. v0.4)

**D21-D28 + M2 carry the complete Grok audit as authored:** D21 = wall clock + monotonic rejected with the audit's reasoning · D22 = N-1 full matrix · D23 = unreferenced definition + automatic sweep + only-deleter + SQLite one-liners · D24 = 401-immediate, pending-403, hak-no-token, host-local bootstrap (N-2) · D25 = EOF-empty, me spoof-proof, thread-direct-replies · D26 = error envelope · D27 = presence rule + 15-min staleness · D28 = charter schema with Fix A/B + boundary note · M2 = Q16 in go gates (N-3) + hygiene. Spot-checks passed (§7 ttl clamp line, §14 recovery paragraph, §2 charter row, example marked Fix A applied). No discarded findings. This is the first round with a 100% fold of the review lineage.

## 2. Kimi's four still-open claims, audited

- **K1 canonical JSON undefined (D12) — CONFIRMED as a spec hole; damage model CORRECTED.** Grep: no RFC 8259/JCS reference, no canonicalization rule anywhere. The rule must be written before implementation (same blocking class as my F-2 uniqueness-scope in v0.2). But Kimi's scenario is wrong: a retry with a differently-encoded body under D12 produces a **false 409, never a duplicate envelope** — the 409-on-mismatch clause (C6) makes dedupe failures loud by construction. The real cost: a client with a non-deterministic encoder cannot retry its own emission. **Recommendation:** adopt Kimi's option 2 as normative v1 — **hash the raw request body bytes as received**, dedupe = did I see these exact bytes; extend C6 with a byte-identical-retry success case and a re-serialized-body 409 case (documented expected behavior, not a bug). JCS (RFC 8781) recorded as the v2 upgrade path. Raw-byte is deterministic, zero-config, and honest about what the key guarantees.
- **K2 scope history lacks since — CONFIRMED.** GET /scopes exposes only active=1 / history=1; scope_seq exists but is not cursor-pullable. One line: ?since=<scope_seq> with scope_seq > since, mirroring messages. Valid.
- **K3 matrix gaps — CONFIRMED in substance; fix is a symmetry sentence, not two cells.** D22 lists pairs directionally (share[n] vs. share[m] proves the directional reading), so share-vs-write and read-exclusive-vs-share are formally unset. Conflict is a symmetric relation; the right fix is one normative sentence (the matrix is direction-independent) plus the compact table — both missing cells are 409, per Kimi's expectation and consistent with read-exclusive being exclusive among readers.
- **K4 share[n] syntax — CONFIRMED.** POST body schema has no capacity field. Adopt: kind=share + integer capacity field, validated against charter share_capacities.

## 3. The medium/low list, audited

- **until semantics — valid, adopt:** inclusive (seq <= until), default no upper bound.
- **meta.kind on all types — valid ambiguity, adopt the clarification:** meta optional; meta.kind mandatory exactly where D13 mandates it (task_result, review_verdict) and where the kind table maps the type at use (status, handover); chat needs no kind.
- **body cap / rate limit — half valid, weight corrected:** body cap 413 enforcement is a real one-liner (Q8 exists but no endpoint line). Rate limiting: LAN-only, ~5 seats, single writer — the DOS framing is overweight for this deployment; adopt a minimal per-token POST limiter with 429 as optional/configurable, low priority.
- **admin-op atomicity — VALID, and the best new finding of the round** (Kimi's, not mine): D6 requires system envelopes but never binds mutation + envelope into one transaction. This is exactly the D15 guarantee applied to admin ops; without it the audit trail can diverge from state on partial failure. Adopt normatively. **Quinn sharpening:** the same transactional binding must cover the D23 attachment_delete sweep (file deletion + envelope append atomic, else a lost envelope deletes a file without audit trace).
- **token hashing — valid gap, overbuilt fix:** the algorithm is genuinely unspecified (one doc line needed). But bcrypt/pepper is overkill here: tokens carry 256+ bits of entropy, so unsalted SHA-256 has no realistic preimage/rainbow exposure — the hash cannot be reversed regardless. Adopt: SHA-256, no salt required at this token entropy; revisit only if tokens ever become human-memorable.
- **room name vs path wording — valid cosmetic:** name is the room's URL identifier, validated against path-segment rules, primary lookup key, immutable.

## 4. Consolidation assessment (Kimi Turn 1 meta-point)

Kimi was right that v0.4-as-patched drafts were an assembly risk; the consolidated document resolves it (single clean text, §17 inline, no errata layers). Keep the rule: fold layers never accumulate into the operative document — v0.5-lite or straight into the operator go message; decision log + CHANGELOG carry the history.

## 5. Verdict and path to go

Kimi's bottom line stands after audit: architecture sound, remaining issues are precision gaps, three of them (dedupe rule, scope since, matrix symmetry) worth folding before the first line of code. With Kimi's list + the corrections in §2–3, everything is a one-liner to one paragraph — no redesign. Fold as v0.5-lite (D29–D33) or as recorded answers in the implementation notes; then the operator go on §16 (Q1/Q2/Q15/Q16/Q18) closes the design phase and implementation + pi-side bridge start together under C1–C10 (C6 extended per K1).

**Status: audit complete. Kimi confirms three gaps, one corrected damage model, one overbuilt fix, one genuinely new binding requirement (admin-op atomicity, credited to Kimi). Fold-ready for the go.**

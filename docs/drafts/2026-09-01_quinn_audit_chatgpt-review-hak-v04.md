---
title: Quinn Audit — ChatGPT Review of HAK Spec v0.4
author: quinn (backend glm-5.3-flash via B.AI)
date: 2026-09-01
input: operator-relayed ChatGPT review on hak-spec-v0.4.md (same consolidated text as the Kimi audit)
verdict: strongest round yet — two genuine net-new holes (file authorization, upload/reference/GC race) that all prior reviewers missed; first cross-reviewer conflict of the series resolved AGAINST my own earlier recommendation (raw-byte hashing retracted, JCS adopted); C11-C18 adoption endorsed; readiness verdict shifts to GPT's: hardening pass before go
---

# Audit: ChatGPT Review of HAK v0.4

## 0. Relationship to the Kimi audit (same document, two reviewers)

ChatGPT reviewed the same consolidated v0.4 I audited for Kimi's review. No fold verification needed (v0.4 is unchanged); this audit adjudicates ChatGPT's 13 points + C11-C18 proposal against the text and against the accumulated review lineage (my v0.2 review, Claude audit, Grok audit, Kimi audit).

## 1. The two genuine net-new holes (verified against the text)

- **File authorization is undefined — CONFIRMED, P0.** §7 Files defines POST/GET with no authorization rule whatsoever: who can download a guessed file_id, cross-room references, pending-member uploads, upload-before-reference ownership — all unset. This is the same class as my v0.2 F-1 (spoofable from) and nobody caught it across four rounds. **Recommendation: adopt the SIMPLER of GPT's two options** — uploads are created for a specific room (room in the upload form data), only that room's members can download, cross-room references rejected at envelope POST, GC unreferenced defined per-room. Keeps the endpoint dumb, kills the whole question class.
- **Upload→reference→GC race — CONFIRMED, P0/P1.** D23's only-deleter rule bounds desync, not this race: sweep checks unreferenced → concurrent POST references the file → sweep deletes → envelope points at missing file. **Adopt GPT's grace period: files younger than 24h are never GC'd regardless of reference status.** Cheapest correct fix at this scale; the file-state machine and sweep-locking alternatives are overkill for v1.
- Related and also valid: **fresh-upload disk fill** — D18 bounds old files, not fresh floods. Adopt GPT's quota note as an optional per-room charter cap; low priority at ~5 seats, but the policy hook should exist in the charter schema (attachment_policy already exists — add optional max_unreferenced_bytes or rely on 24h grace + small cap).

## 2. The conflict: canonicalization (GPT #6 vs. Kimi audit + my recommendation)

**First genuine cross-reviewer conflict of the series, and GPT wins.** In the Kimi audit I recommended raw-byte hashing (Kimi's option 2) over canonical-JSON. GPT's server-side JCS argument is stronger: the hash is computed **server-side on the parsed body** — so client encoder differences are already irrelevant, my zero-config advantage was illusory. Against that, raw-byte has a real cost: a client whose re-serialization differs between attempts (non-deterministic encoders) gets false 409s on legitimate retries, while server-side canonicalization (parse → normalize → JCS → hash) makes retries robust regardless of client serialization and still detects every semantic change. **I retract the raw-byte recommendation.** Adopt: hash input = normalized POST object after schema validation, serialized per **RFC 8785 (JCS)**; plus GPT's omitted-vs-null rule (schema validation normalizes: absent == null for optional fields, one line).

**Erratum on my own Kimi audit:** that document cites "JCS (RFC 8781)" — wrong number, JCS is **RFC 8785**. F5-class citation slip in my own audit, caught by cross-checking; correct in any v0.5 reference. The recommendation's substance survives; the citation does not.

## 3. C5a (my own formulation) — GPT is right

C5a "byte-identical re-serialization" was my C5-split wording and is ambiguous as written. Adopt GPT's fix, which is also elegant: **C5a = canonical-JSON re-serialization of every envelope is byte-identical, using the SAME canonicalization rule as the idempotency hash.** One rule closes two independent ambiguities (D12 and C5a). This is the second time a reviewer improved on my conformance wording (Claude improved C5 into the split; GPT now fixes the split's own byte-identity premise).

## 4. Remaining points, adjudicated

- **Admin authority two-sources (#2) — CONFIRMED, adopt GPT's fix A:** line 138 + §17 line 619 make operator implicitly admin while charter.admins is authoritative. Adopt: room creation MUST insert operator into charter.admins; admins is the sole authoritative set; the bootstrap invariant is then exactly one line (every room always has operator in admins). The admin-lifecycle edge cases (revoked admin seat etc.) fold into the membership state machine (#8).
- **Charter mutation wording (#3) — CONFIRMED:** §17 says admins "may update it" while the v1 path is creation-only. Adopt: "In v1 the charter is immutable after creation; a future mutation endpoint MUST emit admin-op: charter_update."
- **Renew semantics (#4) — CONFIRMED:** "extends TTL" is all the text says. Adopt: renew succeeds only if the claim is live at transaction start (BEGIN IMMEDIATE, same as D15); else 404/409, no event. Renew-races, wrong-seat, admin-renew: covered by that rule + D16 exemption.
- **Resource matching — CONFIRMED with sharpening:** D22 literally counts share claims "on the resource prefix" — ambiguous. Adopt GPT's model: URI must parse; scheme lowercased; **capacity class determined by scheme**; **conflict identity = exact normalized URI**; share counting per (room, resource) within the scheme's class capacity. Also fixes the gpu://foo vs gpu://foobar worry.
- **share[n] semantics — already agreed (Kimi K4):** kind=share + integer capacity field = units consumed, validated against charter share_capacities.
- **Pagination (#5) — adopt GPT's canonical rule verbatim:** since exclusive, until inclusive, bounds before order/limit, order affects presentation only, no opaque cursor needed (seq immutable). This supersedes the Kimi-until line by generalizing it.
- **Exactly-once wording (#7) — adopt:** "exactly-once logical persistence per idempotency key"; processing stays at-least-once by cursors.
- **Membership state machine (#8) — adopt, incl. the table:** pending/valid → join only; member/valid → normal; revoked/valid → 403; any/revoked-token → 401. Rejoin after revoke creates a NEW pending membership (authz is rebuilt, history visibility follows the new approval); member re-join is idempotent. Token validity and membership stay orthogonal, as designed.
- **Retraction authorization (#12) — CONFIRMED gap, adopt:** own messages only; admin any; at most one effective retraction per target (duplicates → 200 idempotent or 409, pick one — propose: additional retractions of the same target are rejected 409 with pointer to the effective one); retraction cannot target a retraction (un-say the retraction by posting anew). C17 covers it.
- **meta.kind discipline (#13) — CONFIRMED with a catch nobody had:** §5/§6 promise room-defined kinds, but the §17 charter schema has NO field to define them — the promise is unimplementable as written. Adopt the cleaner option: **drop room-defined kinds for v1** (closed set stays closed; same precedent as D20 reserve), reserved kinds = status/handover/response/admin-op, server rejects client admin-op from every seat (extends Fix A from humans to all client emissions). meta optional; kind mandatory exactly where mapped (D13 + status/handover).
- **Backup (#10) — CONFIRMED, adopt one-liners:** backup via sqlite3 backup API or VACUUM INTO (file copy of a live WAL db is insufficient); uploads/ and hak.db are one backup unit (a restore with one but not the other violates references).
- **LOC constraint (#13/challenge) — concede:** drop ~500-1.000 LOC from §13 as an acceptance constraint; reframe as small conceptual surface. GPT's 1.500-explicit-lines-beats-900-clever point matches everything this team has learned from review rounds.
- **C11-C18 — adopt as proposed.** Executable tests over prose; C12/C13/C14/C16/C17/C18 map 1:1 to the holes above. The conformance suite was already the gate; now it also covers the mundane boundary conditions where production bugs live.
- **Smaller catches — all adopt as one-liners:** health endpoint auth+payload; token issuance bound to IDENTITIES.md-registered seats (enforced, not convention); status-code table; last_poll = activity semantics (explicit); done-clears-line documented as UI projection rule; room-name path-normalization rejection; repo_url schemes restricted to https/git/ssh; ref/uri rendered-links scheme allowlist (javascript: catch is real for the web UI); name display-only never a filesystem path; audit scope documented (successful privileged ops only).

## 5. Verdict

ChatGPT's verdict displaces Kimi's: **v0.4 is NOT yet implementation-ready — it is one hardening pass away (v0.4.1), then ready.** The two file-lifecycle holes are genuine P0s that no prior round (including mine) caught; the canonicalization conflict resolves against my earlier recommendation; C11-C18 make the readiness claim defensible. Everything remains spec-closure, zero architecture changes — GPT's own bottom line. Fold path: v0.4.1 hardening pass carrying GPT #1-#13 adjudicated as above + C11-C18 + the erratum, then the operator go (§16: Q1/Q2/Q15/Q16/Q18) with implementation + pi-side bridge under the extended suite.

**Status: audit complete. Series meta: five review rounds, first cross-reviewer conflict (resolved on merits, against my own prior recommendation — the process works), one self-erratum logged, two net-new P0 holes credited to ChatGPT.**

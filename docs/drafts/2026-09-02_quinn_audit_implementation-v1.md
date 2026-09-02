# Quinn Audit — HAK Implementation v1 (d3e1508) + v0.5.1 Fold (e64863c)

Date: 2026-09-02 · Review seat: Quinn (glm-5.3-flash) · Method: independent clone, throwaway venv run, spot checks with file:line evidence · Deploy-key verified

## 0. Verdict

**The implementation matches the design. No blocking findings.** The v0.5.1 patch folds all five items faithfully (D44–D48), the conformance suite runs green **33/33 in my independent environment** (fresh venv, Python 3.13), and every high-risk mechanism I spot-checked implements the spec as adjudicated. Proceed to live seat onboarding.

## 1. v0.5.1 fold verification (commit e64863c)

- **D44** DB-authoritative deletion protocol — verbatim as adjudicated, including the three protections: `deletion_pending` never served (GET → 404, hak.py:855), never referenceable (envelope POST → 422, hak.py:520), never re-swept as fresh.
- **D45** admin-membership invariant + operator non-revocable (hak.py:407: member-revoke of `operator` → 422 `operator_non_revocable`).
- **D46** charter defaults materialized at normalization.
- **D47** first-POST-201 / identical-retry-200 normative; C6 reworded.
- **D48** OPERATOR GO — Q1 GX10, Q2 transparent DMs, Q15 30-min, **Q16 badge** (per N-3), Q18 sweep+grace.

## 2. Conformance suite — independent run

- Suite: 33 tests covering **all of C1–C18** plus the audit-derived extras (operator non-revocable, conflict-matrix symmetry D34, TTL clamp, body/file caps 413, D35 scope pagination, DM transparency, health/error envelope, admin-op client rejection).
- **My run: 33/33 passed.** First run failed 3 upload tests — my venv lacked `python-multipart`, which README (line 280) and CI both document; zero blame on the code.
- The suite tests the real HTTP stack via TestClient, not mocks — the right choice.
- Cosmetic: commit d3e1508 says "32 tests"; current count is 33 (a test was added in ba61e30 with the D34 symmetry fix). Harmless.

## 3. Spot checks (file:line)

- **D44 sweep** (hak.py:912–937): mark + envelope commit, unlink strictly after commit, retry on later passes, `deleted_at` convergence; in-flight downloads serve-through only while content exists.
- **Token hashing**: SHA-256 unsalted (hak.py:101, 125) — per the Kimi-audit adjudication (256-bit entropy, no KDF needed).
- **D36 rejoin**: revoked → status reset to pending (new-pending semantics), member rejoin idempotent (hak.py:362–378).
- **D32 invariant**: operator auto-inserted into `admins` + auto-member at room creation (hak.py:335–346).
- **D31 canonicalization** (canonical.py): JCS subset with UTF-16 code-unit key sorting (RFC 8785 §3.2.3), ECMAScript-style numbers, Appendix-B-style vectors in self-test; hash input = post-validation model dump with explicit nulls (omitted == null, D31).
- **D33 pagination**: bounds applied before ordering (hak.py:579).
- **Bridge**: five-call seam wrapper per §8/§9, env-configured, no secret material in repo.

## 4. Publication hygiene

No IPs, tokens, secrets, or internal endpoints in any tracked file; drafts keep full provenance including the reviewer-side audit chain. AGPL-3.0 chosen for the bus — noted.

## 5. Coverage statement (checks not run)

No line-by-line audit of the full 1058-line service surface; the audit basis is the complete independent conformance run, the full test inventory, and spot checks on the mechanisms the review series flagged as load-bearing (D44/D45/D36/D32/D31/D33). JCS float formatting is a documented subset (ECMAScript-style); exotic float values were not tested against the full RFC 8785 vectors — HAK bodies rarely carry floats, but if they ever do, that is the first place to look.

## 6. Series meta

From spec v0.2 (2026-09-01) to a deployed, CI-gated, independently-audited implementation (2026-09-02, ~2 days), with zero conformance failures in an independent run — the review-spiral investment (6 rounds, 5 reviewers, 2 errata, 1 retracted recommendation) paid off as predicted: the mundane boundary conditions were already closed before code. The next real audit trigger is the first production incident or the first cross-seat protocol dispute — whichever comes first.

**Status: implementation audit complete. Green light from this seat.**
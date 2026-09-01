Yes. **The core design is sound**, and I would be comfortable moving it toward implementation after a short hardening pass. The strongest parts are the event/projector split, server-owned provenance, cursor semantics, idempotent POSTs, and the explicit scope-conflict model. The spec is unusually disciplined about keeping coordination separate from orchestration. 

That said, I **would not call v0.4 fully implementation-ready yet**. I see several places where two competent implementers could make different choices, plus a few security/data-lifecycle holes that the C1–C10 suite won't catch.

## My assessment

**Architecture:** ✅ sound
**Core consistency model:** ✅ sound
**API shape:** ✅ good
**Failure/concurrency model:** ✅ mostly strong
**Security model:** ⚠️ adequate for a trusted LAN, but underspecified
**Implementation determinism:** ⚠️ several ambiguities remain
**Acceptance tests:** ⚠️ good foundation, but missing important cases

The most important issues are below, roughly in priority order.

---

## 1. File authorization is the biggest missing piece

You define:

```text
POST /v1/files
GET  /v1/files/{id}
```

but files aren't inherently associated with a room in the API. An attachment becomes associated with a room only when an envelope references its `file_id`. The spec then defines "unreferenced" by searching room history. 

That leaves several questions:

* Who can download `f_abc`?
* Can any authenticated seat who guesses/obtains the ID download it?
* Can a member of room A attach a file to room B?
* Can a pending member upload files?
* Can a file be referenced by multiple rooms?
* Can an attachment be referenced **after** upload but before the message POST?
* What happens if the message POST fails after the upload succeeds?

### Recommendation

Make the ownership/authorization rule explicit.

I'd choose:

> A file is initially owned by the uploading seat. It becomes room-visible when referenced by an envelope. `GET /files/{id}` requires membership in at least one room that references the file, unless the caller is an admin. A file may be referenced by multiple rooms.

Or, if cross-room references aren't wanted:

> Every upload is created for a specific room and can only be referenced from that room.

The latter is substantially simpler.

Also add a **quota/orphan policy**. Your 30-day GC solves stale files, but it doesn't prevent someone from filling the disk with fresh unreferenced uploads. The current retention policy only bounds files once they're old enough. 

**Priority: P0.**

---

## 2. The charter/admin model has a subtle contradiction

The charter says `admins` are seats holding the admin role, while §4 says:

> `operator` is always implicitly an admin in every room.

The schema also says `admins` has at least one entry and the implementation injects the creating admin if missing. 

You therefore have **two sources of authority**:

1. `charter.admins`
2. implicit `operator`

That is survivable, but I'd eliminate the ambiguity now.

For example:

> `operator` is not implicit. Room creation MUST insert `operator` into `admins`, and `admins` is the sole authoritative admin set.

Then the bootstrap invariant becomes very clean:

**Every room always has `operator` in `admins`.**

Or explicitly make `operator` a special immutable super-admin outside the charter. But don't have both models at once.

### Related question

What happens if an admin seat is:

* revoked from membership,
* removed from `charter.admins`,
* has all tokens revoked,
* or is deleted from IDENTITIES.md?

The spec currently doesn't fully define the resulting authority state.

**Priority: P0/P1.**

---

## 3. Charter mutation is described but the endpoint doesn't exist

This is probably the clearest "implementation ambiguity."

§17 says charter mutation must go through the service and must generate:

`admin-op: charter_update`

but then explicitly says the dedicated update endpoint is post-v1; in v1, creation is the charter-update path. 

That's okay **if the charter is immutable for all of v1 after creation**.

But the document also says:

> "Only seats with role admin may update it"

which implies updates exist in v1.

I'd change that sentence to:

> "In v1 the charter is immutable after room creation. A future mutation endpoint MUST emit `admin-op: charter_update`."

That removes a misleading promise.

**Priority: P1.**

---

## 4. Scope semantics need another half-page of precision

The conflict matrix is excellent. 

But implementation still has unanswered questions around the actual lease lifecycle:

### Renew

What happens if:

* renew arrives exactly after TTL?
* renew arrives after the projector considers it expired?
* another seat has already reclaimed the resource?
* renew races with another claim?
* renew is attempted by the wrong seat?
* admin renews another seat's claim?

The text only says `renew` "extends TTL." 

I'd define:

> Renew succeeds only if the claim is still live at transaction start. Otherwise 404/409 and no event is appended.

### Resource matching

You say share capacities use URI-scheme prefixes with `startswith`. 

Be careful: naïve prefix matching makes:

`gpu://foo`

match:

`gpu://foobar`

and probably produces surprising behavior for paths.

Define resource normalization and matching, e.g.:

* URI must parse
* scheme is normalized lowercase
* resource identity is exact URI after normalization
* capacity class is determined by scheme, not arbitrary string prefix

### `share[n]`

The spec says `share[n]`, but doesn't formally define what `n` means or how `n` relates to `share_capacities`.

Is:

```json
{"kind":"share[2]"}
```

a claim consuming 2 units?

Or is `n` merely an identifier/class?

That's worth fixing before implementation.

**Priority: P0/P1.**

---

## 5. Pagination needs explicit semantics

`GET /messages` has:

`since`, `until`, `limit`, `order`.

You specify `since` in ascending mode, but not the complete interaction matrix.

For example:

* Does `since` always mean `seq > since`, even with `order=desc`?
* Does `until` mean inclusive or exclusive?
* If `order=desc`, is `since` still the lower bound?
* What does `limit=0` do?
* Are filters applied before pagination? Obviously yes, but state it.
* Does a page contain a continuation cursor?
* Can new messages arrive between page requests without affecting deterministic traversal?

I'd strongly recommend defining a single canonical rule:

> `since` is exclusive, `until` is inclusive; bounds are applied before ordering and limiting. `order=asc|desc` affects presentation only. No opaque cursor is needed in v1 because `seq` is immutable.

That gives clients a very predictable model.

**Priority: P1.**

---

## 6. Idempotency canonicalization needs to be normative

This is a good design:

> same `(room, seat, client_msg_id)` + same canonical body → original response; different body → 409. 

But **"canonical JSON" isn't enough for an implementation spec**.

You need to say what canonicalization means.

Otherwise these could conceivably hash differently:

```json
{"body":"x","to":null}
```

versus:

```json
{"to":null,"body":"x"}
```

or differ over omitted optional properties versus explicit `null`.

I'd specify something like:

> The hash input is the normalized POST request object after schema validation, serialized according to RFC 8785 JSON Canonicalization Scheme.

Or define your own normalization rules.

Also decide whether an explicitly supplied `meta: null` is legal if `meta` is optional, and whether omitted vs `null` are equivalent.

**Priority: P1.**

---

## 7. "Exactly-once emission" is slightly too strong

The spec correctly implements **idempotent creation**, not distributed exactly-once processing.

The distinction matters.

You currently say:

> emission is exactly-once via `client_msg_id` + content hash. 

I'd call this:

> **Exactly-once logical persistence per idempotency key.**

Because a client can:

1. POST
2. server commits
3. response is lost
4. client retries
5. receives 200

That's exactly what you want—but it isn't conventional distributed "exactly once" delivery.

Your processing model itself is explicitly at-least-once. That's correct. 

---

## 8. Membership transitions need a state machine

You have:

`pending → member → revoke`

but not the complete state machine.

Define behavior for:

* pending → join again
* member → join again
* revoked → join again
* pending → revoke
* revoked → approve
* member → approve
* token valid but membership revoked
* token revoked but membership still member

The last two are especially important because authentication and authorization are deliberately separate concepts.

I'd make a small normative table:

| Membership | Token   | Result        |
| ---------- | ------- | ------------- |
| pending    | valid   | join only     |
| member     | valid   | normal access |
| revoked    | valid   | 403           |
| any        | revoked | 401           |

And define whether rejoining a revoked seat creates a new pending membership or restores the old row.

**Priority: P1.**

---

## 9. Attachment references need transactional semantics

There is a nice lifecycle rule: the sweep is the **only deleter**, which is good and materially reduces DB/filesystem race complexity. 

But upload and message creation are necessarily separate HTTP operations:

```text
POST /files
POST /messages referencing file
```

So you inevitably get orphan windows.

That's fine—but state it.

More importantly, the sweep needs to avoid this race:

```text
sweep checks "unreferenced"
client concurrently posts envelope referencing file
sweep deletes file
message now points to missing file
```

The current "only deleter" rule doesn't itself prevent this.

You need either:

* a grace period plus transactional reference registration,
* a file state (`uploaded` → `referenced`),
* or a sweep transaction that locks/checks the DB immediately before deletion.

Given the tiny system, I'd use an explicit file record and a short **upload grace period**, e.g. don't GC files younger than 24h regardless of reference status.

**Priority: P0/P1.**

---

## 10. Backup/restore is under-specified for an "auditable" service

The architecture says:

> Backup = file copy / litestream (later). 

For SQLite WAL, "copy the SQLite file" is not a sufficient backup specification while the service is running. The WAL may contain committed state not reflected in the main DB file.

You don't need an elaborate backup system, but specify one valid mechanism:

* SQLite Online Backup API,
* consistent filesystem snapshot,
* or Litestream once actually deployed.

And separately:

**How are uploads backed up?**

You have two durable stores:

```text
hak.db
uploads/
```

A restore that gets one without the other can violate the attachment references.

This is especially relevant because the system's selling point is persistent audit history.

**Priority: P1.**

---

## 11. C5a "byte-identical re-serialization" is probably impossible as currently worded

You say:

> byte-identical re-serialization of the full envelope stream. 

But JSON objects don't intrinsically have a canonical byte representation.

If serialization order, whitespace, escaping, or numeric formatting changes, semantically identical JSON can have different bytes.

This is fixable:

> C5a: canonical-JSON serialization of every envelope reconstructed from the log is byte-identical.

And then use the **same canonicalization definition as the idempotency hash**.

That's actually a nice opportunity: one canonical serialization rule solves two independent ambiguities.

**Priority: P1.**

---

## 12. Retraction semantics need authorization rules

You define what a retraction looks like, but not **who can retract what**.

Can:

* any agent retract its own message?
* an agent retract another agent's message?
* an admin retract anything?
* a retraction itself be retracted?
* multiple retractions target the same message?

For an auditable system I'd recommend:

> A seat may retract only messages authored by that seat; an admin may retract any message. A target may have at most one effective retraction. A retraction cannot target another retraction.

If you intentionally want unrestricted transparent correction, say so explicitly.

**Priority: P1.**

---

## 13. `meta.kind` discipline is underspecified

You say:

> every `meta` object carries exactly one `kind`.

But the schema example has a `meta`, while ordinary `chat` appears capable of not having one. Then §6 says unlisted structured information can use `chat` plus a room-defined `meta.kind`. 

Questions:

* Is `meta` optional?
* If present, must `kind` be present?
* Are unknown kinds accepted?
* Which kinds are globally reserved?
* Can a room charter actually define a new kind in v1, given the charter schema has no such field?
* Can `meta.kind="admin-op"` ever be client-supplied?

I'd explicitly distinguish:

**reserved kinds** vs **room-defined extension kinds**.

And the server must reject client attempts to emit `admin-op`.

**Priority: P1.**

---

# A few smaller but worthwhile catches

### `GET /files/{id}` and membership

Definitely define authorization.

### `GET /health`

Is it authenticated? If unauthenticated, does it reveal anything beyond liveness? Define `200` payload.

### Token issuance

Define whether tokens can be issued for arbitrary seat strings or only identities known to IDENTITIES.md. The charter has a convention here, but the token endpoint doesn't appear to enforce it.

### Token storage

"Stored hashed" is good. Specify password-style hashing vs ordinary SHA-256. Since these are high-entropy random bearer tokens, SHA-256/HMAC is arguably sufficient and much cheaper than password KDFs—but make the decision explicit.

### Token presentation

"shown once" is good. Define whether the API ever returns the token ID separately from the secret, since token revoke operates on `{token_id}`.

### HTTP status semantics

You have 201/200/409/422/401/403 in places, but a concise status-code table would remove implementation drift.

### `last_poll`

"Any authenticated request" means a client repeatedly downloading a file can appear alive without polling messages. That's probably okay, but make the intended semantic explicit: **activity**, not "message polling."

### `status=done`

You say a `done` status clears the presence line. That means the latest status isn't actually displayed if it is `done`, which is a UI projection rule rather than generic status semantics. Fine, but specify it as such.

### Room naming

The charter regex is good, but the API should explicitly reject path-normalization tricks and name/path mismatches.

### `repo_url`

A URI format validator accepts much more than HTTP(S). If this is intended to be Git linkage, specify allowed schemes.

### `ref` / `uri`

These are free-form-ish URI fields. If they are later rendered as links in a web UI, define allowed schemes to avoid accidentally turning `javascript:` or similar schemes into clickable content.

### MIME handling

You correctly say MIME isn't trusted for execution. I'd also say `name` is display-only and must never become a filesystem path.

### Audit completeness

System envelopes are excellent, but ask: **does failed admin authorization generate an audit event?** Probably not—and that's sensible—but document whether the audit trail means successful privileged operations only or security events too.

---

# One architectural concern I'd challenge

The spec says the service is only **~500–1,000 LOC**. 

I wouldn't use that as an acceptance constraint.

The design itself is small, but once you implement:

* auth
* membership
* token lifecycle
* room policy
* two event logs
* projector rebuild
* idempotency
* attachments
* GC
* concurrency
* Web UI
* conformance suite

I'd optimize for **small conceptual surface**, not a particular LOC count. A 1,500-line implementation with explicit tests is preferable to a 900-line implementation that compresses policy into clever code.

---

# What I would add to C1–C10

I'd make the conformance suite **C1–C18** rather than adding more prose without executable tests:

| Test | What it catches                                   |
| ---- | ------------------------------------------------- |
| C11  | Membership state-machine transitions              |
| C12  | File authorization / cross-room access            |
| C13  | Upload → reference → GC race                      |
| C14  | Canonical JSON/idempotency equivalence            |
| C15  | Pagination bounds/order/filter semantics          |
| C16  | Renew-after-expiry / renew-vs-reclaim race        |
| C17  | Retraction authorization and duplicate retraction |
| C18  | Admin invariants / charter authority consistency  |

That would make the "implementation-ready" claim much more defensible.

---

# Bottom line

**I would not redesign HAK.** The architecture is coherent. In particular, the choice to keep messages and scope events as separate append-only logs, with mutable state projected from them, is a strong foundation.  The cursor discipline and server-owned provenance are also unusually well nailed down. 

I would, however, make a **v0.4.1 hardening pass before the implementation go**.

The five things I'd insist on resolving first are:

1. **File authorization + upload/reference/GC race**
2. **Admin/operator authority model**
3. **Scope renew/resource/`share[n]` semantics**
4. **Pagination and canonical JSON semantics**
5. **Membership/retraction state and authorization rules**

After those, I'd add the corresponding conformance tests. The existing C1–C10 suite is a good spine, but it currently proves the elegant parts of the design more thoroughly than the mundane boundary conditions where production bugs are most likely. 

**Verdict: ~8/10 as a design; not quite a 10/10 implementation spec yet.** The gaps are mostly specification closure, not architectural flaws.

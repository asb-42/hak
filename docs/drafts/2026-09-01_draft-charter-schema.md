**Draft Charter Schema for HAK v1**

Below is a concrete, minimal JSON Schema (Draft 2020-12 style) plus a normative description that fits the rest of the v0.3 spec. It formalizes everything the current document already references without inventing new features.

### Normative description

The **charter** is the single authoritative document for a room. It is created with the room (`POST /v1/rooms`) and returned by `GET /v1/rooms/{room}`. Only seats with role `admin` may update it (via a future admin endpoint or direct SQLite in v1; the update itself must emit a system envelope with `meta.kind="admin-op"` and `op="charter_update"`).

Charter fields control:
- identity and purpose of the room,
- who may act as admin,
- the trivial dispatch table (§8.3),
- optional linkage to a Git repo / project,
- claim policy (default TTL + share capacities),
- attachment size cap,
- the standing charter rule that **write claims are mandatory for any `file://` path under a repo/tree**.

Membership itself is *not* stored inside the charter; it is derived from the membership table (join → pending → approved). The charter only records the initial admin list and the dispatch rules.

### JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://hak.local/schemas/charter-v1.json",
  "title": "HAK Room Charter v1",
  "type": "object",
  "additionalProperties": false,
  "required": ["name", "purpose", "admins", "dispatch", "claim_policy", "attachment_policy"],
  "properties": {
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 64,
      "pattern": "^[a-z0-9][a-z0-9._-]{0,62}$",
      "description": "Room identifier (must match the path segment). Immutable after creation."
    },
    "purpose": {
      "type": "string",
      "minLength": 1,
      "maxLength": 1024,
      "description": "Human-readable purpose of the room."
    },
    "admins": {
      "type": "array",
      "minItems": 1,
      "uniqueItems": true,
      "items": {
        "type": "string",
        "minLength": 1,
        "description": "Seat name that holds the admin role (must exist in IDENTITIES.md or be the reserved human seat 'operator')."
      },
      "description": "Seats that may perform admin operations (approve/revoke, token issue/revoke, charter updates, attachment GC). The human seat 'operator' is always implicitly an admin in every room."
    },
    "dispatch": {
      "type": "object",
      "additionalProperties": false,
      "required": ["human", "agent"],
      "properties": {
        "human": {
          "type": "object",
          "additionalProperties": false,
          "required": ["consumes", "emits"],
          "properties": {
            "consumes": {
              "type": "array",
              "items": { "type": "string", "enum": ["*"] },
              "description": "Always ['*'] in v1 — humans read everything."
            },
            "emits": {
              "type": "array",
              "items": {
                "type": "string",
                "enum": ["chat", "admin-op"]
              },
              "description": "What a human seat may emit."
            }
          }
        },
        "agent": {
          "type": "object",
          "additionalProperties": false,
          "required": ["consumes", "emits"],
          "properties": {
            "consumes": {
              "type": "array",
              "items": { "type": "string", "enum": ["*"] },
              "description": "Always ['*'] in v1 — agents pull everything; filtering is client-side."
            },
            "emits": {
              "type": "array",
              "items": {
                "type": "string",
                "enum": [
                  "chat",
                  "status",
                  "task_request",
                  "task_result",
                  "artifact_ref",
                  "review_verdict",
                  "retraction"
                ]
              },
              "description": "Closed set of types an agent seat may emit."
            }
          }
        }
      },
      "description": "Trivial dispatch table from §8.3. Per-seat subscriptions are a v2 candidate."
    },
    "repo_url": {
      "type": ["string", "null"],
      "format": "uri",
      "description": "Optional primary Git repository this room coordinates (e.g. https://github.com/asb-42/bdh). Used only for documentation and for the write-claim rule below."
    },
    "project": {
      "type": ["string", "null"],
      "maxLength": 128,
      "description": "Optional short project name / code (free-form metadata)."
    },
    "claim_policy": {
      "type": "object",
      "additionalProperties": false,
      "required": ["default_ttl_min", "write_mandatory_for_repo_paths", "share_capacities"],
      "properties": {
        "default_ttl_min": {
          "type": "integer",
          "minimum": 1,
          "maximum": 1440,
          "default": 30,
          "description": "Default claim TTL in minutes (Q15 recorded default = 30). Clients may request a shorter TTL; longer requests are rejected or clamped."
        },
        "write_mandatory_for_repo_paths": {
          "type": "boolean",
          "const": true,
          "description": "Charter rule: any write to a path under a repo/tree requires a live write/exclusive claim. Always true in v1."
        },
        "share_capacities": {
          "type": "object",
          "additionalProperties": {
            "type": "integer",
            "minimum": 1
          },
          "description": "Map of resource-class prefix → maximum concurrent share[n] claims. Example: {\"gpu://\": 2, \"host://\": 4}. Exclusive and write kinds ignore this map. Empty object = no share claims allowed."
        }
      }
    },
    "attachment_policy": {
      "type": "object",
      "additionalProperties": false,
      "required": ["max_file_bytes"],
      "properties": {
        "max_file_bytes": {
          "type": "integer",
          "minimum": 1024,
          "maximum": 104857600,
          "default": 26214400,
          "description": "Per-file upload size limit in bytes (Q4 recorded default = 25 MiB). Enforced at POST /v1/files."
        }
      }
    },
    "notes": {
      "type": ["string", "null"],
      "maxLength": 4096,
      "description": "Free-form operator notes (not interpreted by the service)."
    }
  }
}
```

### Example (minimal valid charter)

```json
{
  "name": "bdh-cl-development",
  "purpose": "Coordination for BDH continuous-learning experiments and phase checklists.",
  "admins": ["operator"],
  "dispatch": {
    "human": {
      "consumes": ["*"],
      "emits": ["chat", "admin-op"]
    },
    "agent": {
      "consumes": ["*"],
      "emits": [
        "chat",
        "status",
        "task_request",
        "task_result",
        "artifact_ref",
        "review_verdict",
        "retraction"
      ]
    }
  },
  "repo_url": "https://github.com/asb-42/bdh",
  "project": "bdh-cl",
  "claim_policy": {
    "default_ttl_min": 30,
    "write_mandatory_for_repo_paths": true,
    "share_capacities": {
      "gpu://": 1,
      "host://": 4
    }
  },
  "attachment_policy": {
    "max_file_bytes": 26214400
  },
  "notes": null
}
```

### Implementation notes (to keep the surface small)

- On `POST /v1/rooms` the server validates the charter against this schema, injects the creating admin into `admins` if missing, and stores the whole object (or a normalized version) in the rooms table.
- `GET /v1/rooms/{room}` returns the charter plus the derived membership list and live projector data (last_read, last_poll, active scopes count, etc.).
- Any later charter mutation must be an admin-op system envelope so the change is part of the auditable history.
- The `dispatch` table is deliberately trivial and constant for v1; the schema still records it so future per-seat subscriptions have a clear extension point.
- `share_capacities` keys are treated as URI-scheme prefixes (simple `startswith` match). Exact matching can be tightened later if needed.

This closes the “charter schema missing” gap while staying inside the existing design constraints.

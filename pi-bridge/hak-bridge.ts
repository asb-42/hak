/**
 * HAK bridge — pi-side five-call seam wrapper (spec v0.5.1 §8/§9).
 *
 * Wraps the running HAK service so agent seats can participate in the bus
 * without leaving pi: post/pull envelopes, claim/renew/release scopes.
 * One tool, one subcommand surface; the LLM sees five verbs.
 *
 * Config via environment:
 *   HAK_URL    e.g. http://127.0.0.1:8890   (default)
 *   HAK_TOKEN  bearer secret for this seat  (required)
 *   HAK_SEAT   seat name override (default: pi-203)
 */

import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "@earendil-works/pi-ai";

const HAK_URL = process.env.HAK_URL ?? "http://127.0.0.1:8890";
const SEAT = process.env.HAK_SEAT ?? "pi-203";

function cfgError(): string {
  return (
    `HAK bridge is not configured. Set HAK_TOKEN (bearer secret for seat ${SEAT}) ` +
    `and optionally HAK_URL (current: ${HAK_URL}). ` +
    `Ask the operator to issue a token: POST /v1/tokens {"seat": "${SEAT}"}.`
  );
}

interface HakError {
  error?: { code?: string; message?: string; detail?: unknown };
}

async function call(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: unknown,
): Promise<{ status: number; json: any }> {
  const token = process.env.HAK_TOKEN;
  if (!token) throw new Error(cfgError());
  const res = await fetch(`${HAK_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15_000),
  });
  let json: any = null;
  try {
    json = await res.json();
  } catch {
    /* 204s and text bodies */
  }
  return { status: res.status, json };
}

function render(status: number, json: any): string {
  if (status >= 400) {
    const e = (json as HakError)?.error ?? {};
    const d = e.detail !== undefined ? ` — ${JSON.stringify(e.detail)}` : "";
    return `HAK ${status} ${e.code ?? "error"}: ${e.message ?? "request failed"}${d}`;
  }
  return JSON.stringify(json, null, 0);
}

const envelopeType = Type.Union([
  Type.Literal("chat"),
  Type.Literal("status"),
  Type.Union([Type.Literal("task_request"), Type.Literal("task_result")]),
  Type.Union([Type.Literal("artifact_ref"), Type.Literal("review_verdict")]),
  Type.Literal("retraction"),
]);

const hakTool = defineTool({
  name: "hak",
  label: "HAK bus",
  description:
    "Inter-agent messaging bus (HAK). Five verbs via `do`:\n" +
    "- post: send an envelope to a room (idempotent per client_msg_id; 201 first, 200 identical retry)\n" +
    "- pull: fetch messages since a cursor seq (resumable; empty 200 at EOF)\n" +
    "- claim: acquire a resource scope (write/read-exclusive/exclusive/share); renew by re-claiming\n" +
    "- renew: extend a live claim's TTL\n" +
    "- release: drop a claim (idempotent)\n" +
    "- status: room state — members, presence, active scopes\n" +
    "Scope conflicts return 409 with the holder — never silently queued.",

  parameters: Type.Object({
    do: Type.Union([
      Type.Literal("post"),
      Type.Literal("pull"),
      Type.Literal("claim"),
      Type.Literal("renew"),
      Type.Literal("release"),
      Type.Literal("status"),
    ]),
    room: Type.String({ description: "Room name" }),
    do_what: Type.Optional(
      Type.String({
        description:
          "Freetext intent (used for planning clarity); the structured fields below are authoritative.",
      }),
    ),
    body: Type.Optional(Type.String({ description: "post: envelope body text" })),
    type: Type.Optional(
      Type.Union([
        Type.Literal("chat"),
        Type.Literal("status"),
        Type.Literal("task_request"),
        Type.Literal("task_result"),
        Type.Literal("artifact_ref"),
        Type.Literal("review_verdict"),
        Type.Literal("retraction"),
      ]),
    ),
    client_msg_id: Type.Optional(
      Type.String({ description: "post: idempotency key (recommended)" }),
    ),
    reply_to: Type.Optional(Type.String({ description: "post: target envelope id" })),
    meta_kind: Type.Optional(
      Type.String({
        description:
          "post: meta.kind — status | handover | response | admin-op(server-only)",
      }),
    ),
    meta_state: Type.Optional(
      Type.String({ description: "post: status state — working_on|waiting_on|blocked|done" }),
    ),
    for_seat: Type.Optional(Type.String({ description: "post: handover target seat" })),
    since: Type.Optional(Type.Integer({ description: "pull: cursor seq (exclusive)" })),
    limit: Type.Optional(Type.Integer({ description: "pull: max messages (1-500)" })),
    resource_uri: Type.Optional(
      Type.String({ description: "claim: scheme://resource — e.g. gpu://render or file:///repo" }),
    ),
    kind: Type.Optional(
      Type.Union([
        Type.Literal("write"),
        Type.Literal("read-exclusive"),
        Type.Literal("exclusive"),
        Type.Literal("share"),
      ]),
    ),
    units: Type.Optional(Type.Integer({ description: "claim: share units (default 1)" })),
    scope_id: Type.Optional(Type.String({ description: "renew/release: scope id" })),
  }),

  async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
    const op = params.do;
    try {
      switch (op) {
        case "post": {
          if (!params.body) throw new Error("post requires `body`");
          const meta: Record<string, unknown> = {};
          if (params.meta_kind) meta.kind = params.meta_kind;
          if (params.meta_state) meta.state = params.meta_state;
          if (params.for_seat) meta.for_seat = params.for_seat;
          const payload: Record<string, unknown> = {
            type: params.type ?? "chat",
            body: params.body,
          };
          if (params.client_msg_id) payload.client_msg_id = params.client_msg_id;
          if (params.reply_to) payload.reply_to = params.reply_to;
          if (Object.keys(meta).length) payload.meta = meta;
          const { status, json } = await call("POST", `/v1/rooms/${params.room}/messages`, payload);
          return { content: [{ type: "text", text: render(status, json) }], details: { status } };
        }
        case "pull": {
          const q = new URLSearchParams();
          if (params.since !== undefined) q.set("since", String(params.since));
          if (params.limit !== undefined) q.set("limit", String(params.limit));
          const { status, json } = await call(
            "GET",
            `/v1/rooms/${params.room}/messages?${q}`,
          );
          return { content: [{ type: "text", text: render(status, json) }], details: { status } };
        }
        case "claim": {
          if (!params.resource_uri || !params.kind)
            throw new Error("claim requires resource_uri and kind");
          const { status, json } = await call("POST", `/v1/rooms/${params.room}/scopes`, {
            resource_uri: params.resource_uri,
            kind: params.kind,
            units: params.units ?? 1,
          });
          return { content: [{ type: "text", text: render(status, json) }], details: { status } };
        }
        case "renew": {
          if (!params.scope_id) throw new Error("renew requires scope_id");
          const { status, json } = await call(
            "POST",
            `/v1/rooms/${params.room}/scopes/${params.scope_id}/renew`,
          );
          return { content: [{ type: "text", text: render(status, json) }], details: { status } };
        }
        case "release": {
          if (!params.scope_id) throw new Error("release requires scope_id");
          const { status, json } = await call(
            "DELETE",
            `/v1/rooms/${params.room}/scopes/${params.scope_id}`,
          );
          return {
            content: [
              {
                type: "text",
                text: status === 204 ? "HAK 204 released" : render(status, json),
              },
            ],
            details: { status },
          };
        }
        case "status": {
          const out: string[] = [];
          const members = await call("GET", `/v1/rooms/${params.room}/members`);
          out.push(render(members.status, members.json));
          const scopes = await call("GET", `/v1/rooms/${params.room}/scopes`);
          out.push(render(scopes.status, scopes.json));
          return { content: [{ type: "text", text: out.join("\n") }], details: {} };
        }
      }
      return {
        content: [{ type: "text", text: `HAK bridge: unknown verb '${op}'` }],
        details: {},
      };
    } catch (e) {
      return {
        content: [{ type: "text", text: `HAK bridge error: ${(e as Error).message}` }],
        details: { error: true },
      };
    }
  },
});

export default function (pi: ExtensionAPI) {
  pi.registerTool(hakTool);

  // /hak command: quick connectivity check (health + seat)
  pi.registerCommand("hak", {
    description: "HAK bus: check bridge configuration and service health",
    handler: async (_args, ctx) => {
      const token = process.env.HAK_TOKEN;
      if (!token) {
        ctx.ui.notify(cfgError(), "warning");
        return;
      }
      try {
        const { status, json } = await call("GET", "/v1/health");
        if (status === 200)
          ctx.ui.notify(`HAK OK: ${json.service} v${json.version} at ${HAK_URL}`, "info");
        else ctx.ui.notify(render(status, json), "warning");
      } catch (e) {
        ctx.ui.notify(`HAK unreachable: ${(e as Error).message}`, "warning");
      }
    },
  });

  pi.on("session_start", async (_event, ctx) => {
    if (process.env.HAK_TOKEN) {
      try {
        const { status } = await call("GET", "/v1/health");
        if (status === 200)
          ctx.ui.notify(`HAK bridge up (seat ${SEAT}, ${HAK_URL})`, "info");
      } catch {
        ctx.ui.notify(`HAK bridge: service unreachable at ${HAK_URL}`, "warning");
      }
    }
  });
}

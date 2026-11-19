import type { KeyObject } from "node:crypto";
import {
  ConfigError,
  apnsConfigFromEnv,
  createForwarder,
  deviceToken,
  openPushId,
  pushIdKeyFromEnv,
  sealPushId,
  type Env,
  type Forwarder,
} from "./lib.js";
import { RateLimit } from "./limit.js";

export const MAX_NOTIFICATION_BYTES = 2048;
export const HOURLY_LIMIT = 60;
const HOUR_MS = 60 * 60 * 1000;
// Room for the push ID and a notification whose every character JSON escapes.
const MAX_BODY_BYTES = 16 * 1024;

export type Handler = (request: Request) => Promise<Response>;
export type Log = (line: string) => void;

export interface RegisterDeps {
  key: KeyObject;
}

export interface PushDeps {
  key: KeyObject;
  forwarder: Forwarder;
  limit: RateLimit;
  log: Log;
}

/** `POST /v1/register`: seals Apple's device token into a push ID. */
export async function register(request: Request, deps: RegisterDeps): Promise<Response> {
  const body = await readJson(request);
  if (body instanceof Response) return body;
  const token = typeof body["token"] === "string" ? deviceToken(body["token"]) : null;
  if (!token) return problem(400, "token must be Apple's device token in hex");
  return Response.json({ push_id: sealPushId(token, deps.key) });
}

/** `POST /v1/push`: forwards one sealed notification to the device a push ID names. */
export async function push(request: Request, deps: PushDeps): Promise<Response> {
  const body = await readJson(request);
  if (body instanceof Response) return body;
  const pushId = body["push_id"];
  const notification = body["notification"];
  if (typeof pushId !== "string" || typeof notification !== "string" || notification === "") {
    return problem(400, "push_id and notification must be strings");
  }
  if (Buffer.byteLength(notification) > MAX_NOTIFICATION_BYTES) {
    return problem(413, `notification must be at most ${MAX_NOTIFICATION_BYTES} bytes`);
  }
  const token = openPushId(pushId, deps.key);
  if (!token) return problem(404, "unknown push ID");
  // Counted by device, so registering again for a new push ID buys no more.
  const wait = deps.limit.take(token.toString("hex"));
  if (wait > 0) {
    return problem(429, `at most ${HOURLY_LIMIT} notifications an hour`, { "retry-after": String(Math.ceil(wait / 1000)) });
  }
  const outcome = await deps.forwarder.send(token, notification);
  switch (outcome.status) {
    case "sent":
      return new Response(null, { status: 202 });
    case "unregistered":
      return problem(404, "unknown push ID");
    case "failed":
      deps.log(`push: Apple's push service failed: ${outcome.reason}`);
      return problem(502, "Apple's push service did not take the notification");
  }
}

/** The register handler, with its key from the environment. */
export function registerFromEnv(env: Env = process.env, log: Log = console.error): Handler {
  let deps: RegisterDeps | undefined;
  return async (request) => {
    try {
      deps ??= { key: pushIdKeyFromEnv(env) };
    } catch (error) {
      return unconfigured(error, log);
    }
    return register(request, deps);
  };
}

/** The push handler, with its settings from the environment and its own in-memory limit. */
export function pushFromEnv(env: Env = process.env, log: Log = console.error): Handler {
  let deps: PushDeps | undefined;
  return async (request) => {
    try {
      deps ??= {
        key: pushIdKeyFromEnv(env),
        forwarder: createForwarder(apnsConfigFromEnv(env)),
        limit: new RateLimit(HOURLY_LIMIT, HOUR_MS),
        log,
      };
    } catch (error) {
      return unconfigured(error, log);
    }
    return push(request, deps);
  };
}

function unconfigured(error: unknown, log: Log): Response {
  if (!(error instanceof ConfigError)) throw error;
  log(`push: ${error.message}`);
  return problem(500, "the push service is not configured");
}

// Reads at most MAX_BODY_BYTES, so an oversized body costs no more than that.
async function readJson(request: Request): Promise<Record<string, unknown> | Response> {
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (request.body) {
    const reader = request.body.getReader();
    for (let read = await reader.read(); !read.done; read = await reader.read()) {
      size += read.value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        return problem(413, "body too large");
      }
      chunks.push(read.value);
    }
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return problem(400, "body must be JSON");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return problem(400, "body must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

function problem(status: number, error: string, headers: Record<string, string> = {}): Response {
  return Response.json({ error }, { status, headers });
}

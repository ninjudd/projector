import { sign, type KeyObject } from "node:crypto";
import { connect as connectHttp2, constants, type ClientHttp2Session } from "node:http2";

export type Environment = "production" | "sandbox";

/** Apple's push service for each environment. An Xcode-installed build's token works only in the sandbox. */
export const APNS_ORIGINS: Readonly<Record<Environment, string>> = {
  production: "https://api.push.apple.com",
  sandbox: "https://api.sandbox.push.apple.com",
};

export interface ApnsConfig {
  /** Apple's push key, the .p8 file's P-256 private key. */
  key: KeyObject;
  keyId: string;
  teamId: string;
  /** The app's bundle ID, which Apple calls the topic. */
  bundleId: string;
  environment: Environment;
}

export type Outcome = { status: "sent" } | { status: "unregistered" } | { status: "failed"; reason: string };

export interface Forwarder {
  /** Sends one sealed notification to one device and reports what Apple said. It never rejects. */
  send(token: Buffer, notification: string): Promise<Outcome>;
  close(): void;
}

export interface ForwarderOptions {
  connect?: (origin: string) => ClientHttp2Session;
  now?: () => number;
  timeoutMs?: number;
}

export interface ProviderToken {
  get(): string;
  invalidate(): void;
}

// Apple rejects a provider token older than an hour and refuses new ones made
// more often than every 20 minutes, so one token serves 50 minutes.
const TOKEN_LIFETIME_MS = 50 * 60 * 1000;
const TIMEOUT_MS = 10_000;
// What the lock screen shows if the app's notification service extension
// cannot open the sealed text in time.
const FALLBACK_ALERT = { title: "Projector", body: "An agent needs you." };
const REASON = /^[A-Za-z]+$/;

/** Makes the ES256 JWT that authenticates the service to Apple, and reuses it for its lifetime. */
export function providerToken(config: Pick<ApnsConfig, "key" | "keyId" | "teamId">, now: () => number = Date.now): ProviderToken {
  let token = "";
  let madeAt = -Infinity;
  return {
    get() {
      const at = now();
      if (at - madeAt >= TOKEN_LIFETIME_MS) {
        const header = base64url({ alg: "ES256", kid: config.keyId });
        const claims = base64url({ iss: config.teamId, iat: Math.floor(at / 1000) });
        const signature = sign("sha256", Buffer.from(`${header}.${claims}`), { key: config.key, dsaEncoding: "ieee-p1363" });
        token = `${header}.${claims}.${signature.toString("base64url")}`;
        madeAt = at;
      }
      return token;
    },
    invalidate() {
      madeAt = -Infinity;
    },
  };
}

/**
 * Forwards sealed notifications to Apple's push service over one HTTP/2
 * connection, which it opens on first use and reopens once Apple closes it.
 */
export function createForwarder(config: ApnsConfig, options: ForwarderOptions = {}): Forwarder {
  const origin = APNS_ORIGINS[config.environment];
  const connect = options.connect ?? connectHttp2;
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  const token = providerToken(config, options.now);
  let session: ClientHttp2Session | undefined;

  function open(): { session: ClientHttp2Session; reused: boolean } {
    if (session && !session.closed && !session.destroyed) return { session, reused: true };
    const opened = connect(origin);
    const forget = () => {
      if (session === opened) session = undefined;
    };
    // A session error also fails its open streams, which report it to send.
    opened.on("error", forget);
    opened.on("goaway", forget);
    opened.on("close", forget);
    session = opened;
    return { session: opened, reused: false };
  }

  function attempt(current: ClientHttp2Session, device: Buffer, payload: string): Promise<Outcome | "transport"> {
    return new Promise((resolve) => {
      let settled = false;
      const settle = (outcome: Outcome | "transport") => {
        if (!settled) {
          settled = true;
          resolve(outcome);
        }
      };
      const request = current.request({
        ":method": "POST",
        ":path": `/3/device/${device.toString("hex")}`,
        authorization: `bearer ${token.get()}`,
        "apns-topic": config.bundleId,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "content-type": "application/json",
      });
      let status = 0;
      const chunks: Buffer[] = [];
      request.setTimeout(timeoutMs, () => {
        request.close(constants.NGHTTP2_CANCEL);
        settle({ status: "failed", reason: "timeout" });
      });
      request.on("response", (headers) => {
        status = Number(headers[":status"]);
      });
      request.on("data", (chunk: Buffer) => chunks.push(chunk));
      request.on("end", () => {
        settle(status === 0 ? "transport" : outcome(status, Buffer.concat(chunks)));
      });
      request.on("error", () => {
        settle(status === 0 ? "transport" : { status: "failed", reason: `${status}` });
      });
      request.end(payload);
    });
  }

  function outcome(status: number, body: Buffer): Outcome {
    if (status === 200) return { status: "sent" };
    const reason = reasonOf(body);
    if (status === 410 || (status === 400 && reason === "BadDeviceToken")) return { status: "unregistered" };
    if (status === 403 && reason === "ExpiredProviderToken") token.invalidate();
    return { status: "failed", reason: reason ? `${status} ${reason}` : `${status}` };
  }

  return {
    async send(device, notification) {
      const payload = JSON.stringify({ aps: { alert: FALLBACK_ALERT, "mutable-content": 1 }, sealed: notification });
      // A connection that sat idle can be dead without the client knowing,
      // as when a function instance is suspended between requests. A request
      // that fails on it before Apple answers gets one fresh connection.
      for (let tries = 0; tries < 2; tries++) {
        let current: { session: ClientHttp2Session; reused: boolean };
        try {
          current = open();
        } catch {
          return { status: "failed", reason: "connect" };
        }
        const result = await attempt(current.session, device, payload);
        if (result !== "transport") return result;
        current.session.destroy();
        if (!current.reused) break;
      }
      return { status: "failed", reason: "connection" };
    },
    close() {
      session?.close();
      session = undefined;
    },
  };
}

function base64url(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

// Apple's reasons are single words such as BadDeviceToken. Keeping only
// those means a log line can never carry anything else the body held.
function reasonOf(body: Buffer): string {
  try {
    const parsed: unknown = JSON.parse(body.toString("utf8"));
    const reason = typeof parsed === "object" && parsed !== null ? (parsed as { reason?: unknown }).reason : undefined;
    return typeof reason === "string" && REASON.test(reason) ? reason : "";
  } catch {
    return "";
  }
}

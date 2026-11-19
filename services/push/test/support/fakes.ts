import { generateKeyPairSync, randomBytes, type KeyObject } from "node:crypto";
import { constants, createServer, type IncomingHttpHeaders, type ServerHttp2Session } from "node:http2";
import type { AddressInfo } from "node:net";
import type { ApnsConfig, Forwarder, Outcome } from "../../src/lib.js";

export function appleKey(): { privateKey: KeyObject; publicKey: KeyObject } {
  return generateKeyPairSync("ec", { namedCurve: "P-256" });
}

export function config(overrides: Partial<ApnsConfig> = {}): ApnsConfig {
  return {
    key: appleKey().privateKey,
    keyId: "KEY1234567",
    teamId: "TEAM123456",
    bundleId: "bot.projector.example",
    environment: "sandbox",
    ...overrides,
  };
}

export function token(): Buffer {
  return randomBytes(32);
}

export interface Received {
  headers: IncomingHttpHeaders;
  body: string;
}

// "reset" refuses the stream before answering, as a connection that died does.
export type Answer = { status: number; reason?: string } | "hang" | "reset";

/** A cleartext HTTP/2 server that records each request and answers as `answer` says. */
export async function fakeApns(answer: (received: Received, index: number) => Answer = () => ({ status: 200 })) {
  const received: Received[] = [];
  const sessions = new Set<ServerHttp2Session>();
  const server = createServer();
  server.on("session", (session) => {
    sessions.add(session);
    session.on("close", () => sessions.delete(session));
  });
  server.on("stream", (stream, headers) => {
    const chunks: Buffer[] = [];
    stream.on("data", (chunk: Buffer) => chunks.push(chunk));
    stream.on("end", () => {
      const request = { headers, body: Buffer.concat(chunks).toString("utf8") };
      const reply = answer(request, received.length);
      received.push(request);
      if (reply === "hang") return;
      if (reply === "reset") {
        // Closing with an error code also reports it on this side's stream.
        stream.on("error", () => {});
        stream.close(constants.NGHTTP2_REFUSED_STREAM);
        return;
      }
      stream.respond({ ":status": reply.status });
      stream.end(reply.reason === undefined ? "" : JSON.stringify({ reason: reply.reason }));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`,
    received,
    async close() {
      for (const session of sessions) session.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

/** A forwarder that records what it was asked to send and answers `outcome`. */
export function fakeForwarder(outcome: Outcome = { status: "sent" }) {
  const sent: { token: Buffer; notification: string }[] = [];
  const forwarder: Forwarder = {
    send(device, notification) {
      sent.push({ token: device, notification });
      return Promise.resolve(outcome);
    },
    close() {},
  };
  return { forwarder, sent };
}

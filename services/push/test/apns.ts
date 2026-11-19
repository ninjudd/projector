import assert from "node:assert/strict";
import { generateKeyPairSync, verify } from "node:crypto";
import { connect, type ClientHttp2Session } from "node:http2";
import { test } from "node:test";
import { APNS_ORIGINS, apnsConfigFromEnv, ConfigError, createForwarder, providerToken, type ApnsConfig } from "../src/lib.js";
import { appleKey, config, fakeApns, token, type Answer } from "./support/fakes.js";

async function withApns(answer: (index: number) => Answer, run: (forwarder: ReturnType<typeof createForwarder>, apns: Awaited<ReturnType<typeof fakeApns>>) => Promise<void>, settings: ApnsConfig = config()) {
  const apns = await fakeApns((_, index) => answer(index));
  const forwarder = createForwarder(settings, { connect: () => connect(apns.url), timeoutMs: 200 });
  try {
    await run(forwarder, apns);
  } finally {
    forwarder.close();
    await apns.close();
  }
}

function decode(part: string | undefined): Record<string, unknown> {
  return JSON.parse(Buffer.from(part ?? "", "base64url").toString("utf8")) as Record<string, unknown>;
}

test("a notification reaches the device's path with the bundle ID as topic and the sealed text unchanged", async () => {
  const settings = config();
  await withApns(() => ({ status: 200 }), async (forwarder, apns) => {
    const device = token();
    const notification = "c2VhbGVk+/=\"\\é";
    assert.deepEqual(await forwarder.send(device, notification), { status: "sent" });
    const [received] = apns.received;
    assert.ok(received);
    assert.equal(received.headers[":method"], "POST");
    assert.equal(received.headers[":path"], `/3/device/${device.toString("hex")}`);
    assert.equal(received.headers["apns-topic"], settings.bundleId);
    assert.equal(received.headers["apns-push-type"], "alert");
    const payload = JSON.parse(received.body) as { aps: Record<string, unknown>; sealed: string };
    assert.equal(payload.sealed, notification);
    assert.equal(payload.aps["mutable-content"], 1);
  }, settings);
});

test("the provider token is an ES256 JWT that Apple's push key verifies, with the key ID and team ID", async () => {
  const { privateKey, publicKey } = appleKey();
  const settings = config({ key: privateKey });
  await withApns(() => ({ status: 200 }), async (forwarder, apns) => {
    const before = Math.floor(Date.now() / 1000);
    await forwarder.send(token(), "sealed");
    const authorization = String(apns.received[0]?.headers.authorization);
    assert.match(authorization, /^bearer [\w-]+\.[\w-]+\.[\w-]+$/);
    const [header, claims, signature] = authorization.slice("bearer ".length).split(".");
    assert.deepEqual(decode(header), { alg: "ES256", kid: settings.keyId });
    const { iss, iat } = decode(claims);
    assert.equal(iss, settings.teamId);
    assert.ok(typeof iat === "number" && iat >= before && iat <= Date.now() / 1000);
    const signed = Buffer.from(`${header}.${claims}`);
    const raw = Buffer.from(signature ?? "", "base64url");
    assert.equal(raw.length, 64, "a JWT signature is raw r and s, not DER");
    assert.ok(verify("sha256", signed, { key: publicKey, dsaEncoding: "ieee-p1363" }, raw));
  }, settings);
});

test("one provider token serves 50 minutes, so a token made per request or kept past an hour is caught", () => {
  let now = 1_700_000_000_000;
  const tokens = providerToken(config(), () => now);
  const first = tokens.get();
  now += 49 * 60 * 1000;
  assert.equal(tokens.get(), first);
  now += 60 * 1000;
  const second = tokens.get();
  assert.notEqual(second, first);
  assert.equal(decode(second.split(".")[1]).iat, Math.floor(now / 1000));
});

test("one forwarder reuses its provider token across notifications", async () => {
  await withApns(() => ({ status: 200 }), async (forwarder, apns) => {
    await forwarder.send(token(), "one");
    await forwarder.send(token(), "two");
    const [first, second] = apns.received.map((received) => received.headers.authorization);
    assert.ok(first);
    assert.equal(second, first);
  });
});

test("Apple's ExpiredProviderToken answer makes the next notification carry a new token", async () => {
  await withApns((index) => (index === 0 ? { status: 403, reason: "ExpiredProviderToken" } : { status: 200 }), async (forwarder, apns) => {
    assert.deepEqual(await forwarder.send(token(), "one"), { status: "failed", reason: "403 ExpiredProviderToken" });
    assert.deepEqual(await forwarder.send(token(), "two"), { status: "sent" });
    const [first, second] = apns.received.map((received) => received.headers.authorization);
    assert.notEqual(second, first);
  });
});

test("Apple's 410 and BadDeviceToken answers report the device unregistered", async () => {
  const answers: Answer[] = [{ status: 410, reason: "Unregistered" }, { status: 400, reason: "BadDeviceToken" }];
  await withApns((index) => answers[index] ?? { status: 200 }, async (forwarder) => {
    assert.deepEqual(await forwarder.send(token(), "one"), { status: "unregistered" });
    assert.deepEqual(await forwarder.send(token(), "two"), { status: "unregistered" });
  });
});

test("any other answer reports failure with Apple's reason, and only a one-word reason", async () => {
  const answers: Answer[] = [
    { status: 500, reason: "InternalServerError" },
    { status: 400, reason: "BadTopic" },
    { status: 500, reason: "the device token is abcdef" },
  ];
  await withApns((index) => answers[index] ?? { status: 200 }, async (forwarder) => {
    assert.deepEqual(await forwarder.send(token(), "one"), { status: "failed", reason: "500 InternalServerError" });
    assert.deepEqual(await forwarder.send(token(), "two"), { status: "failed", reason: "400 BadTopic" });
    assert.deepEqual(await forwarder.send(token(), "three"), { status: "failed", reason: "500" });
  });
});

test("a notification Apple never answers fails after the timeout instead of hanging the call", async () => {
  await withApns(() => "hang", async (forwarder) => {
    assert.deepEqual(await forwarder.send(token(), "sealed"), { status: "failed", reason: "timeout" });
  });
});

test("a request that fails on a reused connection before Apple answers gets one fresh connection", async () => {
  const apns = await fakeApns((_, index) => (index === 1 ? "reset" : { status: 200 }));
  const sessions: ClientHttp2Session[] = [];
  const forwarder = createForwarder(config(), {
    connect: () => {
      const session = connect(apns.url);
      sessions.push(session);
      return session;
    },
  });
  try {
    assert.deepEqual(await forwarder.send(token(), "one"), { status: "sent" });
    assert.deepEqual(await forwarder.send(token(), "two"), { status: "sent" });
    assert.equal(sessions.length, 2);
    assert.deepEqual(apns.received.map((received) => JSON.parse(received.body).sealed), ["one", "two", "two"]);
  } finally {
    forwarder.close();
    await apns.close();
  }
});

test("a request that fails on a fresh connection is not retried, so a dead host costs one attempt", async () => {
  await withApns(() => "reset", async (forwarder, apns) => {
    assert.deepEqual(await forwarder.send(token(), "sealed"), { status: "failed", reason: "connection" });
    assert.equal(apns.received.length, 1);
  });
});

test("a connection that refuses new requests reports failure, so send never rejects", async () => {
  const apns = await fakeApns();
  const forwarder = createForwarder(config(), {
    connect: () => {
      const session = connect(apns.url);
      session.close();
      return session;
    },
  });
  try {
    assert.deepEqual(await forwarder.send(token(), "sealed"), { status: "failed", reason: "connection" });
  } finally {
    forwarder.close();
    await apns.close();
  }
});

test("APNS_ENVIRONMENT picks Apple's sandbox or production host, and nothing else", async () => {
  const apns = await fakeApns();
  try {
    const pem = appleKey().privateKey.export({ type: "pkcs8", format: "pem" }).toString();
    const env = { APNS_KEY: pem, APNS_KEY_ID: "KEY1234567", APNS_TEAM_ID: "TEAM123456", APNS_BUNDLE_ID: "bot.projector.example" };
    for (const [environment, host] of [["sandbox", "https://api.sandbox.push.apple.com"], ["production", "https://api.push.apple.com"]] as const) {
      const origins: string[] = [];
      const forwarder = createForwarder(apnsConfigFromEnv({ ...env, APNS_ENVIRONMENT: environment }), {
        connect: (origin) => {
          origins.push(origin);
          return connect(apns.url);
        },
      });
      await forwarder.send(token(), "sealed");
      forwarder.close();
      assert.deepEqual(origins, [host]);
      assert.equal(APNS_ORIGINS[environment], host);
    }
    for (const environment of ["development", "Production", ""]) {
      assert.throws(() => apnsConfigFromEnv({ ...env, APNS_ENVIRONMENT: environment }), ConfigError);
    }
  } finally {
    await apns.close();
  }
});

test("a push key that is not P-256 is refused when the service starts, not at Apple", () => {
  const env = { APNS_KEY_ID: "KEY1234567", APNS_TEAM_ID: "TEAM123456", APNS_BUNDLE_ID: "bot.projector.example", APNS_ENVIRONMENT: "sandbox" };
  for (const pair of [generateKeyPairSync("ec", { namedCurve: "P-384" }), generateKeyPairSync("ed25519")]) {
    const pem = pair.privateKey.export({ type: "pkcs8", format: "pem" }).toString();
    assert.throws(() => apnsConfigFromEnv({ ...env, APNS_KEY: pem }), /APNS_KEY must be a P-256 key/);
  }
  assert.throws(() => apnsConfigFromEnv({ ...env, APNS_KEY: "not a key" }), /APNS_KEY must hold/);
});

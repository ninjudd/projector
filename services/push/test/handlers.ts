import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import { HOURLY_LIMIT, MAX_NOTIFICATION_BYTES, push, pushFromEnv, register, registerFromEnv, type PushDeps } from "../src/handlers.js";
import { RateLimit } from "../src/limit.js";
import { openPushId, pushIdKey, sealPushId, type Outcome } from "../src/lib.js";
import { appleKey, fakeForwarder, token } from "./support/fakes.js";

const HOUR = 60 * 60 * 1000;
const key = pushIdKey(randomBytes(32).toString("base64"));

function post(body: unknown): Request {
  return new Request("https://push.example/v1/push", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

function deps(outcome?: Outcome, now: () => number = Date.now) {
  const { forwarder, sent } = fakeForwarder(outcome);
  const logs: string[] = [];
  const pushDeps: PushDeps = { key, forwarder, limit: new RateLimit(HOURLY_LIMIT, HOUR, now), log: (line) => logs.push(line) };
  return { deps: pushDeps, sent, logs };
}

test("register answers a push ID that opens to the phone's device token", async () => {
  const device = token();
  const response = await register(post({ token: device.toString("hex") }), { key });
  assert.equal(response.status, 200);
  const { push_id: pushId } = (await response.json()) as { push_id: string };
  assert.deepEqual(openPushId(pushId, key), device);
});

test("register refuses a token that is not hex, so junk never becomes a push ID", async () => {
  assert.equal((await register(post({ token: "not a token" }), { key })).status, 400);
  assert.equal((await register(post({}), { key })).status, 400);
  assert.equal((await register(post("not json"), { key })).status, 400);
});

test("push forwards the sealed notification unchanged to the push ID's device, and answers 202", async () => {
  const { deps: pushDeps, sent } = deps();
  const device = token();
  const notification = "c2VhbGVk.bm90aWZpY2F0aW9u+/=";
  const response = await push(post({ push_id: sealPushId(device, key), notification }), pushDeps);
  assert.equal(response.status, 202);
  assert.deepEqual(sent, [{ token: device, notification }]);
});

test("a tampered push ID gets 404 and nothing is forwarded, so a forged ID reaches no device", async () => {
  const { deps: pushDeps, sent } = deps();
  const pushId = sealPushId(token(), key);
  const tampered = (pushId.startsWith("A") ? "B" : "A") + pushId.slice(1);
  const response = await push(post({ push_id: tampered, notification: "sealed" }), pushDeps);
  assert.equal(response.status, 404);
  assert.deepEqual(sent, []);
});

test("a push ID another instance sealed gets 404 and nothing is forwarded", async () => {
  const { deps: pushDeps, sent } = deps();
  const foreign = sealPushId(token(), pushIdKey(randomBytes(32).toString("base64")));
  const response = await push(post({ push_id: foreign, notification: "sealed" }), pushDeps);
  assert.equal(response.status, 404);
  assert.deepEqual(sent, []);
});

test("push logs neither the notification, the push ID, nor the device token, even when Apple fails", async (t) => {
  const printed: string[] = [];
  for (const method of ["log", "info", "warn", "error", "debug"] as const) {
    t.mock.method(console, method, (...args: unknown[]) => printed.push(args.map(String).join(" ")));
  }
  const { deps: pushDeps, logs } = deps({ status: "failed", reason: "500 InternalServerError" });
  const device = token();
  const pushId = sealPushId(device, key);
  const notification = "sealed-notification-text";
  assert.equal((await push(post({ push_id: pushId, notification }), pushDeps)).status, 502);
  const lines = [...logs, ...printed];
  assert.ok(lines.length > 0, "the failure is logged");
  for (const line of lines) {
    for (const secret of [notification, pushId, device.toString("hex"), device.toString("base64")]) {
      assert.ok(!line.includes(secret), `logged ${JSON.stringify(line)}`);
    }
  }
});

test("a notification over 2 KiB gets 413 and is not forwarded, and one of exactly 2 KiB is sent", async () => {
  const { deps: pushDeps, sent } = deps();
  const pushId = sealPushId(token(), key);
  const over = await push(post({ push_id: pushId, notification: "x".repeat(MAX_NOTIFICATION_BYTES + 1) }), pushDeps);
  assert.equal(over.status, 413);
  assert.deepEqual(sent, []);
  const exact = await push(post({ push_id: pushId, notification: "x".repeat(MAX_NOTIFICATION_BYTES) }), pushDeps);
  assert.equal(exact.status, 202);
});

test("the 2 KiB limit counts UTF-8 bytes, so a limit that counts characters is caught", async () => {
  const { deps: pushDeps, sent } = deps();
  const notification = "é".repeat(MAX_NOTIFICATION_BYTES / 2 + 1);
  assert.ok(notification.length <= MAX_NOTIFICATION_BYTES);
  const response = await push(post({ push_id: sealPushId(token(), key), notification }), pushDeps);
  assert.equal(response.status, 413);
  assert.deepEqual(sent, []);
});

test("a body far larger than any push is refused without being parsed", async () => {
  const { deps: pushDeps, sent } = deps();
  const response = await push(post("x".repeat(64 * 1024)), pushDeps);
  assert.equal(response.status, 413);
  assert.deepEqual(sent, []);
});

test("a push ID Apple reports as unregistered gets 404, so the daemon can drop it", async () => {
  const { deps: pushDeps } = deps({ status: "unregistered" });
  const response = await push(post({ push_id: sealPushId(token(), key), notification: "sealed" }), pushDeps);
  assert.equal(response.status, 404);
});

test("the 61st push in an hour for one push ID gets 429, and another push ID still sends", async () => {
  let now = 1_000_000;
  const { deps: pushDeps, sent } = deps(undefined, () => now);
  const pushId = sealPushId(token(), key);
  for (let index = 0; index < HOURLY_LIMIT; index++) {
    assert.equal((await push(post({ push_id: pushId, notification: "sealed" }), pushDeps)).status, 202, `push ${index + 1}`);
    now += 1000;
  }
  const limited = await push(post({ push_id: pushId, notification: "sealed" }), pushDeps);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), String(HOUR / 1000 - HOURLY_LIMIT));
  assert.equal(sent.length, HOURLY_LIMIT);
  const other = await push(post({ push_id: sealPushId(token(), key), notification: "sealed" }), pushDeps);
  assert.equal(other.status, 202);
});

test("registering the same phone again gives a new push ID but no more notifications", async () => {
  const { deps: pushDeps } = deps(undefined, () => 1_000_000);
  const device = token();
  for (let index = 0; index < HOURLY_LIMIT; index++) {
    await push(post({ push_id: sealPushId(device, key), notification: "sealed" }), pushDeps);
  }
  const fresh = sealPushId(device, key);
  assert.equal((await push(post({ push_id: fresh, notification: "sealed" }), pushDeps)).status, 429);
});

test("the hour slides: a push ID at the limit gets one more push as each old one turns an hour old", async () => {
  const start = 1_000_000;
  let now = start;
  const { deps: pushDeps } = deps(undefined, () => now);
  const pushId = sealPushId(token(), key);
  for (let index = 0; index < HOURLY_LIMIT; index++) {
    await push(post({ push_id: pushId, notification: "sealed" }), pushDeps);
    now += 1000;
  }
  assert.equal((await push(post({ push_id: pushId, notification: "sealed" }), pushDeps)).status, 429);
  now = start + HOUR;
  assert.equal((await push(post({ push_id: pushId, notification: "sealed" }), pushDeps)).status, 202);
  assert.equal((await push(post({ push_id: pushId, notification: "sealed" }), pushDeps)).status, 429);
});

test("an unconfigured service answers 500 and logs the variable's name, never a value", async () => {
  const logs: string[] = [];
  const unregistered = await registerFromEnv({}, (line) => logs.push(line))(post({ token: token().toString("hex") }));
  assert.equal(unregistered.status, 500);
  assert.deepEqual(logs, ["push: PUSH_ID_KEY is not set"]);

  const pem = appleKey().privateKey.export({ type: "pkcs8", format: "pem" }).toString();
  logs.length = 0;
  const env = {
    PUSH_ID_KEY: randomBytes(32).toString("base64"),
    APNS_KEY: pem,
    APNS_KEY_ID: "KEY1234567",
    APNS_TEAM_ID: "TEAM123456",
    APNS_BUNDLE_ID: "bot.projector.example",
    APNS_ENVIRONMENT: "development",
  };
  const pushed = await pushFromEnv(env, (line) => logs.push(line))(post({ push_id: "x", notification: "sealed" }));
  assert.equal(pushed.status, 500);
  assert.deepEqual(logs, ["push: APNS_ENVIRONMENT must be production or sandbox"]);
});

import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import { deviceToken, openPushId, pushIdKey, sealPushId } from "../src/lib.js";
import { token } from "./support/fakes.js";

const key = pushIdKey(randomBytes(32).toString("base64"));

test("a push ID opens to the device token it sealed, so a seal and an open that disagree are caught", () => {
  const device = token();
  assert.deepEqual(openPushId(sealPushId(device, key), key), device);
});

test("every byte of a push ID is authenticated, so encryption without a tag is caught", () => {
  const bytes = Buffer.from(sealPushId(token(), key), "base64url");
  for (let index = 0; index < bytes.length; index++) {
    const tampered = Buffer.from(bytes);
    tampered[index] = (tampered[index] ?? 0) ^ 0x01;
    assert.equal(openPushId(tampered.toString("base64url"), key), null, `byte ${index}`);
  }
});

test("a push ID sealed under another key is refused, so one instance cannot accept another's IDs", () => {
  const other = pushIdKey(randomBytes(32).toString("base64"));
  assert.equal(openPushId(sealPushId(token(), other), key), null);
});

test("two seals of one token differ, so a fixed nonce is caught", () => {
  const device = token();
  assert.notEqual(sealPushId(device, key), sealPushId(device, key));
});

test("a push ID is one spelling only, so stray base64url bits cannot make a second ID", () => {
  const pushId = sealPushId(token(), key);
  // 61 bytes leave four unused bits in the last character.
  const last = pushId.at(-1) ?? "";
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const respelled = pushId.slice(0, -1) + alphabet.charAt(alphabet.indexOf(last) ^ 0x01);
  assert.deepEqual(Buffer.from(respelled, "base64url"), Buffer.from(pushId, "base64url"));
  assert.equal(openPushId(respelled, key), null);
});

test("a device token must be hex of a plausible length, so junk never becomes a push ID", () => {
  assert.deepEqual(deviceToken("ab".repeat(32)), Buffer.alloc(32, 0xab));
  assert.equal(deviceToken("zz".repeat(32)), null);
  // Node's hex decoder stops at the first bad pair rather than refusing.
  assert.equal(deviceToken("ab".repeat(16) + "zz".repeat(16)), null);
  assert.equal(deviceToken("abc"), null);
  assert.equal(deviceToken("ab".repeat(7)), null);
  assert.equal(deviceToken("ab".repeat(101)), null);
});

test("the secret key must be 32 bytes, so a short key is refused rather than padded", () => {
  assert.throws(() => pushIdKey(randomBytes(16).toString("base64")), RangeError);
  assert.throws(() => pushIdKey(""), RangeError);
});

import { createCipheriv, createDecipheriv, createSecretKey, randomBytes, type KeyObject } from "node:crypto";

// A push ID is Apple's device token sealed with AES-256-GCM under the
// service's secret key, so the service can read it back and no one else can
// read or forge one, and the service stores nothing. GCM is node:crypto's
// AEAD on every platform Node runs on, and AES is hardware-accelerated almost
// everywhere. Its nonce is random, because a service with no storage cannot
// keep a counter: a random 96-bit nonce stays within NIST SP 800-38D's bound
// for 2^32 seals under one key, and the service seals once per registration.
//
// The bytes are: one version byte, the 12-byte nonce, the encrypted token,
// and the 16-byte tag, in unpadded base64url. The version byte is also the
// additional authenticated data, so a later format can change everything
// after it and an ID can never be read as the wrong version.
const VERSION = 1;
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
const KEY_BYTES = 32;
// Apple documents device tokens as variable length, so this accepts any
// length a token plausibly has rather than the 32 bytes tokens have today.
const MIN_TOKEN_BYTES = 8;
const MAX_TOKEN_BYTES = 100;
const DEVICE_TOKEN = /^(?:[0-9a-fA-F]{2})+$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;
const MAX_PUSH_ID_LENGTH = Math.ceil(((1 + NONCE_BYTES + MAX_TOKEN_BYTES + TAG_BYTES) * 4) / 3);

/** Parses Apple's device token from the hex the app sends, or returns null. */
export function deviceToken(hex: string): Buffer | null {
  if (!DEVICE_TOKEN.test(hex)) return null;
  const token = Buffer.from(hex, "hex");
  return token.length >= MIN_TOKEN_BYTES && token.length <= MAX_TOKEN_BYTES ? token : null;
}

/** Makes the service's secret key from its base64 encoding, which must hold exactly 32 bytes. */
export function pushIdKey(base64: string): KeyObject {
  const bytes = Buffer.from(base64, "base64");
  if (bytes.length !== KEY_BYTES) {
    throw new RangeError(`the push ID key must be ${KEY_BYTES} bytes in base64`);
  }
  return createSecretKey(bytes);
}

/** Seals a device token into a push ID that only `key` opens. */
export function sealPushId(token: Buffer, key: KeyObject): string {
  const header = Buffer.of(VERSION);
  const nonce = randomBytes(NONCE_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, nonce, { authTagLength: TAG_BYTES });
  cipher.setAAD(header);
  const sealed = Buffer.concat([cipher.update(token), cipher.final()]);
  return Buffer.concat([header, nonce, sealed, cipher.getAuthTag()]).toString("base64url");
}

/** Opens a push ID to its device token, or returns null for any ID that `key` did not seal. */
export function openPushId(pushId: string, key: KeyObject): Buffer | null {
  if (pushId.length > MAX_PUSH_ID_LENGTH || !BASE64URL.test(pushId)) return null;
  const bytes = Buffer.from(pushId, "base64url");
  // Base64url decoding ignores stray trailing bits, so an ID that does not
  // re-encode to itself is a second spelling of another ID, not this one.
  if (bytes.toString("base64url") !== pushId) return null;
  const tokenBytes = bytes.length - 1 - NONCE_BYTES - TAG_BYTES;
  if (bytes[0] !== VERSION || tokenBytes < MIN_TOKEN_BYTES || tokenBytes > MAX_TOKEN_BYTES) return null;
  const nonce = bytes.subarray(1, 1 + NONCE_BYTES);
  const sealed = bytes.subarray(1 + NONCE_BYTES, bytes.length - TAG_BYTES);
  const decipher = createDecipheriv("aes-256-gcm", key, nonce, { authTagLength: TAG_BYTES });
  decipher.setAAD(bytes.subarray(0, 1));
  decipher.setAuthTag(bytes.subarray(bytes.length - TAG_BYTES));
  try {
    return Buffer.concat([decipher.update(sealed), decipher.final()]);
  } catch {
    return null;
  }
}

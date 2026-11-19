import { createPrivateKey, type KeyObject } from "node:crypto";
import type { ApnsConfig } from "./apns.js";
import { pushIdKey } from "./push-id.js";

/** A setting that is missing or malformed. Its message names the variable and never its value. */
export class ConfigError extends Error {}

export type Env = Readonly<Record<string, string | undefined>>;

/** Reads the service's secret key from `PUSH_ID_KEY`. */
export function pushIdKeyFromEnv(env: Env = process.env): KeyObject {
  const value = required(env, "PUSH_ID_KEY");
  try {
    return pushIdKey(value);
  } catch {
    throw new ConfigError("PUSH_ID_KEY must be 32 random bytes in base64");
  }
}

/** Reads Apple's push settings from the `APNS_` variables. */
export function apnsConfigFromEnv(env: Env = process.env): ApnsConfig {
  const pem = required(env, "APNS_KEY");
  let key: KeyObject;
  try {
    key = createPrivateKey(pem);
  } catch {
    throw new ConfigError("APNS_KEY must hold the contents of Apple's .p8 push key");
  }
  if (key.asymmetricKeyDetails?.namedCurve !== "prime256v1") {
    throw new ConfigError("APNS_KEY must be a P-256 key, as Apple's .p8 push keys are");
  }
  const environment = required(env, "APNS_ENVIRONMENT");
  if (environment !== "production" && environment !== "sandbox") {
    throw new ConfigError("APNS_ENVIRONMENT must be production or sandbox");
  }
  return {
    key,
    keyId: required(env, "APNS_KEY_ID"),
    teamId: required(env, "APNS_TEAM_ID"),
    bundleId: required(env, "APNS_BUNDLE_ID"),
    environment,
  };
}

function required(env: Env, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new ConfigError(`${name} is not set`);
  return value;
}

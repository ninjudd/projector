// The service's library: the push ID's encryption and the forwarder to
// Apple's push service, apart from the public handlers and their rate limit.
// A deployment that puts its own authentication and its own limit in front
// imports this module and mounts neither handler, because the handlers ask
// for no credential beyond the push ID.
export { deviceToken, openPushId, pushIdKey, sealPushId } from "./push-id.js";
export {
  APNS_ORIGINS,
  createForwarder,
  providerToken,
  type ApnsConfig,
  type Environment,
  type Forwarder,
  type ForwarderOptions,
  type Outcome,
  type ProviderToken,
} from "./apns.js";
export { ConfigError, apnsConfigFromEnv, pushIdKeyFromEnv, type Env } from "./env.js";

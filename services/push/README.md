# Projector push service

The push service wakes Projector's iPhone app when an agent needs you. The
daemon on your machine seals each notification to your phone's push key,
and the service forwards the sealed text to Apple's push service without
being able to read it. The service stores nothing: a push ID is the phone's
device token, encrypted under the service's secret key, so the service needs
no database and loses no registration when you redeploy it.

The service is a few hundred lines of TypeScript on Node 24 with no runtime
dependencies, so you can read all of it before you trust it with Apple's
push key. The app and the daemon side are still being built;
[the phone plan](../../docs/projects/console/phone/readme.md) describes
them.

## Calls

Both calls take and return JSON.

| Call | Body | Answer |
| --- | --- | --- |
| `POST /v1/register` | `{"token": "<hex>"}`, Apple's device token | `200` with `{"push_id": "<id>"}`, or `400` for a token that is not hex |
| `POST /v1/push` | `{"push_id": "<id>", "notification": "<sealed text>"}`, with the notification at most 2 KiB | `202` once Apple takes it, `404` for a push ID this instance did not issue or that Apple reports as unregistered, `413` for a notification over 2 KiB, `429` past 60 notifications an hour for one phone, and `502` when Apple refuses it for another reason |

The service sends Apple an alert with `mutable-content` set and the sealed
text unchanged under the payload's `sealed` key. The app's notification
service extension opens it on the phone. If the extension cannot, the lock
screen shows "An agent needs you."

Knowing a push ID is the only credential `POST /v1/push` asks for, so treat
one like a password. The rate limit caps what a leaked ID can do. Each
function instance counts in its own memory, so the limit is best-effort
rather than shared across instances.

The service logs no request body, push ID, or device token. It logs a
missing setting by its name, and a refusal from Apple by Apple's one-word
reason, such as `403 InvalidProviderToken`.

## Deploy your own instance

An instance can wake only a build of the app signed by the Apple developer
team whose push key it holds. Apple grants push only to teams in the paid
Apple Developer Program. So the hosted instance wakes the App Store app, and
your instance wakes a build you sign with your own team and bundle ID.

1. In your Apple developer account, under **Certificates, Identifiers &
   Profiles**, give your app's bundle ID the Push Notifications capability.
   Then, under **Keys**, create a key with Apple Push Notifications service
   enabled. Download its `.p8` file, which Apple lets you download only
   once, and note its key ID. Your team ID is on the **Membership details**
   page.
2. Create a Vercel project from your copy of this repository, and set its
   root directory to `services/push`. Vercel deploys the two calls as
   functions on the Node runtime, and `vercel.json` routes `/v1/*` to them.
   From the command line, `vercel link` in `services/push/` does the same.
3. Set the project's environment variables, for the Production environment
   only. `.env.example` lists them.

   | Variable | Value |
   | --- | --- |
   | `APNS_KEY` | The contents of the `.p8` file |
   | `APNS_KEY_ID` | The push key's key ID |
   | `APNS_TEAM_ID` | Your team ID |
   | `APNS_BUNDLE_ID` | The bundle ID of the build this instance wakes |
   | `APNS_ENVIRONMENT` | `sandbox` for a build you install from Xcode, `production` for a TestFlight or App Store build |
   | `PUSH_ID_KEY` | The service's secret key, 32 random bytes in base64 |

   For example, from `services/push/`:

   ```sh
   vercel env add APNS_KEY production < AuthKey_ABC123DEFG.p8
   openssl rand -base64 32 | vercel env add PUSH_ID_KEY production
   ```

4. Deploy with `vercel deploy --prod`, and check that the service answers:

   ```sh
   curl -s https://<your-instance>/v1/register \
     -H 'content-type: application/json' \
     -d '{"token": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"}'
   ```

   It prints a push ID. A `500` answer means a variable is missing or
   malformed, and the function's log names it. Registering reads only
   `PUSH_ID_KEY`; the first push reads the `APNS_` variables.

Pick `APNS_ENVIRONMENT` for the build you install. A build installed from
Xcode is development-signed, and its device token works only with Apple's
sandbox, so Apple refuses it in production as `BadDeviceToken`, which the
service answers as `404`. TestFlight and App Store builds need `production`.
Run one instance for each environment you use.

Keep `PUSH_ID_KEY` once you set it. A new key voids every push ID the old
one sealed, so each phone must register again.

## Import the library instead of the handlers

`src/lib.ts` exports the pieces apart from the two public handlers and their
rate limit: `sealPushId` and `openPushId` for the push ID, `createForwarder`
for Apple's push service, and the functions that read the variables above.
A deployment that authenticates its callers and keeps a shared limit
imports this module, puts its own checks in front, and mounts neither
handler, which ask for nothing beyond a push ID.

```ts
import { apnsConfigFromEnv, createForwarder, openPushId, pushIdKeyFromEnv } from "./src/lib.js";

const key = pushIdKeyFromEnv();
const forwarder = createForwarder(apnsConfigFromEnv());

const device = openPushId(pushId, key);
const outcome = device && (await forwarder.send(device, notification));
// outcome.status is "sent", "unregistered", or "failed".
```

## Develop

Run these in `services/push/` with Node 24:

```sh
npm ci
npm run check
npm test
```

`npm run check` type-checks the service and its tests. `npm test` compiles
them to `dist/` and runs them with `node --test`, against a local HTTP/2
server that stands in for Apple's push service. The test files do not end
in `.test.ts`, because `claude plugin test .` at the repository root runs
every such file as a test of Projector's hooks module.

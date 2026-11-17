---
status: draft
priority: later
---

# Answer your agents from your phone

A Projector iPhone app shows the console's sessions, pull requests, and
inbox items, and lets you answer and discuss them. It needs only your
GitHub credentials and your relay key, and it reaches your machines through
the relay's hidden refs, with no Projector server.

## 1. Problem

`project console` (the parent project, `console`) lets you answer and
discuss every live session's items, but only in a terminal on the machine
those sessions run on. When you step away, your agents wait. A question
posted while you are out of reach stalls its session until you come back to
a terminal.

The pieces for a remote view already exist:

- **The relay** carries agents' presence and messages between your
  machines through hidden refs in a GitHub repository they share. Every
  record is sealed with your relay key, so the repository holds only
  ciphertext.
- **GitHub's API** can read and write those refs, so a phone with a GitHub
  token can reach them without Git.
- **`project agent key join` and `key grant`** already copy the relay key
  to a new machine without showing it.

The relay is built for agents, though, not for you. It carries no inbox
items, and by design a relay message carries none of your authority: every
machine holds the relay key, and an agent running as you can read it.

## 2. Solution

The watch daemon publishes each live session's state to the relay, and the
app reads it, draws the console's home list and panels, and writes your
answers back as signed records. Three changes make that safe:

- **The phone signs as you.** The phone holds a signing key of its own that
  never leaves it. You register its public key on each machine in a
  terminal. The daemon records a phone record as yours only when that key
  signed it, so no agent can forge an answer, even one that reads the relay
  key.
- **The daemon publishes sessions and items.** For each live session it
  already relays, the daemon publishes the session's open inbox items and
  its pull request subscriptions, sealed like every relay record.
- **The daemon imports your answers.** It fetches the phone's records,
  checks each signature, and applies it with the same inbox functions the
  console calls. The session's own deliverer then brings it in: the panel
  for Claude Code, and the Codex adapter for Codex.

The app is a SwiftUI app in its own public repository, so anyone can read
the code that holds their GitHub token and their keys.

### 2.1 Flow

1. **Set up the phone once.**
   1. Sign in to GitHub in the app, with GitHub's device flow. The token
      needs read and write access to the repositories your sessions work
      in.
   2. Join the relay key. The app makes a temporary key pair and shows a
      `project agent key grant …` command and a fingerprint, exactly as
      `key join` does. You run the command on a machine that has the key,
      check the fingerprint, and paste its one-line answer into the app.
   3. Make the phone's signing key, and register it. The app shows its
      public key as a `project agent phone add …` command with a
      fingerprint. You run it in a terminal on each machine whose agents
      the phone may answer. The command refuses without a terminal, as
      `key grant` does.
2. **The daemon publishes.** On each relay pass, for each live session with
   a relay presence, the daemon writes one sealed record per open inbox
   item, and one sealed record of the session's subscriptions with their
   roles. It deletes a record once its item resolves or its session lapses.
3. **The app reads.** When you open it, and on background refresh, the app
   lists the key's refs in each repository it knows, opens the records, and
   asks GitHub directly for each subscribed pull request's state, checks,
   and unresolved threads. It turns those into rows with the same rules as
   the console, and draws the home list and each session's panel.
4. **You answer.** The app writes your choice as an answer record, signed
   with the phone's key and then sealed. It holds one action: an answer,
   `done`, `decline`, `dismiss`, `snooze`, `restore`, `start`, `unclear`,
   `read`, or a `discuss` message, with the item's reference and the
   item's content digest, so an answer to an item that has since changed
   is refused.
5. **The daemon imports.** On its next pass, the daemon on the session's
   machine fetches the record, checks the seal, then the signature against
   the registered phone keys, and then that the record is fresh and was not
   seen before. It applies the action with the inbox function the console
   calls, deletes the ref, and republishes the item. A record that fails a
   check is rejected and logged, as the relay logs rejections today.
6. **The session hears it.** The session's deliverer claims the new line
   and brings it in as a new turn, as it does for an answer from the
   console.

### 2.2 New relay records

| Ref | Written by | Holds |
| --- | --- | --- |
| `refs/projector/items/<key-id>/<relay-id>/<ref>` | The daemon | One open inbox item, its digest, and its thread |
| `refs/projector/subscriptions/<key-id>/<relay-id>` | The daemon | The session's pull requests and its role on each |
| `refs/projector/answers/<key-id>/<relay-id>/<id>` | The phone | One signed action on one item, or a `discuss` message that starts a discussion |

Each record keeps the relay's limits: a parentless commit whose tree holds
one `sealed.json` of at most 32 KiB, sealed to the key's own public key.
An answer record also carries, inside the seal:

| Field | Value |
| --- | --- |
| `phone` | The ID of the phone key that signed it |
| `item` | The item's reference and its digest when the phone read it |
| `action` | The action and its argument, such as the letter or the message |
| `at` | When the phone wrote it. The daemon rejects a record older than 7 days, as the relay does for messages |
| `nonce` | A random ID. The daemon remembers the IDs it applied for 7 days and rejects a repeat |
| `signature` | The phone key's signature over every field above |

### 2.3 Changes by repository

| Where | Change |
| --- | --- |
| `src/projector/relay.py` | Publishes item and subscription records, and fetches, checks, and applies answer records |
| `src/projector/phone.py` | New. Registered phone keys, `project agent phone add`, `list`, and `remove`, and signature checks |
| `src/projector/crypto.py` | The signature check, in pure Python like the rest of the module (§ 5) |
| `src/projector/console.py` | Moves the console's "record an action for a session" calls into a function the relay import shares |
| `src/projector/permissions.py` | Asks before `project agent phone add` and `remove` in Claude Code, and leaves both to Codex's approval |
| `tests/fixtures/relay/` | New. Sealed and signed records with their plaintexts, which the Python tests and the app's tests both check |
| `docs/cli.md` | Describes the phone records, the commands, and what a leaked key exposes |
| The app's repository | The SwiftUI app: GitHub sign-in, key join, the phone key, ref reading and writing through GitHub's API, the seal, the row model, the home list, the panels, and background refresh |

## 3. Why this design

- **The phone's own key carries your authority.** The relay key is on
  every machine, and an agent can read it there, so a record sealed with it
  says nothing about who wrote it. A key that exists only on the phone, and
  that you register in a terminal, keeps the rule that agents cannot answer
  for you.
- **No Projector server.** The relay already moves records through GitHub.
  A server would need hosting, an account system, and its own security
  review, and it would see when you answer even if it could not read what
  you said.
- **The daemon imports answers, not the deliverers.** Applying an action
  with the console's inbox functions means the phone, the console, and the
  terminal all record the same thing, and the panel and the Codex adapter
  deliver it unchanged.
- **One record per item.** A session's items together can pass the relay's
  32 KiB limit, and one record per item lets the daemon delete a resolved
  item without rewriting the rest.
- **An answer names the item's digest.** An agent can revise an item while
  your answer is on its way. The digest makes the daemon refuse an answer
  to text you never saw, as `unclear` already checks the text you read.
- **The app is open source.** It holds your GitHub token, your relay key,
  and the key that answers for you. Users can trust it only if they can
  read it and build it themselves. A separate repository keeps Swift, Xcode,
  and App Store releases out of this repository's Python and TypeScript
  gate, while the protocol and its test vectors stay here.
- **The row model is shared again.** The app computes rows from GitHub's
  answers, as the console computes them from the daemon's cache. The
  fixtures in `tests/fixtures/rows/` run through the app's model too, so a
  third copy cannot drift.

## 4. Acceptance criteria

| Do | Expect | Catches |
| --- | --- | --- |
| Post a question in a relayed session | Within one relay pass, an item record appears under the session's relay ID, sealed, and it opens to the item | Items that never reach the phone |
| Resolve the item from the console | The item record is deleted on the next pass | Answered items that stay on the phone |
| Write a signed answer record for item 3 | Item 3 is answered as the record says, the ref is deleted, and the session's deliverer sends `ANSWER 3.a …` | An answer that never reaches the inbox |
| Write the same record a second time | The daemon rejects it as a repeat, and item 3 is unchanged | A replayed answer |
| Write an answer record sealed correctly but signed with an unregistered key | Rejected and logged, and item 3 stays open | An agent that answers for you with the relay key |
| Revise item 3, then apply an answer that names its old digest | Rejected as stale, and item 3 stays open | An answer to text you never saw |
| Run `project agent phone add` without a terminal | Exit 69, nothing registered | An agent that registers its own key |
| Seal and sign each fixture in `tests/fixtures/relay/` in Python and in the app | Both produce records the other opens and verifies | A seal or signature that only one side can read |
| Run each fixture in `tests/fixtures/rows/` through the app's row model | The fixture's expected rows | A third row model that drifts |

## 5. Cost

- **GitHub quota on the phone.** A refresh lists the key's refs in each
  repository, reads each new record, and asks GraphQL for each subscribed
  pull request. With ten sessions and thirty pull requests, measure one
  refresh at under 50 REST calls and 5 GraphQL points, well inside the
  5,000 requests an hour a token gets.
- **The daemon's pushes.** Items change rarely, so publishing adds a few
  refs to the one push each relay pass already makes.
- **What a leaked relay key exposes.** A deleted ref's commit stays
  readable by its SHA on GitHub. Inbox items hold more detail than presence
  records, so a leaked key exposes every item the relay published. The
  documentation says so, as it does for messages today.

## 6. Open questions

- **Which signature scheme.** The iPhone's Secure Enclave keeps a key that
  can never be copied off the device, but it supports only P-256 ECDSA.
  Python's standard library has no P-256, so the daemon's check would need
  a pure-Python implementation, as `crypto.py` already implements X25519
  and ChaCha20. Ed25519 in the Keychain is simpler to verify but can be
  backed up. Decide before the plan is `ready`. Owner: the user.
- **How the phone finds repositories.** The relay works per repository,
  and the phone cannot list every repository that holds your refs without
  probing each one. Options: the grant line carries the repositories the
  granting machine relays, or the daemon publishes a roster to a home
  repository you name. Owner: whoever takes this project.
- **Notifications.** Without a server, the app refreshes when you open it
  and when iOS grants background refresh, which can be many minutes apart.
  Instant push needs Apple's push service and so a server. Deferred: the
  first version has no push.
- **The app's repository and name.** A separate public repository is the
  recommendation (§ 3). Owner: the user.
- **App Store distribution.** Publishing needs an Apple developer account
  and App Review. TestFlight can come first. Owner: the user.
- **Messages to agents.** The phone could also send relay messages to an
  agent, as `project agent send` does. They would carry none of your
  authority, so the first version sends only signed answers and
  discussion. Deferred.

## 7. Background

The relay's design is in `src/projector/relay.py`'s module docstring and in
`docs/cli.md` under "Message agents on other machines". Records are sealed
with ChaCha20 and HMAC-SHA256 under a key agreed with X25519, in
`src/projector/crypto.py`, which implements them in pure Python because the
CLI has no runtime dependencies. Apple's CryptoKit provides X25519 and
ChaCha20-Poly1305, but not the relay's ChaCha20-and-HMAC construction, so
the app implements that construction itself and checks it against the
shared fixtures.

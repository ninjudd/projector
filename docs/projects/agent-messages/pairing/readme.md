---
status: ready
priority: now
---

# Copy the relay key between machines without showing it

`project agent key join` on a new machine and `project agent key grant` on a
machine that has the key move the relay's private key between them, sealed,
so that nothing you copy from one terminal to the other is secret.

## 1. Problem

The agent-messages relay seals every record with one X25519 key pair that
each of your machines holds. You give a second machine that key today with
two commands. `project agent key export` prints the private key itself, as
an `agent.key = "x25519:<base64>"` line, and `project agent key import` on
the other machine reads that line back. Both refuse to run without a
terminal, so an agent cannot run them and copy the key into its transcript.

That guard stops an agent, but not you. The key travels as text on your
screen, and one paste puts it wherever you paste. In practice the exported
line was pasted into an agent conversation along with other terminal output,
and the key had to be thrown away. Anyone who holds it can open every record
the relay ever pushed, and a deleted ref's commit stays readable by its SHA
on GitHub, so the exposure is permanent.

`key init` has a second way to leave the key readable. When no keychain
answers, as on headless Linux, it falls back to writing the key into
`~/.projector.toml` on its own, and says so only after the fact.

## 2. Solution

Replace `key export` and `key import` with two commands that copy the key
sealed. On the new machine, `project agent key join` makes a temporary
X25519 key pair that lives only in its own memory. It prints one command to
run on the machine that has the key, `project agent key grant <request>`,
whose request carries the temporary public key. It then waits for you to
paste back one line. `key grant` seals the relay's private key to that
temporary public key with the existing `crypto.seal` and prints the sealed
line with the key's ID. `key join` opens the line, checks the key against
the ID you confirm, stores it in the keychain, and exits, and the temporary
key goes with the process. The request is a public key and the line opens
only with a private key that never left the new machine, so either one can
land anywhere without harm. `key init` and `key join` also stop falling
back to an inline key unless you pass `--inline`.

### 2.1 Runtime flow

1. **Start on the new machine.** You run `project agent key join` in a
   terminal on machine A. It refuses without a terminal on standard input
   and output. It refuses when `agent.key` is already set, unless you pass
   `--replace`. It refuses when no keychain answers, unless you pass
   `--inline`, and the error says why the keychain did not answer.
2. **Make the request.** `join` makes a temporary private key with
   `crypto.new_private_key` and keeps it only in a local variable. It
   prints the command to run on the other machine and the request's
   fingerprint:

   ```console
   $ project agent key join
   On a machine that has the relay key, run this in a terminal:

     project agent key grant pj1-laptop-Zx8k2Q...Qm9w

   Request fingerprint: K7Q-4MD-9XP
   Paste the line it prints here:
   ```

3. **Grant on the other machine.** You paste that command into a terminal
   on machine B. `grant` refuses without a terminal on standard input and
   output, and refuses when this machine holds no key. It shows the request
   before it does anything:

   ```console
   $ project agent key grant pj1-laptop-Zx8k2Q...Qm9w
   Request from laptop, fingerprint K7Q-4MD-9XP.
   Seal key 3f2a9c1d7e4b6a05 to it? [y/N] y
   Paste this line into the waiting `project agent key join`:

     pk1-eyJ2IjoxLCJzZW5kZXIiOi...

   Key 3f2a9c1d7e4b6a05
   ```

   Anything but `y` or `yes` stops it and prints nothing. On `y`, it reads
   the relay key with `relay.read_key`, seals the key's 32 private bytes to
   the request's public key with `crypto.seal`, using the relay key as the
   sender, and prints the result.
4. **Accept on the new machine.** You paste the line into the waiting
   `join`. It opens the line with `crypto.unseal` and the temporary private
   key, which checks the tag before it decrypts anything. It then checks
   that the opened private key's public key equals the sealed record's
   sender, so the line was sealed by the key it carries. It asks:

   ```console
   Key 3f2a9c1d7e4b6a05. Does the other machine show the same key? [y/N] y
   key 3f2a9c1d7e4b6a05 stored in the macOS Keychain; ~/.projector.toml names it as agent.key = "keychain:3f2a9c1d7e4b6a05"
   ```

   On `y`, it stores the key with the existing `relay.save_key`. Anything
   else, a line that does not open, or a key whose public key is not the
   sender stores nothing and exits with an error.
5. **End.** `join` exits, and the temporary private key goes with it. It
   waits at most 30 minutes for the line, and Ctrl-C ends it at any point.
   Either way it stores nothing and leaves no file behind.

### 2.2 Data shapes

| Text | Format | Holds |
| --- | --- | --- |
| Request | `pj1-` + machine label + `-` + base64url of the temporary public key, without padding | The machine label is the short host name, reduced to lowercase letters, digits and hyphens and cut to 20 characters, for display only. The public key is 32 bytes. |
| Fingerprint | Three groups of three characters from Crockford base32, such as `K7Q-4MD-9XP` | The first 45 bits of the SHA-256 of the temporary public key. `join` and `grant` both compute it from the same public key. |
| Sealed line | `pk1-` + base64url of the compact JSON of the `crypto.seal` record, without padding | The existing sealed record, `{v, sender, ephemeral, ciphertext, tag}`, whose plaintext is the relay key's 32 private bytes. It is about 300 characters. |

`grant` and `join` refuse a request or line with another prefix, version, or
length, and say which they expected. The prefixes make a pasted value's
kind obvious, and the version lets a later format coexist.

### 2.3 Components

| File | Change |
| --- | --- |
| `src/projector/relay.py` | Add `make_request`, `parse_request`, `fingerprint`, `grant_line`, and `accept_line`. Remove `export_line`, `import_key`, and `parse_line`. Change `COPY` to say "To give another machine this key, run `project agent key join` there in a terminal." Make `init_key` and `save_key` refuse to store inline unless `inline` is passed, naming the keychain's error and `--inline`. |
| `src/projector/cli.py` | Replace the `export` and `import` subparsers with `join`, taking `--inline` and `--replace`, and `grant <request>`. Both check for a terminal as `export` and `import` did. `join` reads its line with a 30-minute timeout. Change the `config set agent.key` refusal to name `project agent key join`. |
| `src/projector/crypto.py` | No change. `seal`, `unseal`, `new_private_key`, `public_key`, `key_id`, and `encode` already do the work. |
| The `message` skill, `skills/message/SKILL.md` | The key commands an agent never runs become `key init`, `key join`, `key grant`, and `key remove`. Tell the user to run `key join` on the new machine and paste the command it prints into a machine that has the key. |
| `docs/cli.md` | Rewrite "Make and copy the key" around `join` and `grant`, and update the `agent.key` setting row and the `--inline` text. |
| `tests/test_relay.py` | Replace the `export` and `import` tests with the criteria in § 4, reusing the pseudo-terminal helper the import tests use. |

## 3. Why this design

- **Copy only public or sealed text.** A command that never shows the key
  cannot leak it through the screen, whatever a person does with their
  terminal output. The request is a public key, and the sealed line opens
  only with a private key that exists in one process on the new machine.
- **Keep the temporary key in memory.** A key that never touches disk or
  the keychain needs no expiry, no cleanup, and no recovery after a crash.
  Waiting interactively is what makes that possible, so `join` is one
  command rather than a request and a separate accept.
- **Print the whole command.** `join` prints `project agent key grant
  <request>`, so you copy one line to machine B and need to know no syntax.
- **Confirm on both sides.** The fingerprint lets you check, on machine B,
  that the request is the one you just made rather than one an agent or
  another person handed you. The key ID check stops a line sealed with
  another key, which would otherwise pass every cryptographic check,
  because whoever sealed it held that key.
- **Keep `grant` for a person.** `grant` is the one command that reads the
  real key, and it seals it to any request it is given. An agent tricked
  into running it with an attacker's request would hand the key over, so
  it requires a terminal and a confirmation, and the skill forbids it.
- **Never fall back silently.** A key stored inline is a key in a file that
  every process running as you can read, so storing one has to be an
  explicit `--inline`.
- **Reuse the relay's sealing.** `crypto.seal` already encrypts to a public
  key and authenticates the sender, and `relay.save_key` already stores a
  key and writes `agent.key`. The new commands add only the request and
  line formats around them.
- **Rejected: carrying the request and line through git refs.** It needs a
  repository both machines work in, and it leaves the sealed key on GitHub
  for good, readable by SHA.
- **Rejected: a passphrase-encrypted export.** The passphrase becomes a
  second secret to type on both machines and to keep out of transcripts.
- **Rejected: a key pair per machine.** No private key would ever move,
  but every record would have to be sealed to every machine's key, which
  changes the relay's ref layout and opening rules far more than this
  change does.

## 4. Acceptance criteria

The tests run `join` and `grant` against temporary state roots and user
config files that stand in for two machines, with the fake keychain tools
the relay tests use and the pseudo-terminal helper the import tests use.

| Exercise | Expected result, and the bug it catches |
| --- | --- |
| Run `join` on machine A, run the command it prints with `grant` on machine B and answer `y`, paste the line into A and answer `y`. | A's `key show` reports B's key ID, and the key is in A's keychain. The two fingerprints match. Catches a round trip that does not work. |
| Capture every byte `join` and `grant` write, and every argument of every process they start. | None contains the relay's private key in any encoding. Catches the key reaching the screen or a process list. |
| Paste a line sealed to a different request, a line with one character changed, and a line with another prefix. | Each is refused, and nothing is stored. Catches a missing tag check or format check. |
| Paste a line that a different key sealed to A's request, and answer the key ID question `n`. | Nothing is stored. Catches a substituted key adopted without confirmation. |
| Paste a line whose sealed sender is not the key it carries. | It is refused before the question. Catches a missing consistency check. |
| Run `grant` and answer anything but `y` or `yes`. | It prints no line. Catches a grant without consent. |
| Run `join` or `grant` without a terminal on standard input or output. | Each refuses and names the command to run yourself. Catches an agent running either command. |
| End `join` with Ctrl-C, and let it time out. | Nothing is stored, and no file is created under the state root or the home directory. Catches a temporary key written anywhere. |
| Run `join` while `agent.key` holds a different key, with and without `--replace`. | Without it, `join` refuses. With it, the old keychain item is deleted and the new key stored. Catches a silent overwrite. |
| Run `init` and `join` with no keychain answering, with and without `--inline`. | Without it, each refuses and names the keychain's error and `--inline`. With it, each stores inline with mode 0600. Catches a silent fall back to a readable file. |
| Run `project agent key export` and `project agent key import`. | Each is a usage error that names `key join`. Catches the old commands surviving. |
| Run `project config set agent.key x`. | It refuses and names `key join`. Catches a stale pointer to `key import`. |

Prove that each new regression test fails when the fault it names is
introduced, as `skills/testing.md` asks. After merge, pair a second
machine by hand and confirm that messages flow between them.

## 5. Cost

Pairing runs once per machine, by hand. It costs three X25519 operations to
seal and two to open, a few milliseconds in pure Python, and no network.
It touches no daemon path and no relay pass.

## 6. Background

- `relay.export_line` prints `agent.key = "x25519:<base64>"`, the private
  key in full, and `relay.parse_line` reads it back for `import_key`.
  `run_agent_key` in `src/projector/cli.py` checks only that standard input
  and output are terminals.
- `relay.save_key` stores the key in the keychain when `keychain.store`
  succeeds and, when it raises, writes the key inline with the keychain's
  error as the reason, unless `inline` was passed. `init_key` calls it, so
  `key init` falls back on its own.
- `crypto.seal(sender_private, recipient, plaintext)` derives its keys from
  an ephemeral key and the sender's static key, and `crypto.unseal` returns
  the sender's public key with the plaintext after checking the tag. A
  record sealed by the relay key to the temporary public key therefore
  proves to `join` that its sealer held the key it carries.

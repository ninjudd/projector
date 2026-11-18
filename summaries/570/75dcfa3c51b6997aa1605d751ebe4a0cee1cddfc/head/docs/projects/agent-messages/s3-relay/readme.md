---
status: completed
priority: now
---

# Relay through an S3 bucket

`agent.relay = "s3://<bucket>/<prefix>"` carries the relay's sealed records
as objects in an S3 bucket, or any store that speaks S3 with conditional
writes, instead of as refs in a Git repository. The relay then answers in
seconds, and its cost does not grow with a repository's ref count.

## 1. Problem

The relay (`../readme.md` § 2.2) stores each sealed record as a ref in a
GitHub repository, and `../shared-relay/readme.md` lets you choose that
repository. Git makes the relay free to set up, but it limits how fast and how
far it goes:

- **Latency.** A daemon fetches once per watch pass, every `watch.interval`
  seconds: 60 by default and at least 30. A message or peers reply waits up to
  that long before its recipient sees it, and each fetch from GitHub takes
  about 0.8 seconds even when it finds nothing.
- **Leftovers.** Every renewal and every message leaves a commit in the
  repository's object store after its ref is gone, and GitHub keeps a
  deleted ref's commit readable by its SHA until GitHub Support collects it.
  A company relay accumulates them from every person's agents.
- **Scale.** Each push carries a pass's changes in one Git transaction, and
  every relay shares GitHub's rate limits on Git operations with the people
  pushing code.

An object store has none of these costs. Listing a prefix takes tens of
milliseconds, a deleted object is gone, and S3 now supports the conditional
writes that the relay's leases need.

## 2. Solution

Accept `s3://<bucket>/<prefix>` as a relay location, beside
`github:<owner>/<repo>`. Behind the relay job, add a transport with three
operations: list the records under some prefixes with their versions, read
one record, and apply a batch of changes with expected versions. The Git code
that exists today becomes the Git transport, unchanged in behavior. A new S3
transport implements the same three operations with `ListObjectsV2`,
`GetObject`, `PutObject`, and `DeleteObject`, signed with AWS Signature
Version 4 from the standard library. Each object holds one `sealed.json`,
named like its ref, and its ETag plays the part of a commit SHA:
`If-None-Match: *` creates, `If-Match: <etag>` updates or deletes under a
lease, and a `412` is a stale lease. Objects are laid out by recipient key,
`<prefix>/<key-id>/…`, so one listing per poll finds everything addressed to
you. An S3 location is polled every `agent.s3_poll` seconds, 10 by default,
on the relay's worker pool. Everything above the transport stays as it is:
sealing, the outbox, the checks before opening, delivery, the peers inbox,
and the controls in `../shared-relay/readme.md` § 2.3.

### 2.1 Runtime flow

1. **Configure.** You set `agent.relay = "s3://acme-agents/relay"` in the
   `.projector.toml` whose reach you want. `agent.s3_region`,
   `agent.s3_endpoint`, and `agent.s3_profile` say where the bucket is and
   which credentials to use. `agent.s3_endpoint` and `agent.s3_profile` are
   destination settings, as `agent.relay` is (`../../config-trust/readme.md`
   § 2.1 step 5). An endpoint can name any HTTPS server, which would accept
   every write without needing your credentials, and a profile picks which of
   your AWS identities signs. So when a tracked file sets any of the three,
   each value is used only once you run `project config allow <key> <value>`
   in a terminal. Values from untracked files and `~/.projector.toml` need no
   allowing. The location's identity is its endpoint, bucket, and prefix
   together (§ 2.3), so two checkouts that name the same bucket and prefix on
   two endpoints use two locations.
2. **Find credentials.** The S3 client takes the first of these that is
   present, and never prints, logs, or passes a secret as a process argument:
   1. `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
      `AWS_SESSION_TOKEN` in the daemon's environment.
   2. The profile named by `agent.s3_profile`, then `AWS_PROFILE`, then
      `default`, in `~/.aws/credentials` and `~/.aws/config`, which
      `configparser` reads.
   3. That profile's `credential_process`, run through `core.run_bounded`
      with a 30-second timeout. Its JSON gives the keys and an
      `Expiration`. This covers AWS IAM Identity Center sign-in through
      `aws configure export-credentials --format process`.

   Long-lived access keys have no expiry and are read once. Credentials with
   an `Expiration` are kept in memory and refreshed 5 minutes before it, by
   running `credential_process` again or rereading the files, so no request
   goes out with credentials about to lapse. Session credentials in the
   environment cannot be refreshed, so the client says so in `relay show`
   and recommends a profile with `credential_process` instead.

   The region comes from `agent.s3_region`, then `AWS_REGION` or
   `AWS_DEFAULT_REGION`, then the profile, then `us-east-1`. With no
   credentials, the location's state is `off` with the reason in
   `project agent list` and `watch status`.
3. **Probe the store.** On first use and on each full pass, the job checks
   two things and records the result in the location's cache:
   - **Not public.** An unsigned `ListObjectsV2` on the prefix must fail. If
     it succeeds, anyone can list the relay, so the location is `off` with
     the reason `the bucket lets anyone list s3://…`.
   - **Conditional writes.** The job sends these requests in order on a
     fresh name, `<prefix>/<own-key-id>/probe/<random>` with 32 random hex
     digits. Together they exercise every precondition the leases in step 6
     rely on:

     | Request | Must answer |
     | --- | --- |
     | `PutObject` with `If-None-Match: *` | Success, with an ETag |
     | The same request again | `412` |
     | `PutObject` with `If-Match` on a wrong ETag | `412` |
     | `DeleteObject` with `If-Match` on a wrong ETag | `412` |
     | `DeleteObject` with `If-Match` on the first request's ETag | Success |

     A store that answers any of them otherwise would break leases, so the
     location is `off` with a reason that names the request, such as `this
     store does not honor If-Match on DeleteObject`. When a request fails
     the check, the job still deletes the probe object with its ETag. A
     `5xx` or a timeout is an error for that job rather than a result, and
     the next job probes again.

   Each probe picks a new name, so a probe interrupted before its deletion
   cannot make the next one fail. Each full pass also deletes every object
   under `<prefix>/<own-key-id>/probe/` that the listing shows as more than an
   hour old, with `If-Match` on its listed ETag, so an interrupted probe's
   object does not stay in the bucket.
4. **Poll.** Each S3 location's job runs when its outbox changes, as today,
   and otherwise every `agent.s3_poll` seconds. It lists
   `<prefix>/<own-key-id>/`, following continuation tokens. It also lists
   `<prefix>/<key-id>/` for each key this machine gave up, as the Git
   transport fetches those namespaces today. The listing gives each object's
   name, ETag, size, and last-modified time. The job keeps the ETags in the
   cache's `remote` map in place of commit SHAs.
5. **Open.** The job applies the existing limits. It examines at most
   `EXAMINE_LIMIT` new objects and opens at most `OPEN_LIMIT`. It rejects a
   name outside the layout in § 2.3, and rejects an object over 32 KiB
   without reading it, because the listing already gave its size. That
   replaces the Git transport's tree check. It reads the rest with
   `GetObject` and `If-Match: <etag>`, so it reads the version it listed.
   From there, opening, checking, and delivering are the code that runs
   today.
6. **Apply changes.** The outbox holds the same items as for Git, each with
   the version its lease expects. The transport sends one request per item,
   up to 8 at a time:

   | Change | Request | Lease failure |
   | --- | --- | --- |
   | Create | `PutObject` with `If-None-Match: *` | `412`: the object exists |
   | Update | `PutObject` with `If-Match: <old-etag>` | `412` |
   | Delete | `DeleteObject` with `If-Match: <old-etag>` | `412`; a `404` counts as deleted |

   A `412` marks the item stale, as a lease failure does today, so `rebase`
   gives it the version the next listing finds, or drops a stale deletion
   of another machine's record. A `409`, a `5xx`, or a timeout keeps the item
   queued with the error for the next job. A request that succeeds returns
   the new ETag, which the job records as the item's landed version.
7. **Report.** `project agent relay show` prints an S3 location by its
   identity (§ 2.3), with its region, where the credentials came from, such
   as `profile acme`, never the keys, and the probe's result.
8. **Tell you when credentials lapse.** When a location turns `off` because
   its credentials could not be read, refreshed, or used, the daemon posts
   one notice to that location's inbox (`../peers/readme.md`), which every
   one of your sessions on the location shows. The notice is local: it is
   written only to this machine's mirror, never to the relay. It names the
   source and the fix when one is known:

   ```text
   Relay off: the AWS sign-in for profile acme-relay expired. Run `aws sso login --profile acme-sso`.
   ```

   The causes it names are an expired sign-in, which `credential_process`
   reports, a `403` from S3, missing credentials, and a `credential_process`
   that times out. The daemon posts at most one notice per location, and
   replaces it when the reason changes. It withdraws the notice once a
   request with fresh credentials succeeds. Dismissing the notice hides it
   until the next time the location turns `off`. Outgoing records wait in
   the outbox and incoming ones in the bucket meanwhile, so nothing is lost.

### 2.2 Settings

| Setting | Type and meaning |
| --- | --- |
| `agent.relay` | Gains the form `s3://<bucket>/<prefix>`. The prefix may be empty, holds no `..`, and has no trailing `/`. |
| `agent.s3_region` | `str`, optional. The bucket's region. |
| `agent.s3_endpoint` | `str`, optional. An `https://` URL with no path, query, or user info, for a store that speaks S3, such as Cloudflare R2 or MinIO. With it, requests use path-style addressing. Without it, they go to `https://<bucket>.s3.<region>.amazonaws.com`. |
| `agent.s3_profile` | `str`, optional. The AWS profile to read credentials from. |
| `agent.s3_poll` | `int`, default 10 (kept in `relay.py`), minimum 2. Seconds between polls of an S3 location. |

Every one of these is read from every layer, like `agent.relay`, and none
holds a secret. `agent.s3_endpoint` and `agent.s3_profile` join `agent.relay`
in `config.DESTINATIONS`.

### 2.3 Objects

Each object's body is one `sealed.json`, as a ref's tree holds today, with
`Content-Type: application/json`. Its name is its ref's name with
`refs/projector/` replaced by the prefix and with the recipient's key ID
moved to the front:

| Ref | Object |
| --- | --- |
| `refs/projector/agents/<key-id>/<relay-id>` | `<prefix>/<key-id>/agents/<relay-id>` |
| `refs/projector/inbox/<key-id>/<relay-id>/<id>` | `<prefix>/<key-id>/inbox/<relay-id>/<id>` |
| `refs/projector/user/<key-id>/<id>` | `<prefix>/<key-id>/user/<id>` |
| `refs/projector/user-state/<key-id>/<id>` | `<prefix>/<key-id>/user-state/<id>` |

The transport maps between the two forms, so the job, the outbox, and the
cache keep using ref names. One more name has no ref:
`<prefix>/<key-id>/probe/<random>` is a probe object (§ 2.1 step 3), which
the job never reads or opens.

An S3 location's identity is its endpoint, bucket, and prefix together,
written as the path-style URL of the prefix, such as
`https://s3.amazonaws.com/acme-agents/relay`. The endpoint is
`agent.s3_endpoint` with its scheme and host in lowercase and a default port
removed. A location with no endpoint is on AWS, and its identity is written
with `https://s3.amazonaws.com`, because an AWS bucket name is unique across
regions. The region is not part of the identity: it picks the host that
requests go to and the scope they are signed for, so a wrong region makes
requests fail rather than reach another bucket. Every rule that names a
location uses the identity. The daemon groups registrations into jobs by it,
each registration records it as `location` in `agent.json`
(`../shared-relay/readme.md` § 2.1 step 2), each peers record carries it as
`location` (`../peers/readme.md` § 2.3), and `relay show` prints it. So two
checkouts that name `s3://team/relay` on two endpoints run two jobs with two
caches and two outboxes, and a record copied from one store into the other
is rejected as `location-mismatch`. `project config allow` still approves
each setting's value on its own, one per key and value
(`../../config-trust/readme.md` § 2.1 step 5), so an identity is used only
once every part of it that a tracked file sets is allowed.

A location's cache and outbox live at `relay/s3/<digest>.json` and
`outbox/s3/<digest>.json`. The digest is the first 16 hex digits of the
SHA-256 of the identity, and each file records the identity itself.

### 2.4 Components

| File | Change |
| --- | --- |
| New `src/projector/s3.py` | A small S3 client from the standard library. It signs requests with Signature Version 4 over `hmac` and `hashlib`, and sends them with `urllib.request` and the default `ssl` context, with a 30-second timeout. It finds credentials as § 2.1 step 2 describes. It provides `list_objects(prefix)`, which gives each object's name, ETag, size, and last-modified time, `get_object(name, if_match)`, `put_object(name, body, if_match=None, if_none_match=False)`, and `delete_object(name, if_match)`, and raises `PreconditionFailed`, `NotFound`, and `S3Error`. It reads at most 1 MiB of any response, refuses an XML body that holds a `<!DOCTYPE`, and parses listings with `xml.etree.ElementTree`. It refreshes credentials with an `Expiration` 5 minutes before it (§ 2.1 step 2). Its errors and its `repr` never include a secret key or a session token. |
| `src/projector/relay.py` | Add the `Transport` interface: `snapshot(prefixes)`, `read(ref, version)`, and `apply(items)`. Move today's fetch, `for-each-ref`, `ls-tree`, `cat-file`, `write_commit`, and push code into `GitTransport`, without changing its behavior. Add `S3Transport` with the mapping in § 2.3 and the requests in § 2.1 step 6, and the probe in § 2.1 step 3 with its sweep of old probe objects. `Job` takes a transport, and its examine step asks the transport whether an object is too large before it reads one. Group S3 registrations into jobs, and key their caches and outboxes, by the identity in § 2.3. |
| `src/projector/config.py` | Accept `s3://` in `relay_location`, refuse an `agent.s3_endpoint` with a path, query, or user info, and add `s3_identity(endpoint, bucket, prefix)`, which writes the identity in § 2.3. Add the four `agent.s3_*` settings, `agent.s3_poll` to `MINIMUMS` with 2, and `agent.s3_endpoint` and `agent.s3_profile` to `DESTINATIONS`. |
| `src/projector/watch.py` | Start an S3 location's job every `agent.s3_poll` seconds as well as when its outbox changes, and report its probe result and credential source in `watch status`. Post, replace, and withdraw the credential notice in § 2.1 step 8 as the location's state changes. |
| `src/projector/repo_inbox.py` (from `../peers/readme.md`) | Hold local notices from Projector itself, which are never relayed, and let `withdraw` remove one. |
| `src/projector/cli.py` | Show S3 locations in `project agent relay show` by their identity, as § 2.1 step 7 says. |
| `docs/cli.md` | Document the S3 relay. Give the IAM policy each person needs: `s3:ListBucket` on the bucket with an `s3:prefix` condition, and `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` on `<prefix>/*`. Recommend a lifecycle rule that expires objects after 8 days, as a backstop for the relay's own 7-day sweep. Say that a bucket with versioning keeps deleted records. |
| New `tests/test_s3.py`, `tests/test_relay.py` | The criteria in § 4, against a fake S3 server that the tests run on a local port. |

## 3. Why this design

- **Add a transport rather than branch inside the job.** The relay's hard
  parts are sealing, the outbox and its leases, the bounded checks before
  opening, and delivery. None of them depends on Git. A transport with three
  operations keeps them in one place for both stores, and moving the Git
  code behind it changes no behavior that the existing tests pin.
- **Map leases onto conditional requests.** A ref lease expects a commit,
  and an S3 conditional request expects an ETag. S3 accepts `If-None-Match`
  on `PutObject`, and `If-Match` on `PutObject` and `DeleteObject` in general
  purpose buckets, at no extra cost, so the outbox's rules carry over
  exactly.
- **Lay objects out by recipient key.** Everything a machine must read sits
  under one prefix, so a poll is one listing, or a few for a large backlog,
  however many people share the bucket.
- **Keep the standard library.** Projector's core takes no runtime
  dependencies, and `boto3` would add tens of megabytes. Signature Version 4
  is a published algorithm with AWS's test suite to check it against, and
  the relay needs only four operations.
- **Refuse a public or unconditional store.** A bucket anyone can list
  exposes the same metadata a public relay repository would, and a store
  that ignores conditions would let two machines overwrite each other's
  leases without either noticing. A store can honor `If-None-Match` on
  creation and still ignore `If-Match` on updates or deletions, and then a
  stale deletion removes another machine's renewed record. So the probe
  sends each conditional request the leases use, with a condition that must
  fail, before the relay trusts the store.
- **Poll first.** Polling every 10 seconds already cuts the Git relay's
  latency by a factor of 6 or more, with no infrastructure beyond the
  bucket. Event notifications need a queue or a server per subscriber, so
  they wait for a hosted relay (§ 6).
- **Rejected: running the `aws` CLI for each request.** It adds a process per
  request and an install the core does not require. Its credential support
  is still reachable through `credential_process`.

## 4. Acceptance criteria

The tests run a fake S3 server on a local port. It implements
`ListObjectsV2` with continuation and last-modified times, `GetObject`, `PutObject`, and
`DeleteObject` with `If-Match` and `If-None-Match`, and it checks each
request's Signature Version 4. A test can tell it to ignore one condition on
one request. They point `agent.s3_endpoint` at it, and run
the relay tests' two-machine setup with an S3 location.

| Exercise | Expected result, and the bug it catches |
| --- | --- |
| Sign the requests in cases from the AWS Signature Version 4 test suite, such as `get-vanilla`. | Each signature matches the suite's. Catches a wrong canonical request or signing key. |
| Register A on machine 1 and B on machine 2 with an S3 location, send from A to B, and reply. | Each machine lists the other's agent, the message and reply arrive, and the Git `origin` gains no ref. Catches a job that still uses Git. |
| Send from A while machine 2 polls every `agent.s3_poll` seconds. | B's line is appended within `agent.s3_poll` plus 2 seconds of the send. Catches a location polled only on watch passes. |
| Renew A's presence from two jobs at once. | One `PutObject` succeeds. The other gets `412`, is marked stale, and lands after the next listing with that version as its lease. Catches a lease the transport does not send. |
| Delete an object another machine already deleted. | The `404` counts as deleted, and the outbox empties. Catches a deletion retried forever. |
| List an object of 33 KiB, an object with a malformed name, and 300 new valid objects. | The first is rejected with no `GetObject`, the second is ignored, and the job examines 256 and opens 64. Catches a transport that reads unbounded input. |
| Stop the daemon after the probe's first `PutObject` and before its deletion, restart it, and run a full pass with the fake server's clock an hour later. | The probe after the restart passes on a new name, and the full pass deletes the first probe's object. Catches a probe name reused across runs, which turns a valid store `off` for good, and a leftover probe object nothing removes. |
| Make the fake server ignore `If-None-Match`, then `If-Match` on `PutObject` alone, then `If-Match` on `DeleteObject` alone, then let it answer an unsigned listing. | The location is `off` each time, with a reason that names the request it failed, no probe object is left, and nothing else is written. Catches a relay on a store that breaks creation, update, or deletion leases, or on a public bucket. |
| On each machine, register one session in a checkout whose `agent.s3_endpoint` names one fake server and one in a checkout whose endpoint names a second, all with `agent.relay = "s3://team/relay"`. Send a peers item from machine 1 through the first, and copy its object into the second server's bucket. Resolve a checkout with no endpoint. | Each machine runs two jobs with two cache files and two outbox files, and each server holds only the presence records of its own sessions. The item arrives through the first server, and its copy is rejected as `location-mismatch` on the second. The checkout with no endpoint resolves to the identity `https://s3.amazonaws.com/team/relay`. Catches a location keyed by its bucket and prefix alone, which sends one store's records to another. |
| Commit `agent.s3_endpoint` and `agent.s3_profile` to the default branch's `.projector.toml`, and allow neither. | No request is sent to the endpoint and no credentials are read for the profile, and `list` names `project config allow` for each. Catches a repository that points your relay at its own server, or signs with an identity you did not choose. |
| Give credentials through the environment, then a profile, then a `credential_process` whose credentials expire during the test. | Each signs correctly. The `credential_process` runs again 5 minutes before the expiry, and no request is signed with credentials past it. No output, log line, `watch status`, or process argument holds the secret key or the token. Catches a credential leak and a stale session. |
| Make `credential_process` report an expired sign-in, then fail again with another reason, then succeed. | One notice appears in every session on the location, naming the profile and `aws sso login`. It is replaced when the reason changes, never duplicated, and withdrawn after the first successful request. Records queued meanwhile push afterward. Catches a lapse nobody is told about, a stack of notices, and one that outlives the fix. |
| Make `credential_process` hang, and make the server hang on a request. | The process is killed after 30 seconds and the request times out, each with the error in `watch status`. A Codex event for an unrelated session still arrives within 2 seconds. Catches an S3 call that stalls the daemon's loop. |
| Answer a listing with a `<!DOCTYPE` body, and with a body over 1 MiB. | Each is an error for that job, and nothing is parsed. Catches an XML entity attack and unbounded reads. |
| Run the existing Git relay tests unchanged. | They pass. Catches a behavior change from moving the Git code behind `GitTransport`. |
| Read every object in the fake bucket after an exchange. | No object holds a name, summary, or text in plain form. Catches plaintext reaching the store. |

Each test must fail when the fault it names is introduced, as
`skills/testing.md` asks. After merge, relay between two real machines
through an S3 bucket with the IAM policy in `docs/cli.md`, and record the
observed delivery time in this plan.

## 5. Cost

- **Requests.** One `ListObjectsV2` per location per poll on each machine:
  8,640 a day at the default 10 seconds, about $0.04 a day at S3 Standard's
  listed $0.005 per 1,000 in `us-east-1`. Each record costs one `PutObject`
  to write, one `GetObject` per reader, and one `DeleteObject`.
- **Storage.** Records are at most about 22 KiB and live at most 7 days, so
  storage costs nothing worth measuring.
- **Daemon.** S3 calls run on the relay's worker pool, never on the loop that
  scans sessions, and each is bounded by its timeout. Signing costs a few
  HMAC operations per request.
- **Latency.** Sending waits about a second for the daemon to see the outbox
  plus one request, and receiving waits up to one poll, so about 10 seconds
  at worst by default, against 60 for the Git relay.

## 6. Open questions

- **A hosted relay.** Deferred. Projector could run an S3-compatible relay as
  a paid service, with credentials scoped to each key's prefix and
  `agent.s3_endpoint` pointing at it. Nothing in this plan stands in its way.
  A hosted relay could also push notifications, which § 3 leaves for later.
- **Instance and container credentials.** Deferred. The S3 client is part of
  the watch daemon, the one process that makes S3 requests, and the daemon
  runs on people's own computers. Those have no EC2 instance metadata
  endpoint and no ECS or EKS container credentials, so the client reads
  neither. Both could follow if people run the daemon on AWS, such as agents
  in cloud workspaces on EC2, where the machine's own role could reach the
  bucket with no keys to set up.

## 7. Decisions

- **Refresh early, and tell you when credentials lapse.** Decided by the user
  on 2026-11-17. Credentials with an expiry refresh 5 minutes before it
  (§ 2.1 step 2). When credentials stop working, the daemon posts one local
  notice to the location's inbox naming the fix, and withdraws it once
  requests succeed again (§ 2.1 step 8).

## 8. What building settled

- **The transport has four operations.** `Transport` in `relay.py` has
  `snapshot(prefixes)`, `check(ref, version)`, `read(handle)`, and
  `apply(items)`. `check` is the examine step's question before a read: Git
  answers it from the commit's tree and S3 from the listing's size, and a
  record it refuses never counts toward `OPEN_LIMIT`, as a refused tree
  never did. `apply` answers each item's outcome: whether it landed, what
  the store said, whether its lease failed, and the version it left.
- **`GitTransport` moves the tracking refs itself.** It runs the
  `update-ref` that follows a push inside `apply`, so the job's bookkeeping
  after a push names no Git command. `Job.open` still reads a Git blob, for
  the tests that open a record on the bare repository.
- **The client sends with `http.client`, not `urllib.request`.** It follows
  no redirect, so a signed request never goes to a host it did not name,
  and a timer bounds each whole request at 30 seconds, since a socket's
  timeout bounds only each read and a store could send a byte at a time.
- **A bucket whose name holds a dot goes path-style on AWS,** to
  `s3.<region>.amazonaws.com/<bucket>`, because no certificate for a
  virtual host matches a dotted name.
- **A listing stops at 50 pages,** 50,000 objects under one key's prefix,
  and more is an error for that job rather than a backlog to page through.
- **The client honors `AWS_SHARED_CREDENTIALS_FILE` and `AWS_CONFIG_FILE`,**
  as the AWS tools do, and rereads a profile's long-lived keys when either
  file changes.
- **An expired sign-in is a `credential_process` that fails saying so.**
  Its last line of standard error names an expiry, a failed refresh, or an
  SSO session, and the fix is `aws sso login` for the profile its command
  names with `--profile`, else for the profile itself.
- **The signer is checked against AWS's own cases,** `get-vanilla` and two
  query cases from the Signature Version 4 test suite, and the four S3
  examples in AWS's documentation, each with the signature AWS publishes.
- **The parser refuses a document type, not a scan of the bytes.**
  `parse_xml` builds the tree with an expat parser whose document type and
  entity declaration handlers raise, so it refuses the declaration after
  decoding the body. A UTF-16 body, whose bytes never spell `<!DOCTYPE`,
  is refused as a UTF-8 one is.
- **The 30 seconds bound the host's lookup too.** `http.client` resolves
  the host before any socket exists for the timer to shut, so the client
  opens the socket itself: it looks the host up on a daemon thread, stops
  waiting at the request's deadline, and connects within the time left.
  The connection keeps the host's name for TLS and the signed `Host`
  header. A lookup that never returns leaves its thread behind until the
  resolver gives up.
- **Keys a refresh gives are judged by the clock after it.** A
  `credential_process` can take most of its 30 seconds, so the provider
  reads the clock again once it answers. Keys that have expired by then,
  or that expire within the 5-minute refresh margin, are an `expired`
  error rather than signing anything: the source has no fresher keys, so
  each request would only run it again.
- **Bucket names and prefixes are lowercase.** The prefix takes lowercase
  letters, digits, `.`, `_`, and `-` in `/`-separated segments, so every
  rule that compares locations without case, as the daemon's jobs and the
  outbox files do, compares S3 identities exactly.
- **A registration records what an S3 location needs.** `agent.json` holds
  the location's settings as `s3`, with `bucket`, `prefix`, `endpoint`,
  `region`, `profile`, and `poll`, and the `store` its files are named by,
  `s3/<digest>`, which the panel reads to find the location's inbox
  mirror, since it cannot compute a SHA-256. A waiting registration
  records each setting and value it waits for as `allow`, and the daemon
  admits it once every one is allowed. A registration from before records
  none, and waits for `agent.relay` alone, as it did.
- **The daemon keeps one client per S3 location,** made from the settings
  of the location's newest live registration, so its credentials are read
  once and refreshed in place. An unregistered session's mark keeps its
  `s3` settings, so its deletions still reach the bucket. So does the
  outbox of a location a session moves away from: the deletions it queues
  keep the old registration's `s3`, and while they wait and no
  registration names the location, the daemon makes its client from them.
  That job polls nothing, and while the store is off it holds every
  change back, as any S3 location's job does.
- **An S3 location that is off sends nothing but its probe.** While the
  probe refuses the store or the credentials lapse, the job lists nothing
  and applies nothing, and the outbox keeps every change, deletions
  included. It turns `off` with the reason, which `list`, `watch status`,
  and `relay show` print. A lapse is tried again on every job, and a
  refused store on the next full pass.
- **A store that answers a probe request with another `4xx` is refused,**
  with the status in the reason, such as `this store answers 400 to
  If-Match on PutObject rather than 412`, and one that refuses the last
  deletion under the object's own ETag is refused too. A `403` there is a
  lapse of the credentials, not a property of the store.
- **A record that changed between the listing and its read waits.** A
  `GetObject` that answers `412` or `404` leaves the record unexamined, and
  the next poll lists its new version, rather than rejecting a record a
  peer replaced.
- **`send` reminds no agent about a public relay on an S3 location,** since
  the probe refuses a bucket that anyone can list.
- **`relay show --json` gives `allow` as a list of commands,** one for each
  value the location waits for.
- **The lease test changes the object between the listing and the
  renewal,** as a second job's renewal would, since the daemon never runs
  two jobs for one location at once.
- **The credential notice is a mirror entry with the role `local`.** It
  lives in the location's inbox mirror beside the relayed entries, with
  `notice.json` holding its ID, its text, and whether you dismissed it. It
  shows as `Projector: Relay off: …` in `project inbox list`, the panel,
  and the console, `inbox show` names it as from Projector on this machine,
  and it takes a dismissal and nothing else: a response or a snooze is
  refused, so no state record of it is ever queued, and neither the panel
  nor the console offers Snooze. The job posts and withdraws it on its worker thread, so a busy
  mirror never holds up the daemon's loop. `notice.json` records a text
  only once the mirror holds it, so a notice that a busy mirror kept out
  posts on the next job. It records the notice's ID before the mirror
  shows it, and a withdrawal removes every `local` entry, so a post that
  stops between the two writes leaves nothing a withdrawal misses.
  Dismissing the notice reads no relay key, in the console or with
  `project inbox dismiss`, since nothing about it is sealed.
- **A notice is withdrawn on the first signed request that succeeds,** a
  listing, the probe's first write, or a change applied, even while the
  probe refuses the store, since the notice is about the credentials.

## 9. Outcome

Shipped as four layers: the transport, the S3 client, the S3 relay, and the
credential notice. The criteria in § 4 have these tests, each of which
failed with its fault introduced:

| Criterion | Evidence |
| --- | --- |
| Signatures from AWS's test suite | `tests.test_s3.SignatureTests.test_each_signature_matches_the_aws_signature_version_4_test_suite` |
| Agents and messages through a bucket, with no Git ref | `S3RelayTests.test_an_s3_location_carries_agents_and_messages_and_no_ref_reaches_git` |
| Delivery within `agent.s3_poll` plus 2 seconds | `S3RelayTests.test_a_message_arrives_within_the_poll_interval_with_no_watch_pass` |
| A renewal's lease | `S3RelayTests.test_a_renewal_whose_object_another_job_changed_gets_412_and_lands_on_the_next_listing` |
| A deletion already done | `S3RelayTests.test_a_deletion_of_an_object_already_gone_counts_as_deleted` |
| A large object, a malformed name, and a flood | `S3RelayTests.test_a_large_or_misnamed_object_and_a_flood_are_bounded_before_any_read` |
| An interrupted probe and its sweep | `S3RelayTests.test_a_probe_interrupted_by_a_restart_leaves_no_object_after_the_next_full_pass` |
| Stores that ignore a condition, and a public bucket | `S3RelayTests.test_a_store_that_ignores_a_condition_or_lets_anyone_list_turns_the_location_off` |
| One bucket and prefix on two endpoints | `S3RelayTests.test_one_bucket_and_prefix_on_two_endpoints_are_two_locations` |
| A committed endpoint and profile | `S3RelayTests.test_an_endpoint_and_a_profile_a_repository_commits_wait_until_you_allow_each`, `test_a_registration_written_while_admission_checks_another_waits_for_its_own_values` |
| Credentials from the environment, a profile, and a process, refreshed early, never leaked | `tests.test_s3.CredentialTests`, `S3RelayTests.test_no_output_log_status_or_process_argument_holds_the_secret_key_or_the_token` |
| The credential notice | `LapseNoticeTests.test_a_lapse_shows_one_notice_in_every_session_replaced_as_its_reason_changes_until_a_request_succeeds`, `test_a_dismissed_notice_stays_hidden_while_the_relay_is_off_and_shows_again_when_it_next_turns_off`, `test_a_notice_a_busy_inbox_kept_out_shows_on_the_next_job` |
| A hung process or store | `tests.test_s3.CredentialTests.test_a_credential_process_that_hangs_is_killed_at_its_timeout`, `OperationTests.test_a_request_that_hangs_or_trickles_is_stopped_at_its_timeout`, `test_a_name_lookup_that_never_returns_is_stopped_at_the_timeout`, `S3RelayTests.test_a_hung_credential_process_or_store_is_stopped_and_stalls_no_codex_event` |
| A `<!DOCTYPE` listing and one over 1 MiB | `OperationTests.test_a_response_that_declares_a_document_type_is_refused_in_any_encoding`, `test_a_listing_that_holds_more_than_1_mib_is_not_parsed`, and the last part of the hung-store test |
| The Git relay tests unchanged | `tests.test_relay` before the S3 layers, which passed with no change to a test |
| No plaintext in the bucket | `S3RelayTests.test_no_object_in_the_bucket_holds_a_name_summary_or_text_in_plain_form` |

The relay between two real machines through an S3 bucket, with the IAM
policy in `docs/cli.md`, waits until after the merge, for the user to run.
Its observed delivery time goes here once it runs.

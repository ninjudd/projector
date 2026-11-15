---
status: ready
priority: now
---

# Put a branch's iOS app on a device with `project ios`

`project ios` builds any branch or pull request of a repository's iOS app in
a worktree of its own, puts it on the person's iPhone, iPad, or Mac, and
brings that device's crash reports and log back, so an agent can hand over a
device test with one button and the person can run the same command
anywhere.

## 1. Problem

Projector's inbox lets an agent ask the person to test a change on their
phone. The agent posts a `request` with a start action, which is the one
thing the agent does when the person presses start, such as installing a
build. The person's press is the consent to install, because an install
replaces whatever build they had on the device. Projector supplies the
request but nothing to fill it, so each iOS repository has to invent the
build, the install, and the way back for logs and crashes.

The steps are easy to get wrong by hand:

- **A build in the checkout disturbs it.** To build a pull request with
  `xcodebuild`, you check out its branch, which moves the person's or the
  agent's working copy and carries any uncommitted change along with it.
- **The right device is hard to pick.** `xcrun devicectl` lists every paired
  device, including ones whose connection is `unavailable`. With an iPhone
  and an iPad both plugged in, a bare install has to guess.
- **A locked phone refuses the launch.** The install succeeds, but the
  launch fails with a lock error, which is the usual state of a phone by the
  time a build ends.
- **An agent on another machine cannot reach the device.** A start action
  that installs only works when the agent runs on the Mac the device is
  plugged into. When the person is on a laptop elsewhere, they need a command
  they can run there.
- **Crashes and logs stay on the device.** A crash report is a two-part JSON
  `.ips` file in the device's crash log domain. A device's log needs
  `sudo log collect`, which writes an archive of the whole device's log.
  Neither is something a person does by hand in the middle of a test.

One repository solved this with a 500-line shell script that has run every
device test there for the past week. Each of its commands is general except for the
names of the app's Xcode project, schemes, and log subsystem, so other
repositories that use Projector for iOS work would gain from having it in the
`project` command.

## 2. Solution

Add a `project ios` command group, ported from that script to a Python module
on the standard library. `project ios build <branch-or-number>` fetches the
branch, or a pull request's head, checks it out detached in one worktree that
Projector keeps for the repository, builds the app with `xcodebuild` into one
shared DerivedData folder, keeps a copy of the built app for that branch, and
installs and opens it on the one connected device. `install` puts a kept
build on a device again, `devices` lists paired devices, `crashes` copies and
summarizes the app's newest crash reports, `logs` writes the app's recent log
lines to a text file, and `cleanup` removes the worktree and builds. The app
is found by convention: the scheme is `<App> Development` if the project has
one, and otherwise `<App>`, where `<App>` defaults to the name of the Xcode
project. The `implement` skill's device test uses it: the agent builds ahead
with `--no-install`, then posts a request whose start action is
`project ios install <branch>` when it can reach a device, and whose steps
always include the `project ios build <branch>` command the person can run
on their own Mac.

### 2.1 Commands

| Command | What it does |
| --- | --- |
| `project ios build <branch-or-number>` | Fetches, builds, keeps the build, then installs and opens it. `--no-install` stops after the build. |
| `project ios install [<branch-or-number>]` | Installs and opens the kept build of that branch, or with no argument the newest kept build. |
| `project ios devices [--json]` | Lists paired iOS devices with each one's name, UDID, kind, and connection state, and whether it can take an install. |
| `project ios crashes` | Copies the app's three newest crash reports to the output folder and prints a summary of each. |
| `project ios logs [--last <span>]` | Writes the app's log lines from the last 5 minutes, or the span, such as `20m`, to a new text file in the output folder. |
| `project ios cleanup` | Removes the worktree, DerivedData, and kept builds, and prints how much space that freed. |

Every command except `devices` and `cleanup` takes these options:

| Option | Effect |
| --- | --- |
| `--app <App>` | Builds or reads that app instead of the default one. |
| `--device <name-or-UDID>` | Uses that paired device. |
| `--iphone`, `--ipad` | Uses the one connected iPhone, or the one connected iPad. |
| `--mac` | Builds the app as Designed for iPad and opens it on this Mac, quitting the copy that is running. `crashes` and `logs` read this Mac instead of a device. |

`build` also takes `--verbose`, which prints every line `xcodebuild` writes.
`logs` also takes `--predicate <predicate>`, which replaces the filter in
§ 2.4 for an app whose log does not follow it.

`--device`, `--iphone`, `--ipad`, and `--mac` exclude each other. A number is
a pull request, and anything else is a branch.

### 2.2 The flow of a build

1. **Check the tools.** Find `xcrun` and `xcodebuild` on `PATH`, or exit 69
   with `project ios needs Xcode's command-line tools on macOS`.
2. **Pick the device first,** unless `--no-install` or `--mac` is given, so a
   build never runs for a device that cannot take it. Read
   `xcrun devicectl list devices --json-output <file>` and keep the paired,
   physical iOS devices. `--device` must match exactly one by name or UDID.
   `--iphone` and `--ipad` keep only that kind. Without either, prefer a
   device whose tunnel is `connected`, and then one whose tunnel is not
   `unavailable`. Refuse none or more than one with the list of paired
   devices and what to do: unlock the device, plug it in, and tap Trust, or
   pass `--device`.
3. **Take the repository's lock.** Hold an `fcntl.flock` on the repository's
   `lock` file for the rest of the run. A second run waits, printing once
   which process holds the lock and for what. The kernel drops the lock when
   the process ends, however it ends.
4. **Fetch the head.** For a pull request, read it from the GitHub API and
   fetch its branch when the pull request comes from this repository, or
   `refs/pull/<n>/head` when it comes from a fork. For a branch, fetch
   `refs/heads/<branch>`. Both use `review.fetch`, which writes no
   `FETCH_HEAD`.
5. **Move the worktree.** Create the worktree detached at the fetched commit,
   or check it out there with `--force`. The worktree belongs to
   Projector, so a build discards any change made in it.
6. **Resolve the app.** Find the Xcode project in the worktree (§ 2.3), pick
   the scheme, and read the app target's `FULL_PRODUCT_NAME`,
   `PRODUCT_BUNDLE_IDENTIFIER`, and `TARGET_BUILD_DIR` from
   `xcodebuild -showBuildSettings -json`.
7. **Build.** Run `xcodebuild build -scheme <scheme> -destination
   generic/platform=iOS -derivedDataPath <DerivedData>
   -allowProvisioningUpdates`. With `--mac`, the destination is
   `platform=macOS,arch=arm64,variant=Designed for iPad`. Every line goes to
   `build.log`. On a terminal, the last ten lines scroll in gray and clear
   when the build ends. Elsewhere, such as an agent's shell, it prints only
   the line that starts the build and the line that ends it, unless
   `--verbose` is given. A failed build prints the log's `error:` lines,
   without repeats, up to 20, or its last 30 lines when it has none, and the
   log's path.
8. **Keep the build.** Clone the built app into `builds/<App>/<key>/`, where
   `<key>` names the branch or pull request, and write `build.json` beside
   it. The clone uses `cp -c`, an APFS copy-on-write clone, so it costs no
   time or space until a later build changes the original.
9. **Install and open.** Run `xcrun devicectl device install app`, then
   `device process launch --terminate-existing <bundle ID>`. When the launch
   fails because the device is locked, print
   `Unlock your iPhone to open it. Waiting…` once and retry every 2 seconds
   for up to 5 minutes. Any other launch error ends the run. With `--mac`,
   quit the running copy through `osascript`, wrap the app in a folder with a
   `Wrapper/` directory and a `WrappedBundle` link, which is how macOS opens
   an iOS app, and `open` the folder.

`install` runs steps 1 to 3, then step 9 on the kept build. A target that has
no kept build fails with the `project ios build` command that makes one.

### 2.3 How the app is found

The repository needs no configuration when it follows the convention.

| Question | Answer by convention | Setting that overrides it |
| --- | --- | --- |
| Which Xcode project? | The one `.xcworkspace`, or with none the one `.xcodeproj`, in the worktree. Projector skips `.git`, `Pods`, `node_modules`, `build`, `.build`, and anything inside an `.xcodeproj` or `.xcworkspace`. | `ios.project`, a path relative to the repository's root. |
| Which app? | The Xcode project's name, so `MyApp.xcodeproj` gives `MyApp`. | `--app`, or the setting `ios.app`. |
| Which scheme? | `<App> Development` when the project has it, and otherwise `<App>`. With neither, the command fails and lists the project's schemes. | None. Pass the scheme's name as `--app` instead: a scheme named `MyApp Beta` is the app `MyApp Beta`. |
| Which log lines? | § 2.4. | `--predicate`. |
| Where do crashes and logs go? | `~/Desktop`. | `ios.output`. |

`ios.app`, `ios.project`, and `ios.output` are new string keys in
`config.SETTINGS`, so `project config set` sets them in a repository's or the
user's `.projector.toml`. `ios.output` expands a leading `~`.

### 2.4 Crashes and logs

`crashes` lists the device's `systemCrashLogs` domain with
`xcrun devicectl device info files`, keeps the `.ips` files named
`<product>-*`, and copies the three newest to the output folder under their
own names, skipping one already there. With `--mac`, it reads
`~/Library/Logs/DiagnosticReports` and its `Retired` folder. For each report
it prints the time, app version and build, OS version, the exception type
and signal or the termination namespace and code with its reasons, and the
first 20 frames of the thread that crashed. A watchdog kill crashes no
thread, so the summary shows the main thread, which is the one that kept the
app from answering.

`logs` writes to `<product> log <YYYY-MM-DD> at <HH.MM.SS>.txt` in the output
folder. When that name exists, it adds ` 2`, ` 3`, and so on, claiming each
name with an exclusive create so a run in the same second never writes over
another. On a device it runs `sudo log collect --device-udid <UDID> --last
<span>` into a temporary archive, then `sudo log show` with the filter, and
deletes the archive, which holds the whole device's log. It says first that
sudo asks for a password and that collecting can take a few minutes. With
`--mac` it runs `log show --last <span>` on this Mac, with no password. A
failure or Ctrl-C deletes the claimed file and the archive.

The filter keeps a line when either of these holds:

- **The subsystem belongs to the app.** The subsystem equals the bundle ID,
  starts with the bundle ID and a dot, or equals the bundle ID without its
  last part when that still leaves at least three parts. An app logs under a
  subsystem such as `com.example.myapp`, while its Development build's
  bundle ID is often `com.example.myapp.dev`, and this rule matches both. It
  never matches a two-part vendor prefix such as `com.example`, which other
  apps from the same vendor log under: for the bundle ID
  `com.example.myapp`, dropping the last part leaves two parts, so only the
  bundle ID and its dotted children match.
- **The app's process wrote a fault.** A Swift `fatalError` message reaches
  the log as a fault from the process, not from the app's subsystem.

### 2.5 State

Each repository's state lives under
`$XDG_STATE_HOME/projector/ios/<owner>/<repo>/`, or
`~/.local/state/projector/ios/<owner>/<repo>/` without `XDG_STATE_HOME`.
`<owner>/<repo>` comes from `review.origin_repo`.

| Path | Holds |
| --- | --- |
| `lock` | The file a run holds its lock on, with the holder's process ID and command. |
| `worktree/` | The detached worktree, shared by every branch and app. |
| `DerivedData/` | The shared build folder, so moving between branches rebuilds only what changed. |
| `build.log` | The newest build's whole log. |
| `builds/<App>/<key>/` | The kept app for one branch or pull request, and its `build.json`. |

`build.json` records one kept build:

| Field | Value |
| --- | --- |
| `app` | The app's name, such as `MyApp`. |
| `scheme` | The scheme it was built with. |
| `target` | The branch or pull request number as the person gave it. |
| `branch` | The branch it was built from. |
| `sha` | The commit it was built from. |
| `destination` | `device` or `mac`. |
| `product` | The `.app` folder's name. |
| `bundle_id` | The app's bundle ID. |
| `built_at` | When the build ended, as an ISO 8601 time. |

### 2.6 The device test in the skills

The `implement` skill's hand-over already keeps a pull request a draft while
the person tests it on a device. A paragraph there says how to prepare that
test in a repository where `project ios` finds an Xcode project:

1. Push the branch, so the person's own run of `build` fetches the same
   commit.
2. Run `project ios build <number> --no-install`, in the background when the
   host allows it, and post nothing until it succeeds.
3. Run `project ios devices --json`. When a device can take an install, give
   the request the start action `Install #<number>'s build of <App> on your
   <device> and open it`, and on `STARTED` run
   `project ios install <number>`, then `ready`, or `block` with the reason
   its error gives, such as a locked phone.
4. Whether or not there is a start action, the request's steps give the
   command `project ios build <number>` and say to run it in any checkout of
   the repository on the Mac the device is plugged into. When no device can
   take an install, the agent is on another machine, so that command is the
   only way to install, and the request has no start action.
5. When the person reports a crash or odd behavior, run `project ios crashes`
   when the agent can reach the device. Otherwise, and for any device log,
   which needs the person's password, ask in a request for the person to run
   `project ios crashes` or `project ios logs` and share the file it names.

The `work` skill's line about a change to check on the person's phone points
at that paragraph.

### 2.7 Changes by file

| File | Change |
| --- | --- |
| `src/projector/ios.py` | New. Device choice, the lock, fetch and worktree, app resolution, the build and its output window, kept builds, install and open, the Mac wrapper, crash summaries, logs, and cleanup. |
| `src/projector/cli.py` | `add_ios_parser` and `run_ios`, beside the other command groups. |
| `src/projector/config.py` | `ios.app`, `ios.project`, and `ios.output` in `SETTINGS`. |
| `src/projector/review.py` | Moves the choice between a pull request's branch and `refs/pull/<n>/head` out of `fetch_head` into a function that `fetch_head` and `ios.py` both call. |
| `docs/cli.md` | A new section, "Put an iOS app on a device", with the commands, the convention, the settings, and the state. |
| `skills/implement/SKILL.md` | The device test paragraph of § 2.6. |
| `skills/work/SKILL.md` | A pointer from the phone test line to that paragraph. |
| `tests/test_ios.py` | New. The tests of § 4. |

## 3. Why this design

- **In `project`, not a separate tool.** Its reason to exist is the inbox's
  device test, which Projector's skills already describe and leave with
  nothing to run. A separate tool would make every iOS repository install and
  find it, and the skills could not name it.
- **`ios`, not `xcode`.** Every command acts on an iOS app, including
  `--mac`, which runs the iOS build on a Mac. `xcode` names the tool rather
  than the job, and suggests general project management that the group does
  not do.
- **A convention before configuration.** The schemes Xcode makes are named
  for the app, and a repository with a Development scheme for a separate
  bundle ID usually names it `<App> Development`. Three settings cover the
  rest, and a repository that follows the convention sets none. A table of
  per-app settings would need a new kind of key in `config.SETTINGS`, which
  holds only flat, typed keys. `--app` with a scheme's full name covers every
  case such a table would.
- **One worktree and one DerivedData per repository.** Xcode's incremental
  build is what makes a second branch take a minute instead of ten. A
  worktree per branch would rebuild from nothing each time. The cost is that
  builds in one repository run one at a time, which the lock enforces.
- **Kept builds, installed by target.** The script this ports from installed
  "the last build". With one shared build, an agent that builds a second
  pull request while the first one's request waits makes the first request's
  start button install the wrong change. Keeping each target's app and
  naming the target in `install` makes the start action install what the
  request describes.
- **The lock waits instead of failing.** Several agents work in one
  repository at once, and a refusal makes each of them retry by hand. A wait
  costs nothing, because the builds could not run together anyway.
- **Quiet output outside a terminal.** A build writes thousands of lines.
  Passed through to an agent's shell, they fill its context and hide the
  error. The log keeps every line, and a failure prints the lines that
  matter.
- **Device choice before the build.** A build takes minutes, and a device
  that cannot take the install is something the person must fix first.
  Choosing first turns a failure at the end into a prompt at the start.
- **The Desktop as the default output.** A crash report or a log file on the
  Desktop is where a person looks for one, like a screenshot, and where
  iCloud Desktop sync carries it to another Mac. `ios.output` moves it.
- **No new permission rule.** Every `project` command is allowed. `install`
  replaces a build on the person's device, but the skills run it only on
  `STARTED`, which is the person's consent, so an ask rule would only add a
  second prompt to the press.
- **Standard library only.** The script's embedded Python already did the
  JSON work. `plistlib` replaces `PlistBuddy`, and `fcntl.flock` replaces
  `lockf(1)`. The command checks for `xcrun` rather than for macOS, so the
  tests run on Linux with stand-ins for Apple's tools.

## 4. Acceptance criteria

The tests run in temporary Git repositories with stand-in `xcrun`,
`xcodebuild`, `gh`, and `osascript` scripts on `PATH`. The stand-ins record
their arguments and write fixture output, including a fake `.app` folder with
an `Info.plist`. Every fixture is synthetic.

| Do | Expect | Catches |
| --- | --- | --- |
| `build 12` for a pull request from a fork | The fetch names `refs/pull/12/head`, and the worktree is at the pull request's head. | A fork's pull request failing because its branch is not on `origin`. |
| `build` with an iPhone and an iPad both connected | It fails before `xcodebuild` runs and lists both devices. | An install on whichever device the list names first. |
| `build --ipad` with both connected | It installs on the iPad's UDID. | `--ipad` ignored when one device is connected and another is reachable. |
| `build` with only an `unavailable` device | It fails before the build and says how to reach the device. | A ten-minute build for a device that cannot take it. |
| `build a`, then `build b --no-install`, then `install a` | `install` installs `a`'s kept app, not `b`'s. | A start action installing the change another agent built later. |
| `install c` with no kept build of `c` | It fails and prints `project ios build c`. | An install of some other build. |
| A project with schemes `MyApp`, `MyApp Development` | It builds `MyApp Development`. | The plain scheme winning, which can carry the release bundle ID. |
| A project with neither scheme | It fails and lists the schemes. | A build of the wrong scheme. |
| Two `.xcodeproj` folders and no `ios.project` | It fails and names both. | A build of the wrong project. |
| Two runs at once | The second waits, prints the holder once, then runs. A killed holder releases the lock. | Two builds moving one worktree, or a lock left behind by a crash. |
| A successful build of 5,000 log lines with output not a terminal | Exactly 2 lines printed, and `build.log` has all 5,000. | An agent's context filled with build output. |
| A failing build | Its `error:` lines without repeats, at most 20, and the log path. Exit status of `xcodebuild`. | A failure that shows only the last line. |
| A launch that fails as locked twice, then succeeds | One `Waiting…` line, then `Opened it.` | A locked phone ending the run, or one line per retry. |
| A launch that fails for another reason | It exits at once with the error. | Five minutes of waiting on an error that never clears. |
| `crashes` on a synthetic `.ips` with an exception | The summary names the exception and the crashed thread's frames. | A summary of the wrong thread. |
| `crashes` on a synthetic watchdog report with no crashed thread | The summary shows the termination code in hex and the main thread. | An empty summary for the most common hang. |
| `logs` twice in the same second | Two files, the second with ` 2`. | One run writing over another's log. |
| `logs` interrupted after it claimed its file | No file left behind, and no archive. | An empty log file, or a whole device's log left on disk. |
| `logs` for bundle ID `com.example.myapp.dev` | The predicate matches subsystems `com.example.myapp`, `com.example.myapp.dev`, and `com.example.myapp.dev.net`, and not `com.example` or `com.example.myappx`. | The default filter missing the app's own lines, or keeping the vendor's other apps. |
| `logs` for bundle ID `com.example.myapp` | The predicate matches `com.example.myapp` and `com.example.myapp.net`, and not `com.example`. | Dropping the last part of a three-part bundle ID down to the vendor prefix. |
| `cleanup`, then `cleanup` | The first removes the folder and prunes the worktree. The second prints `Nothing to clean up.` | A stale worktree entry left in the repository. |
| Any command with no `xcrun` on `PATH` | Exit 69 and the message of § 2.2 step 1. | A traceback on Linux. |

On a Mac with a paired iPhone, `project ios build <number>` for an open pull
request of an iOS repository installs the app and opens it, and
`project ios crashes` prints the newest report. The pull request's
`## Testing` section records that run.

## 5. Cost

The build is the only hot path, and Xcode's incremental build sets its cost.
Sharing one DerivedData keeps a branch switch to the files that differ.
Projector adds one `devicectl` listing, one `-showBuildSettings` call of a
few seconds, and one APFS clone of the app, which takes milliseconds and no
space until a later build changes the original. `cleanup` reports the space
the folder held, because DerivedData for an app can reach several gigabytes.

## 6. Rollout

1. Open the command, its settings, `docs/cli.md`, and its tests as the first
   layer of a stack.
2. Open the skill paragraphs of § 2.6 as the second layer, because they name
   commands the first layer adds.

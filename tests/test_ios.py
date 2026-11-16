"""The `project ios` commands, against a real Git remote and stand-ins for Apple's tools.

Git runs for real: a bare origin holds `trunk`, branches, and GitHub's `refs/pull/<n>/head`, and each
checkout is a clone of it. `xcrun`, `xcodebuild`, `osascript`, `open`, `log`, and `sudo` are one stand-in
script on `PATH`, which answers from a scenario file and records every call, so the tests run on a Linux
runner with no Xcode. The stand-in `xcodebuild` writes an app holding the commit it was built from, and the
stand-in install records that commit, so a test can see which build reached the device. GitHub is a fake
in place of `review.run_gh`.
"""

from __future__ import annotations

import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

from projector import cli, ios, review

REPO = "acme/app"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}
SRC = Path(__file__).resolve().parents[1] / "src"
STANDIN = r'''
import json, os, shutil, subprocess, sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
scenario_path = Path(os.environ["IOS_SCENARIO"])
scenario = json.loads(scenario_path.read_text())


def record(*entry):
    with open(os.environ["IOS_CALLS"], "a") as calls:
        calls.write(json.dumps(list(entry)) + "\n")


def option(flag):
    return args[args.index(flag) + 1]


def pop(key, default):
    values = scenario.get(key) or []
    if not values:
        return default
    value = values.pop(0)
    scenario_path.write_text(json.dumps(scenario))
    return value


record(name, *args)
if name == "sudo":
    os.execvp(args[0], args)
if name == "xcrun":
    if args[:3] == ["devicectl", "list", "devices"]:
        Path(option("--json-output")).write_text(json.dumps({"result": {"devices": scenario["devices"]}}))
    elif args[:4] == ["devicectl", "device", "install", "app"]:
        record("installed", option("--device"), (Path(args[-1]) / "commit.txt").read_text().strip())
    elif args[:4] == ["devicectl", "device", "process", "launch"]:
        result = pop("launches", "ok")
        if result != "ok":
            sys.exit(result)
    elif args[:4] == ["devicectl", "device", "info", "files"]:
        Path(option("--json-output")).write_text(json.dumps({"result": {"files": scenario["crash_files"]}}))
    elif args[:4] == ["devicectl", "device", "copy", "from"]:
        shutil.copy(Path(scenario["crash_dir"]) / option("--source"), option("--destination"))
    sys.exit(0)
if name == "xcodebuild":
    project = Path(option("-project") if "-project" in args else option("-workspace"))
    products = Path(option("-derivedDataPath") if "-derivedDataPath" in args else "DerivedData")
    products = products / "Build" / "Products" / "Debug-iphoneos"
    if "-list" in args:
        print(json.dumps({"project": {"name": project.stem, "schemes": scenario["schemes"]}}))
    elif "-showBuildSettings" in args:
        print(json.dumps([
            {"target": "MyAppTests", "buildSettings": {"PRODUCT_TYPE": "com.apple.product-type.bundle.unit-test"}},
            {"target": "MyApp", "buildSettings": {
                "PRODUCT_TYPE": "com.apple.product-type.application", "FULL_PRODUCT_NAME": "MyApp.app",
                "PRODUCT_BUNDLE_IDENTIFIER": scenario["bundle_id"], "TARGET_BUILD_DIR": str(products)}}]))
    else:
        for line in scenario.get("build_lines") or [f"line {n}" for n in range(scenario.get("build_line_count", 3))]:
            print(line)
        if scenario.get("build_status", 0) == 0:
            app = products / "MyApp.app"
            app.mkdir(parents=True, exist_ok=True)
            commit = subprocess.run(["git", "-C", str(project.parent), "rev-parse", "HEAD"], check=True,
                                    capture_output=True, text=True).stdout
            (app / "commit.txt").write_text(commit)
        sys.exit(scenario.get("build_status", 0))
if name == "osascript":
    if any(arg.endswith("is running") for arg in args):
        print("true" if pop("running", False) else "false")
    sys.exit(0)
if name == "log":
    if args[0] == "collect":
        if scenario.get("collect_status"):
            sys.exit(scenario["collect_status"])
        Path(option("--output")).mkdir(parents=True)
        (Path(option("--output")) / "logdata").write_text("the whole device's log")
    else:
        print("Timestamp               Ty Process[PID:TID]")
        for line in scenario.get("log_lines", []):
            print(line)
        print("--------------------------------------------------------------------------------------------------")
    sys.exit(0)
if name == "cp":
    shutil.copytree(args[-2], args[-1], symlinks=True)
'''
TOOLS = ("xcrun", "xcodebuild", "osascript", "open", "log", "sudo")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=dict(os.environ, **GIT_ENV), check=True, capture_output=True,
                          text=True).stdout.strip()


def device(name: str, udid: str, kind: str = "iPhone", tunnel: str = "connected") -> dict:
    return {"hardwareProperties": {"reality": "physical", "platform": "iOS", "udid": udid, "deviceType": kind},
            "deviceProperties": {"name": name},
            "connectionProperties": {"pairingState": "paired", "tunnelState": tunnel}}


PHONE = device("Pat's iPhone", "PHONE-UDID")
TABLET = device("Pat's iPad", "TABLET-UDID", kind="iPad")


class FakeGitHub:
    """The pull requests `project ios build` reads, and who has write access to the repository."""

    def __init__(self) -> None:
        self.prs: dict[int, dict] = {}
        self.authors: dict[int, list[str]] = {}
        self.permissions = {"operator": "admin"}

    def add(self, number: int, sha: str, branch: str, head_repo: str = REPO, author: str = "operator") -> None:
        self.prs[number] = {"number": number, "state": "open", "user": {"login": author},
                            "head": {"sha": sha, "ref": branch, "repo": {"full_name": head_repo}}}

    def __call__(self, args: list[str], token: str | None = None) -> str:
        if args == ["api", "user", "--jq", ".login"]:
            return "operator\n"
        path = args[2] if args[:2] == ["api", "--paginate"] else args[1]
        if found := re.fullmatch(rf"repos/{REPO}/pulls/(\d+)", path):
            if int(found.group(1)) not in self.prs:
                raise review.NotFound("HTTP 404")
            return json.dumps(self.prs[int(found.group(1))])
        if found := re.fullmatch(rf"repos/{REPO}/pulls/(\d+)/commits", path):
            return "\n".join(self.authors.get(int(found.group(1)), ["operator"])) + "\n"
        if found := re.fullmatch(rf"repos/{REPO}/collaborators/([^/]+)/permission", path):
            return self.permissions.get(found.group(1), "read") + "\n"
        raise AssertionError(f"unexpected gh call: {args}")


class IosCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "--quiet", "--bare", "-b", "trunk", str(self.origin))
        self.work = self.tmp / "work"
        git(self.tmp, "init", "--quiet", "-b", "trunk", str(self.work))
        (self.work / "ios" / "MyApp.xcodeproj").mkdir(parents=True)
        (self.work / "ios" / "MyApp.xcodeproj" / "project.pbxproj").write_text("// project\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", "trunk")
        git(self.work, "remote", "add", "origin", str(self.origin))
        git(self.work, "push", "--quiet", "origin", "trunk")
        self.checkout = self.clone("checkout")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        standin = self.bin / "standin.py"
        standin.write_text(f"#!{sys.executable}\n{STANDIN}")
        standin.chmod(0o755)
        for name in TOOLS:
            (self.bin / name).symlink_to(standin)
        self.home = self.tmp / "home"
        self.desktop = self.home / "Desktop"
        self.state = self.tmp / "state" / "projector" / "ios" / REPO
        self.calls_file = self.tmp / "calls.jsonl"
        self.calls_file.touch()
        self.scenario_file = self.tmp / "scenario.json"
        self.scenario({"devices": [PHONE], "schemes": ["MyApp", "MyApp Development"],
                       "bundle_id": "com.example.myapp.dev"})
        environment = mock.patch.dict(os.environ, {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.tmp / "state"), "IOS_SCENARIO": str(self.scenario_file),
            "IOS_CALLS": str(self.calls_file)})
        environment.start()
        self.addCleanup(environment.stop)
        self.github = FakeGitHub()
        github = mock.patch.object(review, "run_gh", self.github)
        github.start()
        self.addCleanup(github.stop)

    def clone(self, name: str) -> Path:
        path = self.tmp / name
        git(self.tmp, "clone", "--quiet", str(self.origin), str(path))
        # The commands read the repository from origin's GitHub URL, which insteadOf points back at the bare origin.
        git(path, "remote", "set-url", "origin", f"https://github.com/{REPO}.git")
        git(path, "config", f"url.{self.origin}.insteadOf", f"https://github.com/{REPO}.git")
        return path

    def scenario(self, values: dict) -> None:
        current = json.loads(self.scenario_file.read_text()) if self.scenario_file.exists() else {}
        self.scenario_file.write_text(json.dumps({**current, **values}))

    def push(self, branch: str, text: str, ref: str | None = None) -> str:
        """Commit `text` on `branch` in the working repository and push it, to `ref` when one is given."""
        git(self.work, "checkout", "--quiet", "-B", branch, "trunk")
        (self.work / "change.txt").write_text(text + "\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", text)
        git(self.work, "push", "--quiet", "--force", "origin", f"HEAD:{ref or f'refs/heads/{branch}'}")
        return git(self.work, "rev-parse", "HEAD")

    def project(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.Path, "cwd", return_value=cwd or self.checkout), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["ios", *args])
        return code, out.getvalue(), err.getvalue()

    def succeeds(self, *args: str, cwd: Path | None = None) -> str:
        code, out, err = self.project(*args, cwd=cwd)
        self.assertEqual(0, code, err)
        return out

    def calls(self, name: str | None = None) -> list[list[str]]:
        calls = [json.loads(line) for line in self.calls_file.read_text().splitlines()]
        return [call for call in calls if name is None or call[0] == name]

    def builds(self) -> list[list[str]]:
        return [call for call in self.calls("xcodebuild") if "build" in call]

    def installed(self) -> list[tuple[str, str]]:
        return [(udid, commit) for _, udid, commit in self.calls("installed")]

    def worktree_head(self) -> str:
        return git(self.state / "worktree", "rev-parse", "HEAD")


class TrustTests(IosCase):
    def test_a_forks_pull_request_builds_from_refs_pull_once_allowed(self) -> None:
        sha = self.push("patch-1", "from a fork", ref="refs/pull/12/head")
        self.github.add(12, sha, "patch-1", head_repo="someone/fork", author="someone")

        self.succeeds("build", "12", "--allow-untrusted", "--no-install")

        self.assertEqual(sha, self.worktree_head())

    def test_an_untrusted_head_is_refused_before_anything_is_fetched_or_built(self) -> None:
        fork = self.push("patch-1", "from a fork", ref="refs/pull/12/head")
        self.github.add(12, fork, "patch-1", head_repo="someone/fork", author="someone")
        stranger = self.push("feature", "a stranger's commit")
        self.github.add(13, stranger, "feature")
        self.github.authors[13] = ["operator", "stranger"]

        for number, reason in (("12", "its head is in someone/fork"), ("13", "a commit names stranger")):
            with self.subTest(number):
                code, _, err = self.project("build", number)

                self.assertEqual(65, code)
                self.assertIn(reason, err)
                self.assertIn("--allow-untrusted", err)
        self.assertEqual([], self.calls("xcodebuild"))
        self.assertFalse((self.state / "worktree").exists())
        self.assertNotEqual(0, subprocess.run(["git", "-C", str(self.checkout), "cat-file", "-e", fork],
                                              capture_output=True).returncode, "the fork's head was fetched")


class WorktreeTests(IosCase):
    def test_a_build_in_a_second_clone_fetches_into_the_clone_that_owns_the_worktree(self) -> None:
        self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")
        second = self.clone("second")
        moved = self.push("feature", "second")

        self.succeeds("build", "feature", "--no-install", cwd=second)

        self.assertEqual(moved, self.worktree_head())
        common = git(self.state / "worktree", "rev-parse", "--path-format=absolute", "--git-common-dir")
        self.assertEqual((self.checkout / ".git").resolve(), Path(common).resolve())

    def test_a_worktree_whose_clone_is_gone_is_made_again_from_this_clone(self) -> None:
        sha = self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")
        second = self.clone("second")
        shutil.rmtree(self.checkout)

        self.succeeds("build", "feature", "--no-install", cwd=second)

        self.assertEqual(sha, self.worktree_head())
        common = git(self.state / "worktree", "rev-parse", "--path-format=absolute", "--git-common-dir")
        self.assertEqual((second / ".git").resolve(), Path(common).resolve())

    def test_cleanup_in_another_clone_removes_the_worktree_from_the_clone_that_owns_it(self) -> None:
        self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")
        second = self.clone("second")

        self.assertIn("Removed", self.succeeds("cleanup", cwd=second))

        self.assertEqual(["lock"], [path.name for path in self.state.iterdir()])
        self.assertEqual(1, len(git(self.checkout, "worktree", "list").splitlines()))
        self.assertEqual("Nothing to clean up.\n", self.succeeds("cleanup", cwd=second))


class KeptBuildTests(IosCase):
    def test_install_puts_the_named_targets_build_on_the_device_after_another_target_built(self) -> None:
        first = self.push("first", "first")
        self.succeeds("build", "first")
        self.push("second", "second")
        self.succeeds("build", "second", "--no-install")

        self.succeeds("install", "first")

        self.assertEqual([("PHONE-UDID", first), ("PHONE-UDID", first)], self.installed())

    def test_a_branch_and_the_pull_request_on_it_keep_separate_builds_in_flat_folders(self) -> None:
        on_branch = self.push("feature/login", "first")
        self.succeeds("build", "feature/login", "--no-install")
        on_pr = self.push("feature/login", "second")
        self.github.add(12, on_pr, "feature/login")
        self.succeeds("build", "12", "--no-install")

        self.assertEqual({"branch-feature%2Flogin", "pr-12"},
                         {path.name for path in (self.state / "builds" / "MyApp").iterdir()})
        self.succeeds("install", "feature/login")
        self.succeeds("install", "12")
        self.assertEqual([("PHONE-UDID", on_branch), ("PHONE-UDID", on_pr)], self.installed())

    def test_install_of_a_target_with_no_kept_build_says_how_to_make_one(self) -> None:
        self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")

        code, _, err = self.project("install", "other")

        self.assertEqual(1, code)
        self.assertIn("`project ios build other`", err)
        self.assertEqual([], self.installed())

    def test_a_mac_clones_the_app_and_other_systems_copy_it(self) -> None:
        (self.bin / "cp").symlink_to(self.bin / "standin.py")
        self.push("feature", "first")
        with mock.patch.object(ios.sys, "platform", "darwin"):
            self.succeeds("build", "feature", "--no-install")
        with mock.patch.object(ios.sys, "platform", "linux"):
            self.succeeds("build", "feature", "--no-install")

        self.assertEqual(1, len(self.calls("cp")))
        self.assertEqual("-cR", self.calls("cp")[0][1])
        self.assertTrue((self.state / "builds" / "MyApp" / "branch-feature" / "MyApp.app" / "commit.txt").exists())


class AppTests(IosCase):
    def test_the_development_scheme_wins_over_the_plain_one(self) -> None:
        self.push("feature", "first")

        self.succeeds("build", "feature", "--no-install")

        build = self.builds()[0]
        self.assertEqual("MyApp Development", build[build.index("-scheme") + 1])

    def test_a_project_with_neither_scheme_fails_and_lists_its_schemes(self) -> None:
        self.scenario({"schemes": ["Other", "Other Tests"]})
        self.push("feature", "first")

        code, _, err = self.project("build", "feature", "--no-install")

        self.assertEqual(1, code)
        self.assertIn("Other, Other Tests", err)
        self.assertEqual([], self.builds())

    def test_two_projects_and_no_setting_fail_naming_both(self) -> None:
        git(self.work, "checkout", "--quiet", "-B", "two", "trunk")
        (self.work / "tools" / "Helper.xcodeproj").mkdir(parents=True)
        (self.work / "tools" / "Helper.xcodeproj" / "project.pbxproj").write_text("// helper\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "--quiet", "-m", "two projects")
        git(self.work, "push", "--quiet", "origin", "two")

        code, _, err = self.project("build", "two", "--no-install")

        self.assertEqual(1, code)
        self.assertIn("ios/MyApp.xcodeproj", err)
        self.assertIn("tools/Helper.xcodeproj", err)
        self.assertEqual([], self.builds())


class DeviceTests(IosCase):
    def test_two_connected_devices_fail_before_the_build_and_list_both(self) -> None:
        self.scenario({"devices": [PHONE, TABLET]})
        self.push("feature", "first")

        code, _, err = self.project("build", "feature")

        self.assertEqual(1, code)
        self.assertIn("Pat's iPhone (PHONE-UDID, connected)", err)
        self.assertIn("Pat's iPad (TABLET-UDID, connected)", err)
        self.assertEqual([], self.calls("xcodebuild"))

    def test_ipad_picks_the_connected_ipad_beside_a_connected_iphone(self) -> None:
        self.scenario({"devices": [PHONE, TABLET]})
        sha = self.push("feature", "first")

        self.succeeds("build", "feature", "--ipad")

        self.assertEqual([("TABLET-UDID", sha)], self.installed())

    def test_an_unavailable_device_fails_before_the_build_and_says_how_to_reach_it(self) -> None:
        self.scenario({"devices": [device("Pat's iPhone", "PHONE-UDID", tunnel="unavailable")]})
        self.push("feature", "first")

        code, _, err = self.project("build", "feature")

        self.assertEqual(1, code)
        self.assertIn("plug it into this Mac and tap Trust", err)
        self.assertEqual([], self.calls("xcodebuild"))

    def test_devices_json_says_which_device_can_take_an_install(self) -> None:
        self.scenario({"devices": [PHONE, device("Old iPad", "OLD-UDID", kind="iPad", tunnel="unavailable")]})

        listed = json.loads(self.succeeds("devices", "--json"))["devices"]

        self.assertEqual({"PHONE-UDID": True, "OLD-UDID": False},
                         {entry["udid"]: entry["installable"] for entry in listed})

    def test_every_command_without_xcode_exits_69(self) -> None:
        bare = self.tmp / "bare"
        bare.mkdir()
        (bare / "git").symlink_to(shutil.which("git"))
        with mock.patch.dict(os.environ, {"PATH": str(bare)}):
            for command in (["devices"], ["build", "feature"], ["install"], ["logs", "--mac"], ["cleanup"]):
                with self.subTest(command[0]):
                    code, _, err = self.project(*command)

                    self.assertEqual(69, code)
                    self.assertIn(ios.TOOLS_MESSAGE, err)


class LaunchTests(IosCase):
    def setUp(self) -> None:
        super().setUp()
        sleep = mock.patch.object(ios.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.push("feature", "first")

    def test_a_locked_device_is_waited_for_with_one_message(self) -> None:
        self.scenario({"launches": ["The device is locked.", "The device is locked.", "ok"]})

        out = self.succeeds("build", "feature")

        self.assertEqual(1, out.count("Waiting…"))
        self.assertTrue(out.endswith("Opened it.\n"))
        self.assertEqual(3, sum("launch" in call for call in self.calls("xcrun")))

    def test_another_launch_error_ends_the_run_at_once(self) -> None:
        self.scenario({"launches": ["The application could not be found."]})

        code, _, err = self.project("build", "feature")

        self.assertEqual(1, code)
        self.assertIn("The application could not be found.", err)
        self.assertEqual(1, sum("launch" in call for call in self.calls("xcrun")))

    def test_mac_quits_the_running_copy_and_opens_a_wrapper(self) -> None:
        self.scenario({"running": [True, False]})

        self.succeeds("build", "feature", "--mac")

        build = self.builds()[0]
        self.assertEqual(ios.MAC_DESTINATION, build[build.index("-destination") + 1])
        self.assertTrue(any("tell application id (item 1 of argv) to quit" in call
                            for call in self.calls("osascript")))
        wrapper = self.state / "mac" / "MyApp.app"
        self.assertEqual([["open", str(wrapper)]], self.calls("open"))
        self.assertEqual("Wrapper/MyApp.app", os.readlink(wrapper / "WrappedBundle"))
        self.assertTrue((wrapper / "Wrapper" / "MyApp.app" / "commit.txt").exists())
        self.assertEqual([], self.installed())

    def test_the_bundle_id_reaches_applescript_as_an_argument_never_as_source(self) -> None:
        self.scenario({"running": [True, False], "bundle_id": 'com.example.my"app'})

        self.succeeds("build", "feature", "--mac")

        scripts = self.calls("osascript")
        self.assertEqual(3, len(scripts))
        for call in scripts:
            with self.subTest(call):
                self.assertEqual('com.example.my"app', call[-1])
                self.assertNotIn("com.example", " ".join(call[1:-1]))


class BuildOutputTests(IosCase):
    def test_a_build_outside_a_terminal_prints_two_lines_and_logs_them_all(self) -> None:
        self.scenario({"build_line_count": 5000})
        self.push("feature", "first")

        out = self.succeeds("build", "feature", "--no-install")

        self.assertEqual(2, len(out.splitlines()), out)
        self.assertEqual(5000, len((self.state / "build.log").read_text().splitlines()))

    def test_a_failed_build_prints_each_error_once_and_exits_with_its_status(self) -> None:
        errors = [f"/src/File{n}.swift:1:2: error: thing {n} is wrong" for n in range(25)]
        self.scenario({"build_lines": ["Compiling", *errors, errors[0], "** BUILD FAILED **"], "build_status": 3})
        self.push("feature", "first")

        code, _, err = self.project("build", "feature", "--no-install")

        self.assertEqual(3, code)
        self.assertEqual(1, err.count("thing 0 is wrong"))
        self.assertIn("thing 19 is wrong", err)
        self.assertNotIn("thing 20 is wrong", err)
        self.assertIn(str(self.state / "build.log"), err)
        self.assertFalse((self.state / "builds").exists())


def crash_report(body: dict) -> str:
    header = {"app_name": "MyApp", "timestamp": "2026-11-15 10:00:00.00 -0800", "app_version": "1.0",
              "build_version": "7", "os_version": "iPhone OS 26.1"}
    return json.dumps(header) + "\n" + json.dumps(body, indent=2)


EXCEPTION = {"exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"}, "faultingThread": 1,
             "usedImages": [{"name": "MyApp"}],
             "threads": [{"frames": [{"imageIndex": 0, "symbol": "main"}]},
                         {"queue": "player", "frames": [{"imageIndex": 0, "symbol": "Player.play()",
                                                         "sourceFile": "Player.swift", "sourceLine": 42}]}]}
WATCHDOG = {"termination": {"namespace": "FRONTBOARD", "code": 0x8badf00d,
                            "reasons": ["scene-update watchdog transgression"]},
            "usedImages": [{"name": "MyApp"}],
            "threads": [{"frames": [{"imageIndex": 0, "symbol": "Library.load()"}]},
                        {"frames": [{"imageIndex": 0, "symbol": "idle()"}]}]}


class CrashTests(IosCase):
    def setUp(self) -> None:
        super().setUp()
        self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")
        self.reports = self.home / "Library" / "Logs" / "DiagnosticReports"
        self.reports.mkdir(parents=True)

    def test_a_crash_summary_shows_the_exception_and_the_thread_that_crashed(self) -> None:
        (self.reports / "MyApp-2026-11-15-100000.ips").write_text(crash_report(EXCEPTION))

        out = self.succeeds("crashes", "--mac")

        self.assertIn("EXC_BREAKPOINT SIGTRAP", out)
        self.assertIn("Player.play() Player.swift:42", out)
        self.assertNotIn("main", out.split("thread")[-1])
        self.assertTrue((self.desktop / "MyApp-2026-11-15-100000.ips").exists())

    def test_a_watchdog_summary_shows_the_code_in_hex_and_the_main_thread(self) -> None:
        (self.reports / "Retired").mkdir()
        (self.reports / "Retired" / "MyApp-2026-11-15-100000.ips").write_text(crash_report(WATCHDOG))

        out = self.succeeds("crashes", "--mac")

        self.assertIn("termination FRONTBOARD 0x8badf00d", out)
        self.assertIn("thread 0, the main thread, since no thread crashed", out)
        self.assertIn("Library.load()", out)
        self.assertNotIn("idle()", out)

    def test_a_device_gives_its_newest_three_reports_of_this_app(self) -> None:
        crash_dir = self.tmp / "device-crashes"
        crash_dir.mkdir()
        files = []
        for day in range(1, 6):
            name = f"MyApp-2026-11-0{day}-100000.ips"
            (crash_dir / name).write_text(crash_report(EXCEPTION))
            files.append({"name": name, "relativePath": name, "resources": {"isDirectory": False},
                          "metadata": {"lastModDate": f"2026-11-0{day}T10:00:00Z"}})
        files.append({"name": "Other-2026-11-09-100000.ips", "relativePath": "Other-2026-11-09-100000.ips",
                      "resources": {"isDirectory": False}, "metadata": {"lastModDate": "2026-11-09T10:00:00Z"}})
        self.scenario({"crash_files": files, "crash_dir": str(crash_dir)})

        self.succeeds("crashes")

        self.assertEqual(["MyApp-2026-11-03-100000.ips", "MyApp-2026-11-04-100000.ips", "MyApp-2026-11-05-100000.ips"],
                         sorted(path.name for path in self.desktop.iterdir()))


def selects(predicate: str, subsystem: str) -> bool:
    """Whether `predicate`'s subsystem clauses keep a line from `subsystem`."""
    exact = re.findall(r'subsystem == "([^"]+)"', predicate)
    prefixes = re.findall(r'subsystem BEGINSWITH "([^"]+)"', predicate)
    return subsystem in exact or any(subsystem.startswith(prefix) for prefix in prefixes)


class LogTests(IosCase):
    def setUp(self) -> None:
        super().setUp()
        self.scenario({"log_lines": ["2026-11-15 14:42:10.1 Df MyApp[1:2] [com.example.myapp:audio] started",
                                     "2026-11-15 14:42:11.2 F  MyApp[1:2] Fatal error: index out of range"]})

    def build(self, bundle_id: str = "com.example.myapp.dev") -> None:
        self.scenario({"bundle_id": bundle_id})
        self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")

    def predicate(self) -> str:
        show = next(call for call in self.calls("log") if call[1] == "show")
        return show[show.index("--predicate") + 1]

    def test_the_filter_keeps_the_apps_subsystem_and_not_the_vendors(self) -> None:
        for bundle_id, kept, left in (
                ("com.example.myapp.dev", ["com.example.myapp", "com.example.myapp.dev", "com.example.myapp.dev.net"],
                 ["com.example", "com.example.myappx", "com.example.other"]),
                ("com.example.myapp", ["com.example.myapp", "com.example.myapp.net"],
                 ["com.example", "com.example.other"])):
            with self.subTest(bundle_id):
                self.calls_file.write_text("")
                self.build(bundle_id)

                self.succeeds("logs", "--mac")

                predicate = self.predicate()
                self.assertEqual(kept, [subsystem for subsystem in kept if selects(predicate, subsystem)])
                self.assertEqual([], [subsystem for subsystem in left if selects(predicate, subsystem)])
                self.assertIn('process == "MyApp" AND messageType == fault', predicate)

    def test_logs_read_the_app_from_its_kept_build_without_a_worktree(self) -> None:
        code, _, err = self.project("logs", "--mac")
        self.assertEqual(1, code)
        self.assertIn("`project ios build <branch-or-number>`", err)

        self.build()
        shutil.rmtree(self.state / "worktree")
        out = self.succeeds("logs", "--mac")

        self.assertIn("Wrote 2 lines of MyApp's log", out)

    def test_two_logs_in_one_second_get_two_files(self) -> None:
        self.build()
        with mock.patch.object(ios, "local_now", return_value=datetime(2026, 11, 15, 14, 42, 10)):
            self.succeeds("logs", "--mac")
            self.succeeds("logs", "--mac")

        self.assertEqual(["MyApp log 2026-11-15 at 14.42.10 2.txt", "MyApp log 2026-11-15 at 14.42.10.txt"],
                         sorted(path.name for path in self.desktop.iterdir()))

    def test_a_devices_log_comes_through_sudo_and_leaves_no_archive(self) -> None:
        self.build()

        out = self.succeeds("logs", "--last", "20m")

        collect = next(call for call in self.calls("sudo") if call[2] == "collect")
        self.assertEqual(["--device-udid", "PHONE-UDID", "--last", "20m"], collect[3:7])
        self.assertIn("Wrote 2 lines", out)
        self.assertEqual([], list(self.state.glob("log-*")))

    def test_a_failed_collect_leaves_no_file_and_no_archive(self) -> None:
        self.build()
        self.scenario({"collect_status": 130})

        code, _, err = self.project("logs")

        self.assertEqual(1, code)
        self.assertIn("no log was written", err)
        self.assertFalse(self.desktop.exists() and any(self.desktop.iterdir()))
        self.assertEqual([], list(self.state.glob("log-*")))

    def test_ctrl_c_during_the_collect_leaves_no_file_and_no_archive(self) -> None:
        self.build()
        run = subprocess.run

        def interrupted(args, *rest, **options):
            if "collect" in args:
                raise KeyboardInterrupt
            return run(args, *rest, **options)

        with mock.patch.object(ios.subprocess, "run", side_effect=interrupted), \
                self.assertRaises(KeyboardInterrupt):
            self.project("logs")

        self.assertEqual([], list(self.desktop.iterdir()))
        self.assertEqual([], list(self.state.glob("log-*")))

    def test_a_span_that_is_not_one_is_refused(self) -> None:
        self.build()

        code, _, err = self.project("logs", "--mac", "--last", "20")

        self.assertEqual(2, code)
        self.assertIn("--last takes a span", err)
        self.assertEqual([], [call for call in self.calls("log") if call[1] == "show"])


@unittest.skipIf(sys.platform == "win32", "the lock is an flock")
class LockTests(IosCase):
    def setUp(self) -> None:
        super().setUp()
        self.push("feature", "first")
        self.succeeds("build", "feature", "--no-install")

    def install_process(self) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-m", "projector", "ios", "install", "feature"], cwd=self.checkout,
                                env=dict(os.environ, PYTHONPATH=str(SRC)), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def test_a_second_run_waits_for_the_holder_and_says_so_once(self) -> None:
        import fcntl

        with open(self.state / "lock", "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            held.write("process 1, project ios build other")
            held.flush()
            waiting = self.install_process()
            lines: queue.Queue[str] = queue.Queue()
            threading.Thread(target=lambda: lines.put(waiting.stderr.readline()), daemon=True).start()
            self.assertIn("Waiting for process 1, project ios build other to finish", lines.get(timeout=30))
            self.assertEqual([], self.installed())
        out, err = waiting.communicate(timeout=60)

        self.assertEqual(0, waiting.returncode, err)
        self.assertNotIn("Waiting", err)
        self.assertEqual(1, len(self.installed()))

    def test_a_killed_holder_leaves_no_lock_behind(self) -> None:
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl, sys, time\n"
                                   "lock = open(sys.argv[1], 'a+')\nfcntl.flock(lock, fcntl.LOCK_EX)\n"
                                   "print('held', flush=True)\ntime.sleep(120)", str(self.state / "lock")],
                                  stdout=subprocess.PIPE, text=True)
        self.assertEqual("held\n", holder.stdout.readline())
        holder.kill()
        holder.wait()
        holder.stdout.close()

        code, _, err = self.project("install", "feature")

        self.assertEqual(0, code, err)
        self.assertNotIn("Waiting", err)


if __name__ == "__main__":
    unittest.main()

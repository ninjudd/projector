"""The hosted service's client: `project hosted login`, `status`, `subscribe`, `credentials`, and `logout` against a
fake service on a local port, the hosted relay's settings and AWS profile, the account token's keychain items, and
the review action's step for `app: projector`."""

from __future__ import annotations

import base64
import configparser
import json
import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from projector import agents, config, crypto, hosted, keychain, relay, s3
from tests.fake_s3 import FakeS3, plain
from tests.test_ci import step_script
from tests.test_projector import RepositoryTestCase
from tests.test_relay import FakeAdvapi32

ROOT = Path(__file__).parents[1]
# An account token as the service issues one: pjh_ and the unpadded base64url of 32 bytes.
TOKEN = "pjh_" + base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
CHECKOUT = "https://billing.example/checkout/1"
SUBSCRIBE = "https://billing.example/subscribe"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
# The keys `/v1/credentials` answers, spaced as no JSON encoder here writes them, so a client that parsed and
# printed them again would not print these bytes.
KEYS = (b'{"Version":1,  "AccessKeyId":"ASIAHOSTED", "SecretAccessKey":"' + SECRET.encode()
        + b'", "SessionToken":"hosted-session", "Expiration":"2099-01-01T00:00:00Z"}\n')
# A `project` that prints `credentials.json` beside it, as `project hosted credentials` prints the service's keys,
# and logs the arguments of each run.
STUB = '''import json, pathlib, sys
here = pathlib.Path(__file__).parent
with (here / "runs.jsonl").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.write((here / "credentials.json").read_text())
'''


class FakeService:
    """A hosted service on a local port that answers the calls the client makes, as the service's code does, and
    the OIDC endpoint GitHub Actions gives a job. It records each request it answered."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.pending = 1
        self.tokens = {TOKEN}
        self.issued = TOKEN
        self.challenges: dict[str, tuple[str, bytes]] = {}
        self.registered: list[str] = []
        # What the service seals in place of a key challenge, and from which key, as one replaying a record
        # sealed to the key would.
        self.sealing: Optional[bytes] = None
        self.sender: Optional[bytes] = None
        self.paid = True
        self.subscribed = True
        self.claims: dict[str, tuple[int, dict]] = {
            "acme": (200, {"owner": "acme", "installation_id": 1}),
            "acme-pending": (403, {"error": "the app cannot read acme-pending's members yet: an owner of "
                                            "acme-pending must accept the app's organization members permission in "
                                            "the organization's installation settings"}),
            "acme-claimed": (409, {"error": "the installation on acme-claimed already has a payer"}),
        }
        self.account ={"login": "octocat", "github_id": 1, "plan": "solo", "subscription_status": "active",
                        "paid_seats": 3, "keys": [{"key_id": "0123456789abcdef", "registered": "2026-11-01"}]}
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                service.answer(self)

            def do_POST(self) -> None:
                service.answer(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def answer(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("content-length") or 0)
        raw = handler.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else {}
        bearer = (handler.headers.get("authorization") or "").removeprefix("Bearer ")
        parts = urlsplit(handler.path)
        self.seen.append({"method": handler.command, "path": parts.path, "body": body, "bearer": bearer,
                          "machine": handler.headers.get(hosted.MACHINE_HEADER)})
        status, answer = self.route(handler.command, parts.path, parse_qs(parts.query), body, bearer)
        data = answer if isinstance(answer, bytes) else b"" if answer is None else json.dumps(answer).encode()
        handler.send_response(status)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def route(self, method: str, path: str, query: dict, body: dict, bearer: str) -> tuple[int, dict | bytes | None]:
        if path == "/oidc":
            if bearer != "request-token":
                return 401, {"message": "bad request token"}
            return 200, {"value": f"oidc-for-{query['audience'][0]}"}
        if path == "/v1/login":
            return 200, {"device_code": "device", "user_code": "ABCD-1234", "interval": 0, "expires_in": 60,
                         "verification_uri": "https://github.com/login/device"}
        if path == "/v1/login/poll":
            if self.pending:
                self.pending -= 1
                return 202, {"status": "pending", "interval": 0}
            return 200, {"token": self.issued, "login": "octocat", "expires_at": "2027-02-17T00:00:00Z"}
        if path == "/v1/review-token":
            if bearer != f"oidc-for-{self.url}":
                return 401, {"error": "the Actions token is refused"}
            # The OIDC token's ref is refs/pull/7/merge, and the service refuses a body that names another pull
            # request, or names it as anything but a JSON number.
            named = body.get("pull_request", 7)
            if type(named) is not int or named != 7:
                return 401, {"error": f"the Actions token was issued for pull request 7, not {named!r}"}
            if not self.paid:
                return 402, {"error": "the installation on acme is not paid for", "subscribe": SUBSCRIBE}
            return 200, {"token": "ghs_minted", "expires_at": "2026-11-19T01:00:00Z",
                         "login": "projector-review[bot]"}
        if bearer not in self.tokens:
            # The client, not the service, names the command that fixes this.
            return 401, {"error": "the account token is unknown, expired, or revoked"}
        if path == "/v1/keys":
            public = base64.b64decode(body["public_key"])
            if "challenge_id" not in body:
                challenge, made = secrets.token_hex(8), secrets.token_bytes(hosted.CHALLENGE_BYTES)
                self.challenges[challenge] = (body["public_key"], made)
                plaintext = hosted.CHALLENGE_PREFIX + made if self.sealing is None else self.sealing
                return 200, {"challenge_id": challenge,
                             "sealed": crypto.seal(self.sender or crypto.new_private_key(), public, plaintext)}
            sealed_to, made = self.challenges.pop(body["challenge_id"], ("", b""))
            if sealed_to != body["public_key"] or base64.b64decode(body["challenge"]) != made:
                return 401, {"error": "the challenge does not match"}
            self.registered.append(crypto.key_id(public))
            return 201, {"key_id": crypto.key_id(public)}
        if path == "/v1/account":
            return 200, self.account
        if path == "/v1/credentials":
            if not self.subscribed:
                return 402, {"error": "the account has no active subscription", "subscribe": SUBSCRIBE}
            return 200, KEYS
        if path == "/v1/billing/checkout":
            return 200, {"url": CHECKOUT}
        if path == "/v1/logout":
            self.tokens.discard(bearer)
            return 204, None
        if path == "/v1/installations/claim":
            if not self.subscribed:
                return 402, {"error": "claiming an installation needs a subscription to pay for it",
                             "subscribe": SUBSCRIBE}
            return self.claims.get(body.get("owner"), (404, {"error": "the app is not installed there"}))
        return 404, {"error": "no such route"}


class HostedCommandTests(RepositoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = FakeService()
        self.addCleanup(self.service.close)
        (self.home / ".projector.toml").write_text(f'[hosted]\nurl = "{self.service.url}"\n')
        self.items: dict[tuple[str, str], str] = {}
        self.enterContext(mock.patch.object(keychain, "usable", return_value=""))
        self.enterContext(mock.patch.object(keychain, "store", side_effect=self.store))
        self.enterContext(mock.patch.object(keychain, "read", side_effect=self.read))
        self.enterContext(mock.patch.object(keychain, "delete", side_effect=self.delete))
        self.key = relay.Key(crypto.new_private_key(), "keychain")
        self.enterContext(mock.patch.object(relay, "command_key", return_value=(self.key, "")))
        for name in ("XDG_STATE_HOME", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_PROFILE"):
            os.environ.pop(name, None)
        # The running `project`, in a folder whose name holds a space, which the profile's command must quote.
        self.bin = self.home / "bin dir"
        self.bin.mkdir()
        self.project = self.bin / "project"
        self.project.write_text(f"#!{sys.executable}\n{STUB}")
        self.project.chmod(0o755)
        (self.bin / "credentials.json").write_bytes(KEYS)
        self.enterContext(mock.patch.object(sys, "argv", [str(self.project), "hosted", "login"]))
        self.toml = self.home / ".projector.toml"
        self.aws = self.home / ".aws" / "config"

    def store(self, account: str, secret: str, timeout: Optional[float] = None, *,
              service: str = keychain.SERVICE) -> str:
        self.items[(service, account)] = secret
        return "fake keychain"

    def read(self, account: str, timeout: Optional[float] = None, *, service: str = keychain.SERVICE) -> str:
        if (service, account) not in self.items:
            raise keychain.KeychainError(f"fake keychain: holds no key {account}")
        return self.items[(service, account)]

    def delete(self, account: str, timeout: Optional[float] = None, *, service: str = keychain.SERVICE) -> None:
        self.items.pop((service, account), None)

    def signed_in(self) -> None:
        self.items[(hosted.SERVICE, self.service.url)] = TOKEN

    def requests(self) -> list[str]:
        return [f"{seen['method']} {seen['path']}" for seen in self.service.seen]

    def test_login_keeps_the_token_in_the_keychain_and_registers_the_key_by_opening_its_sealed_challenge(self) -> None:
        # The service registers a key only for an answer that opens its challenge, so a client that sent back
        # anything else, or kept no token, signs in to an account it then cannot use.
        code, out, err = self.invoke("hosted", "login")

        self.assertEqual(0, code, err)
        self.assertIn("enter the code ABCD-1234", out)
        self.assertEqual({(hosted.SERVICE, self.service.url): TOKEN}, self.items,
                         "the token is kept under the hosted service name and the address that issued it")
        self.assertEqual([self.key.id], self.service.registered)
        self.assertEqual(["POST /v1/login", "POST /v1/login/poll", "POST /v1/login/poll", "POST /v1/keys",
                          "POST /v1/keys"], self.requests(), "the client polls past a pending answer")
        self.assertIn(f"key {self.key.id} is registered", out)
        answered = base64.b64decode(self.service.seen[-1]["body"]["challenge"])
        self.assertEqual(hosted.CHALLENGE_BYTES, len(answered),
                         "only the random part goes back, never the label it was sealed with")

    def refused_challenge(self, sealed: bytes, secret: bytes) -> None:
        """Log in against a service that seals `sealed` in place of a key challenge, and check that the client
        refuses it, sends nothing back, and shows `secret` nowhere."""
        self.service.sealing = sealed

        code, out, err = self.invoke("hosted", "login")

        self.assertNotEqual(0, code)
        self.assertIn("is not a key challenge", err)
        self.assertEqual(1, self.requests().count("POST /v1/keys"), "no answer goes back to the service")
        self.assertEqual([], self.service.registered)
        self.assertNotIn(secret.decode(), out + err)

    def test_login_refuses_to_open_a_relay_record_the_service_passes_off_as_a_challenge(self) -> None:
        # agent.key opens the relay's records, so a service that replayed one would learn its plaintext.
        record = json.dumps({"secret": "relay-plaintext"}).encode()
        self.service.sender = self.key.private

        self.refused_challenge(record, b"relay-plaintext")

    def test_login_refuses_a_challenge_with_the_label_and_the_wrong_length(self) -> None:
        # Past the label, the service could fit a record's own bytes in any length it chose.
        self.refused_challenge(hosted.CHALLENGE_PREFIX + b"relay-plaintext", b"relay-plaintext")

    def test_login_refuses_a_token_outside_the_services_format_before_the_keychain_sees_it(self) -> None:
        # macOS stores through a script `security -i` reads, where a newline or quote in a token the service chose
        # would run a command of its own, such as deleting the relay's key.
        for issued in (f"{TOKEN}\ndelete-generic-password -s projector-agent", f'{TOKEN[:-1]}"', TOKEN[:-1]):
            with self.subTest(issued=issued):
                self.service.issued = issued

                with mock.patch.object(relay, "command_key", return_value=(None, relay.NO_KEY)):
                    code, out, err = self.invoke("hosted", "login")

                self.assertNotEqual(0, code)
                self.assertIn("is not an account token", err)
                self.assertNotIn(issued, out + err)
                self.assertEqual({}, self.items, "nothing reaches the keychain")

    def test_login_without_agent_key_keeps_the_token_and_says_why_no_key_is_registered(self) -> None:
        with mock.patch.object(relay, "command_key", return_value=(None, relay.NO_KEY)):
            code, out, err = self.invoke("hosted", "login")

        self.assertEqual(0, code, err)
        self.assertEqual(TOKEN, self.items[(hosted.SERVICE, self.service.url)])
        self.assertNotIn("POST /v1/keys", self.requests())
        self.assertIn(relay.NO_KEY, out)

    def test_status_prints_each_field_of_the_account(self) -> None:
        self.signed_in()

        code, out, err = self.invoke("hosted", "status")
        _, as_json, _ = self.invoke("hosted", "status", "--json")

        self.assertEqual(0, code, err)
        for shown in ("login: octocat", "plan: solo", "subscription: active", "paid seats: 3",
                      "keys: 0123456789abcdef (registered 2026-11-01)"):
            self.assertIn(shown, out)
        self.assertEqual({"url": self.service.url, **self.service.account},
                         {name: value for name, value in json.loads(as_json).items() if name != "schema_version"})
        self.assertEqual(TOKEN, self.service.seen[0]["bearer"])

    def test_a_refused_token_says_to_run_login(self) -> None:
        self.items[(hosted.SERVICE, self.service.url)] = "pjh_revoked"

        code, _, err = self.invoke("hosted", "status")
        self.items.clear()
        _, _, missing = self.invoke("hosted", "status")

        self.assertNotEqual(0, code)
        self.assertIn("the account token is unknown, expired, or revoked; run `project hosted login`", err)
        self.assertIn("run `project hosted login`", missing, "no token at all says the same")

    def test_subscribe_refuses_a_team_plan_without_seats_before_calling_the_service(self) -> None:
        self.signed_in()

        code, _, err = self.invoke("hosted", "subscribe", hosted.TEAM)

        self.assertNotEqual(0, code)
        self.assertIn("--seats", err)
        self.assertEqual([], self.service.seen, "no checkout starts for a plan the service would refuse")

    def test_subscribe_asks_for_the_seats_and_prints_the_page_without_a_browser(self) -> None:
        self.signed_in()

        with mock.patch("webbrowser.open") as opened:
            code, out, err = self.invoke("hosted", "subscribe", hosted.TEAM, "--seats", "4", "--no-browser")

        self.assertEqual(0, code, err)
        self.assertIn(CHECKOUT, out)
        opened.assert_not_called()
        self.assertEqual({"plan": hosted.TEAM, "seats": 4}, self.service.seen[-1]["body"])

    def test_claim_names_the_installation_the_caller_now_pays_for(self) -> None:
        self.signed_in()

        code, out, err = self.invoke("hosted", "claim", "acme")

        self.assertEqual(0, code, err)
        self.assertIn("you now pay for the installation on acme", out)
        self.assertEqual(({"owner": "acme"}, TOKEN), (self.service.seen[-1]["body"], self.service.seen[-1]["bearer"]))

    def test_claim_refused_for_the_members_permission_says_what_the_owner_must_accept(self) -> None:
        # The fix is on GitHub, in the organization's settings, so only the service's words name it.
        self.signed_in()

        code, _, err = self.invoke("hosted", "claim", "acme-pending")

        self.assertNotEqual(0, code)
        self.assertIn("must accept the app's organization members permission", err)

    def test_claim_of_an_installation_with_a_payer_says_it_has_one(self) -> None:
        self.signed_in()

        code, _, err = self.invoke("hosted", "claim", "acme-claimed")

        self.assertNotEqual(0, code)
        self.assertIn("the installation on acme-claimed already has a payer", err)

    def test_claim_without_a_paid_plan_says_to_subscribe_first(self) -> None:
        self.signed_in()
        self.service.subscribed = False

        code, _, err = self.invoke("hosted", "claim", "acme")

        self.assertNotEqual(0, code)
        self.assertIn("needs a paid plan first; run `project hosted subscribe`", err)
        self.assertIn(SUBSCRIBE, err)

    def test_logout_revokes_the_token_and_removes_it_from_the_keychain(self) -> None:
        self.signed_in()

        code, out, err = self.invoke("hosted", "logout")

        self.assertEqual(0, code, err)
        self.assertNotIn(TOKEN, self.service.tokens, "the service no longer honors the token")
        self.assertEqual({}, self.items)

    # The hosted relay

    def user_layer(self) -> dict:
        return tomllib.loads(self.toml.read_text())

    def profiles(self) -> dict[str, dict[str, str]]:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(self.aws.read_text())
        return {name: dict(parser[name]) for name in parser.sections()}

    def command(self) -> str:
        return shlex.join([str(self.project), "hosted", "credentials"])

    def test_login_points_the_relay_at_the_hosted_relay_and_keeps_every_other_line(self) -> None:
        # A login that rewrote either file would drop the comments and profiles you keep there.
        toml = (f'# my settings\n[hosted]\nurl = "{self.service.url}"\n\n[review]\nusername = "octocat"  # me\n')
        aws = "# my profiles\n[default]\nregion = us-west-2\n\n[profile work]\nsso_session = acme\n"
        self.toml.write_text(toml)
        self.aws.parent.mkdir()
        self.aws.write_text(aws)

        code, out, err = self.invoke("hosted", "login")

        self.assertEqual(0, code, err)
        self.assertEqual({"relay": hosted.RELAY, "s3_endpoint": config.HOSTED_RELAY, "s3_profile": hosted.PROFILE},
                         self.user_layer()["agent"])
        self.assertTrue(self.toml.read_text().startswith(toml), "every line before the new settings stays as it was")
        self.assertTrue(self.aws.read_text().startswith(aws))
        self.assertEqual({"credential_process": self.command()}, self.profiles()[f"profile {hosted.PROFILE}"],
                         "the command names the running project by its absolute path, quoted for its space")
        self.assertEqual({"region": "us-west-2"}, self.profiles()["default"])
        self.assertIn("the relay now goes through the hosted relay", out)

        code, out, err = self.invoke("hosted", "logout")

        self.assertEqual(0, code, err)
        self.assertEqual({}, self.user_layer()["agent"], "the settings go, as `project config unset` removes them")
        self.assertTrue(self.toml.read_text().startswith(toml))
        self.assertEqual(aws, self.aws.read_text(), "logout removes the profile and the blank line before it")
        self.assertIn("removed agent.relay, agent.s3_endpoint, agent.s3_profile, AWS profile projector-hosted", out)

    def test_login_leaves_another_relay_alone_and_names_the_command_that_switches(self) -> None:
        # Login moving a relay you chose would send your records somewhere you never asked for.
        self.toml.write_text(f'[hosted]\nurl = "{self.service.url}"\n\n[agent]\nrelay = "github:acme/agents"\n')
        before = self.toml.read_text()

        code, out, err = self.invoke("hosted", "login")

        self.assertEqual(0, code, err)
        self.assertEqual(before, self.toml.read_text())
        self.assertFalse(self.aws.exists(), "no profile is written for a relay that is not used")
        self.assertIn("agent.relay = 'github:acme/agents'; run `project hosted relay` to switch", out)

        code, out, err = self.invoke("hosted", "relay")

        self.assertEqual(0, code, err)
        self.assertEqual(hosted.RELAY, self.user_layer()["agent"]["relay"])
        self.assertEqual(self.command(), self.profiles()[f"profile {hosted.PROFILE}"]["credential_process"])

    def test_login_leaves_alone_an_s3_relay_a_nearer_file_names_and_its_aws_endpoint(self) -> None:
        # The relay reads agent.s3_endpoint and agent.s3_profile from whichever file sets each, so hosted values in
        # ~/.projector.toml would move this bucket onto the hosted store, signed with the hosted keys.
        work = self.home / "work"
        work.mkdir()
        (work / ".projector.toml").write_text('[agent]\nrelay = "s3://acme-agents/relay"\n')
        before = self.toml.read_text()

        code, out, err = self.invoke("hosted", "login", cwd=work)

        self.assertEqual(0, code, err)
        self.assertEqual(before, self.toml.read_text())
        self.assertFalse(self.aws.exists())
        self.assertIn(f"{work / '.projector.toml'} sets agent.relay = 's3://acme-agents/relay'", out)
        self.assertEqual("https://s3.amazonaws.com/acme-agents/relay", agents.relay_place(work, None).location)

        code, _, err = self.invoke("hosted", "relay", cwd=work)

        self.assertNotEqual(0, code, "the switch refuses rather than move the nearer file's relay")
        self.assertIn("remove it from that file, then run `project hosted relay`", err)
        self.assertEqual(before, self.toml.read_text())

    def test_login_without_agent_key_leaves_the_relay_alone(self) -> None:
        with mock.patch.object(relay, "command_key", return_value=(None, relay.NO_KEY)):
            code, _, err = self.invoke("hosted", "login")

        self.assertEqual(0, code, err)
        self.assertNotIn("agent", self.user_layer())
        self.assertFalse(self.aws.exists())

    def test_logout_removes_only_the_values_that_are_still_the_hosted_relays(self) -> None:
        self.invoke("hosted", "login")
        config.write_value(self.toml, "agent.relay", "github:acme/agents")
        self.aws.write_text(self.aws.read_text().replace("credential_process", "region = us-east-1\ncredential_process"))
        work = "\n[profile work]\ncredential_process = aws configure export-credentials --profile work\n"
        self.aws.write_text(self.aws.read_text() + work)

        code, out, err = self.invoke("hosted", "logout")

        self.assertEqual(0, code, err)
        self.assertEqual({"relay": "github:acme/agents"}, self.user_layer()["agent"])
        self.assertEqual({"region": "us-east-1"}, self.profiles()[f"profile {hosted.PROFILE}"],
                         "a profile holding a line of yours keeps it")
        self.assertTrue(self.aws.read_text().endswith(work))

        self.aws.write_text(f"[profile {hosted.PROFILE}]\ncredential_process = my-own-command\n")
        self.signed_in()
        self.service.tokens.add(TOKEN)
        code, _, err = self.invoke("hosted", "logout")

        self.assertEqual(0, code, err)
        self.assertEqual(f"[profile {hosted.PROFILE}]\ncredential_process = my-own-command\n", self.aws.read_text(),
                         "a credential_process that no longer runs project hosted credentials is yours")

    def test_credentials_prints_the_services_keys_unchanged_for_a_machine_id_kept_private(self) -> None:
        self.signed_in()

        first = self.invoke("hosted", "credentials")
        second = self.invoke("hosted", "credentials")

        self.assertEqual((0, KEYS.decode(), ""), first, "the body is printed byte for byte, and nothing on stderr")
        self.assertEqual(first, second)
        calls = [seen for seen in self.service.seen if seen["path"] == "/v1/credentials"]
        self.assertEqual([("POST", TOKEN)] * 2, [(seen["method"], seen["bearer"]) for seen in calls])
        machine = calls[0]["machine"]
        self.assertRegex(machine, r"\A[A-Za-z0-9_-]{1,64}\Z")
        self.assertEqual(machine, calls[1]["machine"], "a machine keeps its ID")
        kept = self.home / ".local" / "state" / "projector" / "hosted" / "machine"
        self.assertEqual(machine, kept.read_text().strip())
        if os.name == "posix":
            self.assertEqual(0o600, stat.S_IMODE(kept.stat().st_mode))

    def test_two_first_runs_at_once_answer_the_one_machine_id_the_file_keeps(self) -> None:
        # The service would see one machine under two IDs. Run A whole between run B's first read and its write.
        kept = self.home / ".local" / "state" / "projector" / "hosted" / "machine"
        real = Path.read_text
        first: list[str] = []
        started: list[bool] = []

        def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            try:
                return real(path, *args, **kwargs)
            finally:
                if path == kept and not started:
                    started.append(True)
                    first.append(hosted.machine_id())

        with mock.patch.object(Path, "read_text", autospec=True, side_effect=read_text):
            second = hosted.machine_id()

        self.assertEqual(first, [second], "both runs answer one ID")
        self.assertEqual(second, kept.read_text().strip())

        kept.write_text("cut sh\x00rt\n")
        replaced = hosted.machine_id()
        self.assertRegex(replaced, r"\A[A-Za-z0-9_-]{22}\Z", "a file that holds no ID is replaced")
        self.assertEqual(replaced, hosted.machine_id())

    def test_credentials_refused_say_what_fixes_them_and_fail(self) -> None:
        # The S3 client turns the failure into the relay's notice, so its last line must name the fix.
        self.items[(hosted.SERVICE, self.service.url)] = "pjh_revoked"
        code, out, err = self.invoke("hosted", "credentials")

        self.assertNotEqual(0, code)
        self.assertEqual("", out)
        self.assertIn("run `project hosted login`", err)

        self.signed_in()
        self.service.subscribed = False
        code, out, err = self.invoke("hosted", "credentials")

        self.assertNotEqual(0, code)
        self.assertEqual("", out)
        self.assertIn("the subscription lapsed", err)
        self.assertIn("run `project hosted subscribe`", err)
        self.assertIn(SUBSCRIBE, err)
        self.assertNotIn(TOKEN, err)

    def test_the_s3_client_signs_with_the_keys_the_written_profile_gets_from_project_hosted_credentials(self) -> None:
        server = FakeS3()
        self.addCleanup(server.close)
        server.grant("ASIAHOSTED", SECRET, "hosted-session")
        self.enterContext(mock.patch.object(s3, "connect", plain))
        self.toml.write_text(f'[hosted]\nurl = "{self.service.url}"\nrelay = "{server.endpoint}"\n')
        self.invoke("hosted", "login")
        agent = self.user_layer()["agent"]
        found = config.relay_location(agent["relay"])
        assert isinstance(found, config.S3Relay)
        # The daemon's environment, which need not have the PATH that finds project, and can hold AWS keys of
        # yours that the hosted relay would refuse.
        client = s3.Client(found.bucket, endpoint=agent["s3_endpoint"], profile=agent["s3_profile"],
                           environ={"HOME": str(self.home), "AWS_ACCESS_KEY_ID": "AKIAYOURS",
                                    "AWS_SECRET_ACCESS_KEY": "yours"})

        client.put_object("k/agents/a", b"{}", if_none_match=True)

        self.assertEqual(["ASIAHOSTED"], [seen.signed_by for seen in server.seen("PUT", "projector")])
        self.assertEqual("hosted-session", server.seen("PUT")[0].headers["x-amz-security-token"])
        runs = (self.bin / "runs.jsonl").read_text().splitlines()
        self.assertEqual([["hosted", "credentials"]], [json.loads(line) for line in runs])
        self.assertEqual("project hosted login", client.credentials.sign_in_fix(),
                         "a lapsed sign-in is fixed by logging in to the service, not by aws sso login")

    def test_hosted_relay_takes_an_https_url_and_login_writes_it_as_an_endpoint(self) -> None:
        code, _, err = self.invoke("config", "set", "hosted.relay", "http://relay.example/path")
        self.assertNotEqual(0, code)
        self.assertIn("hosted.relay is 'http://relay.example/path'", err)

        self.toml.write_text(f'[hosted]\nurl = "{self.service.url}"\nrelay = "https://Relay.Example:443/"\n')
        self.invoke("hosted", "login")

        self.assertEqual("https://relay.example", self.user_layer()["agent"]["s3_endpoint"])

    def test_a_402_from_the_relay_turns_it_off_with_the_subscribe_fix(self) -> None:
        lapsed = relay.store_error(s3.S3Error("PUT relay.example/projector/k answered 402", 402),
                                   f"profile {hosted.PROFILE}")

        self.assertIsInstance(lapsed, relay.Lapsed)
        assert isinstance(lapsed, relay.Lapsed)
        self.assertEqual("project hosted subscribe", lapsed.fix)
        self.assertIn("subscription lapsed", str(lapsed))


class KeychainServiceTests(unittest.TestCase):
    def test_the_macos_script_refuses_a_secret_that_could_run_a_command_and_still_stores_the_relay_key(self) -> None:
        # `security -i` splits its script on whitespace and reads quotes and backslashes, so such a secret could
        # end the store command and start another.
        ran: list[bytes] = []

        def run(tool: str, args: list[str], timeout: float, *, input: Optional[bytes] = None,
                secret: str = "") -> bytes:
            ran.append(input or b"")
            return b""

        relay_key = base64.b64encode(crypto.new_private_key()).decode()
        with mock.patch.object(keychain, "run", run):
            for secret in ('tok"en', "tok\ndelete-keychain", "tok en", "tok\\en", "tok'en", "tok\x00en"):
                with self.subTest(secret=secret), self.assertRaises(keychain.KeychainError) as refused:
                    keychain.MacOS().store("0123456789abcdef", secret, 1.0)
                self.assertNotIn(secret, str(refused.exception))
            self.assertEqual([], ran, "no refused secret reaches security")
            keychain.MacOS().store("0123456789abcdef", relay_key, 1.0)
            keychain.MacOS().store("https://hosted.example", TOKEN, 1.0, hosted.SERVICE)
        self.assertEqual(2, len(ran))
        self.assertIn(f"-w {relay_key}\n".encode(), ran[0])

    def test_every_backend_keeps_the_account_token_apart_from_the_relay_key(self) -> None:
        # A backend that ignored the service name would keep the token among the relay's keys, where removing
        # one removes the other.
        for system in ("darwin", "linux"):
            with self.subTest(system=system):
                ran: list[str] = []

                def run(tool: str, args: list[str], timeout: float, *, input: Optional[bytes] = None,
                        secret: str = "") -> bytes:
                    ran.append(" ".join(args) + " " + (input or b"").decode())
                    return b"pjh_account\n"

                with mock.patch.object(keychain, "SYSTEM", system), mock.patch.object(keychain, "run", run), \
                        mock.patch.object(keychain.MacOS, "usable", return_value=""), \
                        mock.patch.object(keychain.Linux, "usable", return_value=""):
                    keychain.store("https://hosted.example", "pjh_account", service=hosted.SERVICE)
                    keychain.read("https://hosted.example", service=hosted.SERVICE)
                    keychain.delete("https://hosted.example", service=hosted.SERVICE)
                self.assertEqual(4, len(ran))
                self.assertEqual([], [call for call in ran if keychain.SERVICE in call])
                self.assertTrue(all(hosted.SERVICE in call for call in ran), ran)
        fake = FakeAdvapi32()
        with mock.patch.object(keychain, "SYSTEM", "win32"), \
                mock.patch.object(keychain, "load_advapi32", return_value=fake):
            keychain.store("https://hosted.example", "pjh_account", service=hosted.SERVICE)
            keychain.delete("https://hosted.example", service=hosted.SERVICE)
        self.assertTrue(all(call == "CredFree" or f" {hosted.SERVICE}:https://hosted.example" in call
                            for call in fake.calls), fake.calls)


@unittest.skipIf(shutil.which("bash") is None, "the action's steps run in bash")
class ReviewTokenStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.addCleanup(self.service.close)
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.home / ".projector.toml").write_text(f'[hosted]\nurl = "{self.service.url}"\n')
        self.output = self.home / "output"
        self.output.write_text("")

    def step(self, **given: str) -> subprocess.CompletedProcess:
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.home), "PROJECTOR_ROOT": str(ROOT),
                       "APP": hosted.APP, "APP_ID": "", "GITHUB_REPOSITORY": "acme/app",
                       "GITHUB_OUTPUT": str(self.output), "XDG_STATE_HOME": str(self.home / "state"),
                       "ACTIONS_ID_TOKEN_REQUEST_URL": f"{self.service.url}/oidc?api-version=2.0",
                       "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token", "GITHUB_REF": "refs/pull/7/merge", **given}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", step_script("Request the hosted service's token")],
                              cwd=self.home, env=environment, capture_output=True, text=True)

    def outputs(self) -> dict[str, str]:
        return dict(line.partition("=")[::2] for line in self.output.read_text().splitlines())

    def test_the_step_trades_the_jobs_oidc_token_for_the_review_token_and_its_login(self) -> None:
        result = self.step()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual({"token": "ghs_minted", "login": "projector-review[bot]"}, self.outputs())
        self.assertLess(result.stdout.index("::add-mask::ghs_minted"),
                        result.stdout.index("projector-review[bot]"), "the token is masked before any line names it")
        traded = self.service.seen[-1]
        self.assertEqual(("/v1/review-token", f"oidc-for-{self.service.url}"), (traded["path"], traded["bearer"]),
                         "the OIDC token's audience is the service's own address")
        self.assertEqual({"repository": "acme/app", "pull_request": 7}, traded["body"])
        self.assertIs(int, type(traded["body"]["pull_request"]), "the service refuses the number as a string")

    def test_a_manual_run_names_no_pull_request_so_the_service_counts_who_started_it(self) -> None:
        result = self.step(GITHUB_REF="refs/heads/main")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual({"repository": "acme/app"}, self.service.seen[-1]["body"])

    def test_an_unpaid_installation_fails_the_job_with_the_services_words_and_where_to_subscribe(self) -> None:
        self.service.paid = False

        result = self.step()

        self.assertEqual(1, result.returncode)
        self.assertIn(f"::error::the installation on acme is not paid for; subscribe at {SUBSCRIBE}", result.stdout)
        self.assertEqual({}, self.outputs(), "no token reaches the review step")

    def test_a_job_without_id_token_write_is_told_to_add_it(self) -> None:
        result = self.step(ACTIONS_ID_TOKEN_REQUEST_URL="", ACTIONS_ID_TOKEN_REQUEST_TOKEN="")

        self.assertEqual(1, result.returncode)
        self.assertIn("id-token: write", result.stdout)
        self.assertEqual([], self.service.seen)


if __name__ == "__main__":
    unittest.main()

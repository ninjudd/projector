"""The S3 client: Signature Version 4, the four operations and their conditions, credentials, and the bounds on
what a store can make it wait for or read. Requests go to the fake server in `tests/fake_s3.py`."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from projector import s3
from tests.fake_s3 import FakeS3, plain

SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TOKEN = "IQoJb3JpZ2luX2VjEXAMPLETOKEN"

# Records one run of the credential process, then prints what `credentials.json` beside it holds, or fails the way
# `mode` says: as an expired AWS sign-in does, with another error, or by hanging.
PROCESS = '''import json, pathlib, sys, time
here = pathlib.Path(__file__).parent
with (here / "runs.jsonl").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
mode = (here / "mode").read_text().strip() if (here / "mode").exists() else ""
if mode == "hang":
    time.sleep(60)
    (here / "finished").write_text("yes")
if mode == "expired":
    print("Error when retrieving token from sso: Token has expired and refresh failed", file=sys.stderr)
    sys.exit(255)
if mode == "fail":
    print("Unable to locate credentials", file=sys.stderr)
    sys.exit(253)
print((here / "credentials.json").read_text())
'''


class SignatureTests(unittest.TestCase):
    def test_each_signature_matches_the_aws_signature_version_4_test_suite(self) -> None:
        """Cases from AWS's Signature Version 4 test suite, `get-vanilla` and its query cases, and the S3 examples
        in AWS's documentation, each with the signature AWS publishes."""
        empty = hashlib.sha256(b"").hexdigest()
        suite = s3.Credentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
        vanilla = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}
        doc = s3.Credentials("AKIAIOSFODNN7EXAMPLE", SECRET)
        bucket = {"Host": "examplebucket.s3.amazonaws.com", "x-amz-content-sha256": empty,
                  "x-amz-date": "20130524T000000Z"}
        welcome = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
        cases = [
            ("GET", "/", [], vanilla, empty, suite, "service",
             "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"),
            ("GET", "/", [("Param2", "value2"), ("Param1", "value1")], vanilla, empty, suite, "service",
             "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"),
            ("POST", "/", [], vanilla, empty, suite, "service",
             "5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b"),
            ("GET", "/test.txt", [], {**bucket, "Range": "bytes=0-9"}, empty, doc, "s3",
             "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"),
            ("PUT", "/test$file.text", [], {**bucket, "Date": "Fri, 24 May 2013 00:00:00 GMT",
                                            "x-amz-storage-class": "REDUCED_REDUNDANCY",
                                            "x-amz-content-sha256": welcome}, welcome, doc, "s3",
             "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"),
            ("GET", "/", [("lifecycle", "")], bucket, empty, doc, "s3",
             "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"),
            ("GET", "/", [("max-keys", "2"), ("prefix", "J")], bucket, empty, doc, "s3",
             "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"),
        ]
        for method, path, query, headers, payload, credentials, service, expected in cases:
            with self.subTest(method=method, path=path, query=query):
                signed = s3.authorization(method, path, query, headers, payload, credentials, "us-east-1", service)
                self.assertEqual(expected, signed.rsplit("Signature=", 1)[1])


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeS3()
        self.addCleanup(self.server.close)
        self.now = float(int(time.time()))
        self.server.clock = lambda: self.now
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        patcher = mock.patch.object(s3, "connect", plain)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.environ = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}

    def client(self, **options) -> s3.Client:
        return s3.Client("team", endpoint=self.server.endpoint, environ=self.environ, clock=lambda: self.now,
                         **options)

    def env_keys(self, access_key: str = "AKIAENV", token: str = "") -> None:
        self.server.grant(access_key, SECRET, token)
        self.environ.update({"AWS_ACCESS_KEY_ID": access_key, "AWS_SECRET_ACCESS_KEY": SECRET,
                             **({"AWS_SESSION_TOKEN": token} if token else {})})

    def aws_file(self, name: str, text: str) -> Path:
        path = self.home / ".aws" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return path

    def process(self, credentials: dict, mode: str = "") -> Path:
        """A credential_process for profile `acme-relay`, which names profile `acme-sso` as `aws configure
        export-credentials` does, printing `credentials`."""
        folder = self.home / "process"
        folder.mkdir(exist_ok=True)
        (folder / "credentials.json").write_text(json.dumps(credentials))
        (folder / "mode").write_text(mode)
        script = folder / "process.py"
        script.write_text(PROCESS)
        self.aws_file("config", f"[profile acme-relay]\ncredential_process = {sys.executable} {script} "
                                "configure export-credentials --profile acme-sso --format process\n")
        return folder

    def runs(self, folder: Path) -> list[list[str]]:
        path = folder / "runs.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class OperationTests(ServerCase):
    def test_each_condition_reaches_the_store_and_a_failed_one_raises_its_own_error(self) -> None:
        self.env_keys()
        client = self.client()

        first = client.put_object("relay/k/agents/a", b"{}", if_none_match=True)
        with self.assertRaises(s3.PreconditionFailed):
            client.put_object("relay/k/agents/a", b"{}", if_none_match=True)
        with self.assertRaises(s3.PreconditionFailed):
            client.put_object("relay/k/agents/a", b"{}", if_match='"wrong"')
        second = client.put_object("relay/k/agents/a", b'{"b": 1}', if_match=first)
        with self.assertRaises(s3.PreconditionFailed):
            client.get_object("relay/k/agents/a", first)
        read = client.get_object("relay/k/agents/a", second)
        with self.assertRaises(s3.PreconditionFailed):
            client.delete_object("relay/k/agents/a", first)
        client.delete_object("relay/k/agents/a", second)
        with self.assertRaises(s3.NotFound):
            client.delete_object("relay/k/agents/a", second)

        self.assertNotEqual(first, second)
        self.assertEqual(b'{"b": 1}', read)
        self.assertEqual([], self.server.names("team"))
        put = self.server.seen("PUT")[0]
        self.assertEqual("application/json", put.headers["content-type"])
        self.assertEqual("AKIAENV", put.signed_by)

    def test_a_listing_follows_continuation_tokens_and_gives_each_objects_etag_size_and_time(self) -> None:
        self.env_keys()
        self.server.page = 2
        names = [f"relay/k/inbox/r/m-{index}" for index in range(5)]
        etags = {name: self.server.put("team", name, b"x" * (index + 1), modified=self.now - index)
                 for index, name in enumerate(names)}
        self.server.put("team", "relay/other/agents/a", b"x")

        listed = self.client().list_objects("relay/k/")

        self.assertEqual([(name, etags[name], index + 1, self.now - index) for index, name in enumerate(names)],
                         [tuple(found) for found in listed])
        self.assertEqual(3, len(self.server.seen("GET")))

    def test_a_name_with_characters_signature_version_4_encodes_is_signed_as_sent(self) -> None:
        self.env_keys()

        self.client().put_object("relay/key id/user/u-1 +=", b"{}", if_none_match=True)
        listed = self.client().list_objects("relay/key id/")

        self.assertEqual(["relay/key id/user/u-1 +="], [found.name for found in listed])

    def test_an_unsigned_listing_is_refused_unless_the_bucket_is_public(self) -> None:
        self.env_keys()

        with self.assertRaises(s3.S3Error) as refused:
            self.client().list_objects("relay/", signed=False, limit=1)
        self.server.public = True
        listed = self.client().list_objects("relay/", signed=False, limit=1)

        self.assertEqual(403, refused.exception.status)
        self.assertEqual([], listed)
        self.assertEqual(["", ""], [found.signed_by for found in self.server.seen("GET")])

    def test_a_response_that_declares_a_document_type_is_refused_in_any_encoding(self) -> None:
        """The parser refuses a document type, so no entity it declares is expanded, whether the body is UTF-8 or
        UTF-16, whose bytes for `<!DOCTYPE` hold a NUL between each character."""
        self.env_keys()
        document = ('<?xml version="1.0" encoding="{}"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
                    "<ListBucketResult><Contents><Key>&lol2;</Key><Size>1</Size>"
                    "<LastModified>2026-01-01T00:00:00.000Z</LastModified></Contents></ListBucketResult>")
        bodies = {"utf-8": document.format("UTF-8").encode("utf-8"),
                  "utf-16": document.format("UTF-16").encode("utf-16"),
                  "utf-16-be": "﻿".encode("utf-16-be") + document.format("UTF-16").encode("utf-16-be"),
                  "utf-16 without a declaration": document.split("?>", 1)[1].encode("utf-16")}

        for encoding, body in bodies.items():
            with self.subTest(encoding=encoding):
                self.server.listing_body = body
                with self.assertRaises(s3.S3Error) as refused:
                    self.client().list_objects("relay/")
                self.assertIn("document type", str(refused.exception))
        self.server.listing_body = document.split("]>", 1)[1].replace("&lol2;", "relay/a").encode("utf-16")
        self.assertEqual(["relay/a"], [found.name for found in self.client().list_objects("relay/")])

    def test_a_listing_that_holds_more_than_1_mib_is_not_parsed(self) -> None:
        self.env_keys()
        self.server.listing_body = b"<ListBucketResult>" + b" " * s3.MAX_RESPONSE + b"</ListBucketResult>"

        with mock.patch.object(s3, "parse_xml", side_effect=AssertionError("parsed")), \
                self.assertRaises(s3.S3Error) as refused:
            self.client().list_objects("relay/")

        self.assertIn("more than 1024 KiB", str(refused.exception))

    def test_a_request_that_hangs_or_trickles_is_stopped_at_its_timeout(self) -> None:
        self.env_keys()
        self.server.hanging = {"list"}
        trickle = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(trickle.close)

        def drip() -> None:
            connection, _ = trickle.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 1000\r\n\r\n")
                for _ in range(40):
                    time.sleep(0.1)
                    try:
                        connection.sendall(b"x")
                    except OSError:
                        return

        threading.Thread(target=drip, daemon=True).start()
        slow = s3.Client("team", endpoint=f"https://127.0.0.1:{trickle.getsockname()[1]}", environ=self.environ,
                         clock=lambda: self.now)

        for client in (self.client(), slow):
            with self.subTest(client=client), mock.patch.object(s3, "TIMEOUT", 1.0):
                started = time.monotonic()
                with self.assertRaises(s3.S3Error) as stopped:
                    client.list_objects("relay/")
                self.assertLess(time.monotonic() - started, 2.5)
                self.assertIn("did not finish within 1 seconds", str(stopped.exception))

    def test_a_name_lookup_that_never_returns_is_stopped_at_the_timeout(self) -> None:
        """The lookup of the store's host name counts toward the request's timeout. A lookup that answers is
        connected to by address, with the name still in the `Host` header the request signs."""
        self.env_keys()
        port = self.server.server.server_address[1]
        release = threading.Event()
        self.addCleanup(release.set)
        looked: list[str] = []

        def stalled(host: str, *args: object, **options: object) -> list:
            looked.append(host)
            release.wait(10)
            raise socket.gaierror("the lookup was released")

        def answering(host: str, *args: object, **options: object) -> list:
            looked.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

        client = s3.Client("team", endpoint=f"https://s3.example.test:{port}", environ=self.environ,
                           clock=lambda: self.now)
        with mock.patch.object(socket, "getaddrinfo", stalled), mock.patch.object(s3, "TIMEOUT", 0.5):
            started = time.monotonic()
            with self.assertRaises(s3.S3Error) as stopped:
                client.list_objects("relay/")
            took = time.monotonic() - started
        with mock.patch.object(socket, "getaddrinfo", answering):
            listed = client.list_objects("relay/")

        self.assertLess(took, 2)
        self.assertIn("did not finish within 0.5 seconds", str(stopped.exception))
        self.assertEqual(["s3.example.test", "s3.example.test"], looked)
        self.assertEqual([], listed)
        sent = self.server.seen("GET")
        self.assertEqual([("AKIAENV", f"s3.example.test:{port}")],
                         [(found.signed_by, found.headers["host"]) for found in sent])

    def test_requests_sign_for_the_region_the_setting_the_environment_or_the_profile_gives(self) -> None:
        self.env_keys()
        self.aws_file("config", "[default]\nregion = eu-west-1\n")
        self.server.region = "eu-west-1"

        self.client().list_objects("relay/")
        with self.assertRaises(s3.S3Error):
            self.client(region="us-west-2").list_objects("relay/")
        self.environ["AWS_REGION"] = "ap-south-1"
        self.server.region = "ap-south-1"
        self.client().list_objects("relay/")

        self.assertEqual([200, 400, 200], [found.status for found in self.server.seen("GET")])


class CredentialTests(ServerCase):
    def test_keys_come_from_the_environment_then_the_profile_then_its_credential_process(self) -> None:
        self.server.grant("AKIAFILE", SECRET)
        self.server.grant("AKIAPROCESS", SECRET, TOKEN, self.now + 3600)
        folder = self.process({"Version": 1, "AccessKeyId": "AKIAPROCESS", "SecretAccessKey": SECRET,
                               "SessionToken": TOKEN, "Expiration": s3.stamp_of(self.now + 3600)})

        self.env_keys("AKIAENV", TOKEN)
        self.client(profile="acme-relay").list_objects("relay/")
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            del self.environ[name]
        self.aws_file("credentials", f"[acme-relay]\naws_access_key_id = AKIAFILE\naws_secret_access_key = {SECRET}\n")
        files = self.client(profile="acme-relay")
        files.list_objects("relay/")
        (self.home / ".aws" / "credentials").write_text("")
        process = self.client(profile="acme-relay")
        process.list_objects("relay/")

        self.assertEqual(["AKIAENV", "AKIAFILE", "AKIAPROCESS"], [found.signed_by for found in self.server.requests])
        self.assertEqual(("profile acme-relay", "profile acme-relay (credential_process)"),
                         (files.source(), process.source()))
        self.assertEqual(1, len(self.runs(folder)))

    def test_a_credential_process_runs_again_five_minutes_before_its_keys_expire_and_none_sign_after(self) -> None:
        expires = self.now + 600
        self.server.grant("AKIAONE", SECRET, TOKEN, expires)
        folder = self.process({"Version": 1, "AccessKeyId": "AKIAONE", "SecretAccessKey": SECRET,
                               "SessionToken": TOKEN, "Expiration": s3.stamp_of(expires)})
        client = self.client(profile="acme-relay")

        client.list_objects("relay/")
        self.now += 299
        client.list_objects("relay/")
        before = len(self.runs(folder))
        self.server.grant("AKIATWO", SECRET, TOKEN, self.now + 3600)
        (folder / "credentials.json").write_text(json.dumps(
            {"Version": 1, "AccessKeyId": "AKIATWO", "SecretAccessKey": SECRET, "SessionToken": TOKEN,
             "Expiration": s3.stamp_of(self.now + 3600)}))
        self.now += 2
        client.list_objects("relay/")
        self.now = expires + 1
        client.list_objects("relay/")

        self.assertEqual(1, before)
        self.assertEqual(2, len(self.runs(folder)))
        self.assertEqual(["AKIAONE", "AKIAONE", "AKIATWO", "AKIATWO"],
                         [found.signed_by for found in self.server.requests])
        self.assertEqual([], self.server.expired_uses)

    def test_keys_that_expired_with_no_fresher_ones_sign_nothing(self) -> None:
        expires = self.now + 600
        self.server.grant("AKIAONE", SECRET, TOKEN, expires)
        self.process({"Version": 1, "AccessKeyId": "AKIAONE", "SecretAccessKey": SECRET, "SessionToken": TOKEN,
                      "Expiration": s3.stamp_of(expires)})
        client = self.client(profile="acme-relay")
        client.list_objects("relay/")

        self.now = expires + 1
        with self.assertRaises(s3.CredentialsError) as lapsed:
            client.list_objects("relay/")

        self.assertEqual("expired", lapsed.exception.cause)
        self.assertEqual(1, len(self.server.requests))

    def test_no_error_repr_or_process_argument_holds_the_secret_key_or_the_token(self) -> None:
        self.server.grant("AKIAONE", SECRET, TOKEN, self.now + 3600)
        folder = self.process({"Version": 1, "AccessKeyId": "AKIAONE", "SecretAccessKey": SECRET,
                               "SessionToken": TOKEN, "Expiration": s3.stamp_of(self.now + 3600)})
        client = self.client(profile="acme-relay")
        client.list_objects("relay/")
        said = []
        for failing in ("fail", "expired"):
            (folder / "mode").write_text(failing)
            self.now += 3600
            try:
                client.list_objects("relay/")
            except s3.CredentialsError as error:
                said.append(str(error))
        self.server.keys.clear()
        self.environ.update({"AWS_ACCESS_KEY_ID": "AKIAONE", "AWS_SECRET_ACCESS_KEY": SECRET,
                             "AWS_SESSION_TOKEN": TOKEN})
        try:
            client.list_objects("relay/")
        except s3.S3Error as error:
            said.append(str(error))

        shown = " ".join([*said, repr(client), repr(client.credentials.get()), json.dumps(self.runs(folder))])
        self.assertEqual(3, len(said))
        self.assertNotIn(SECRET, shown)
        self.assertNotIn(TOKEN, shown)

    def test_an_expired_sign_in_names_the_aws_sso_login_that_fixes_it_and_another_failure_its_reason(self) -> None:
        folder = self.process({}, mode="expired")
        client = self.client(profile="acme-relay")

        with self.assertRaises(s3.CredentialsError) as expired:
            client.list_objects("relay/")
        (folder / "mode").write_text("fail")
        with self.assertRaises(s3.CredentialsError) as failed:
            client.list_objects("relay/")
        (self.home / ".aws" / "config").write_text("")
        with self.assertRaises(s3.CredentialsError) as missing:
            client.list_objects("relay/")

        self.assertEqual(("expired", "aws sso login --profile acme-sso"), (expired.exception.cause,
                                                                            expired.exception.fix))
        self.assertEqual("the AWS sign-in for profile acme-relay expired", str(expired.exception))
        self.assertEqual("failed", failed.exception.cause)
        self.assertIn("Unable to locate credentials", str(failed.exception))
        self.assertEqual("missing", missing.exception.cause)
        self.assertEqual([], self.server.requests)

    def test_a_credential_process_that_hangs_is_killed_at_its_timeout(self) -> None:
        folder = self.process({}, mode="hang")

        started = time.monotonic()
        with mock.patch.object(s3, "PROCESS_TIMEOUT", 1.0), self.assertRaises(s3.CredentialsError) as stopped:
            self.client(profile="acme-relay").list_objects("relay/")
        took = time.monotonic() - started

        self.assertEqual("timeout", stopped.exception.cause)
        self.assertLess(took, 5)
        self.assertEqual(1, len(self.runs(folder)))
        self.assertFalse((folder / "finished").exists())


if __name__ == "__main__":
    unittest.main()

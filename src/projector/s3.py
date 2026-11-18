"""A small S3 client from the standard library, for the relay's S3 locations (`relay.py`).

It signs each request with AWS Signature Version 4 and sends it over HTTPS with the default `ssl` context. It offers
the four operations the relay needs, `ListObjectsV2`, `GetObject`, `PutObject`, and `DeleteObject`, with the
conditional headers its leases use, and reads at most 1 MiB of any response.

Credentials come from the first of these that holds them:

1. `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` in the environment.
2. The profile `agent.s3_profile` names, else `AWS_PROFILE`, else `default`, in `~/.aws/credentials` and
   `~/.aws/config`, or the files `AWS_SHARED_CREDENTIALS_FILE` and `AWS_CONFIG_FILE` name.
3. That profile's `credential_process`, run with a 30-second timeout, whose JSON gives the keys and when they
   expire.

Credentials that expire are refreshed 5 minutes before they do, so no request is signed with credentials about to
lapse. No error, log line, or `repr` holds a secret key or a session token, and no secret is passed as a process
argument.
"""

from __future__ import annotations

import configparser
import hashlib
import hmac
import http.client
import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import threading
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import quote, urlsplit

from .core import ProjectorError, run_bounded

TIMEOUT = 30.0
PROCESS_TIMEOUT = 30.0
REFRESH = 300.0
MAX_RESPONSE = 1024 * 1024
# A listing of more pages than this is an error rather than a backlog: each page holds up to 1,000 objects.
MAX_PAGES = 50
ALGORITHM = "AWS4-HMAC-SHA256"
SERVICE = "s3"
DEFAULT_REGION = "us-east-1"
UNRESERVED = "-_.~"
NAMESPACE = "{http://s3.amazonaws.com/doc/2006-03-01/}"
DOCTYPE = re.compile(rb"<!doctype", re.IGNORECASE)
EXPIRED = re.compile(r"expired|refresh failed|sso session", re.IGNORECASE)
LAST_LINE = 200


class S3Error(ProjectorError):
    """A request S3 refused or that did not finish, with its HTTP status when one came back."""

    def __init__(self, message: str, status: Optional[int] = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class PreconditionFailed(S3Error):
    """A `412`: the condition a request carried does not hold."""


class NotFound(S3Error):
    """A `404`: no object has the name."""


class CredentialsError(S3Error):
    """Credentials that could not be found, read, or refreshed. `cause` is `missing`, `expired`, `timeout`, or
    `failed`, and `fix` is the command that fixes it, when one is known."""

    def __init__(self, message: str, cause: str, source: str, fix: str = "") -> None:
        super().__init__(message)
        self.cause = cause
        self.source = source
        self.fix = fix


@dataclass(frozen=True)
class Credentials:
    """Keys that sign requests, where they came from, and when they expire, if they do. Its repr names only the
    access key ID and the source."""

    access_key: str
    secret_key: str = field(repr=False)
    token: str = field(default="", repr=False)
    expires: Optional[float] = None
    source: str = ""
    # Whether the source can give fresh keys when these expire.
    refreshable: bool = True


class Listed(NamedTuple):
    name: str
    etag: str
    size: int
    modified: float


class Response(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


# Signature Version 4

def encode(text: str) -> str:
    """`text` percent-encoded as Signature Version 4 encodes a name or a query value: every byte but the
    unreserved characters."""
    return quote(text, safe=UNRESERVED)


def canonical_path(path: str) -> str:
    """The path with each segment encoded once, as S3 signs it."""
    return "/".join(encode(segment) for segment in path.split("/"))


def canonical_query(query: list[tuple[str, str]]) -> str:
    return "&".join(f"{name}={value}" for name, value in sorted((encode(name), encode(value))
                                                                for name, value in query))


def canonical_request(method: str, path: str, query: list[tuple[str, str]], headers: dict[str, str],
                      payload_hash: str) -> tuple[str, str]:
    """The canonical request and its signed headers, which are every header given, by lowercased name."""
    named = sorted((name.lower(), " ".join(str(value).split())) for name, value in headers.items())
    signed = ";".join(name for name, _ in named)
    lines = "".join(f"{name}:{value}\n" for name, value in named)
    return "\n".join([method, canonical_path(path), canonical_query(query), lines, signed, payload_hash]), signed


def signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    key = f"AWS4{secret}".encode()
    for part in (date, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def authorization(method: str, path: str, query: list[tuple[str, str]], headers: dict[str, str], payload_hash: str,
                  credentials: Credentials, region: str, service: str = SERVICE) -> str:
    """The `Authorization` header for a request whose headers include `x-amz-date`, signing every header."""
    stamp = next(value for name, value in headers.items() if name.lower() == "x-amz-date")
    date = stamp[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    request, signed = canonical_request(method, path, query, headers, payload_hash)
    to_sign = "\n".join([ALGORITHM, stamp, scope, hashlib.sha256(request.encode()).hexdigest()])
    signature = hmac.new(signing_key(credentials.secret_key, date, region, service), to_sign.encode(),
                         hashlib.sha256).hexdigest()
    return f"{ALGORITHM} Credential={credentials.access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"


def amz_date(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Credentials

def aws_files(environ: dict[str, str]) -> tuple[Path, Path]:
    home = Path(environ.get("HOME", "~")).expanduser()
    return (Path(environ.get("AWS_SHARED_CREDENTIALS_FILE") or home / ".aws" / "credentials").expanduser(),
            Path(environ.get("AWS_CONFIG_FILE") or home / ".aws" / "config").expanduser())


def read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(path.read_text(encoding="utf-8"), source=str(path))
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, configparser.Error) as error:
        raise CredentialsError(f"{path} could not be read: {type(error).__name__}", "failed", str(path)) from None
    return parser


def file_signature(path: Path) -> object:
    try:
        found = path.stat()
    except OSError:
        return None
    return found.st_mtime_ns, found.st_size, found.st_ino


def expiry(text: object) -> Optional[float]:
    """The time an ISO 8601 `Expiration` names, in seconds since the epoch, or None for one that names none."""
    if not isinstance(text, str):
        return None
    try:
        found = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (found if found.tzinfo else found.replace(tzinfo=timezone.utc)).timestamp()


class Provider:
    """Finds credentials for a profile, keeps them in memory, and refreshes those that expire 5 minutes before
    they do. Long-lived keys in the files are read again only when a file changes."""

    def __init__(self, profile: Optional[str], environ: dict[str, str], clock: Callable[[], float] = time.time) -> None:
        self.explicit = profile
        self.environ = environ
        self.clock = clock
        self.cached: Optional[Credentials] = None
        self.signature: object = None
        self.lock = threading.Lock()

    def __repr__(self) -> str:
        return f"Provider({self.profile})"

    @property
    def profile(self) -> str:
        return self.explicit or self.environ.get("AWS_PROFILE") or "default"

    def sections(self) -> tuple[dict[str, str], dict[str, str]]:
        """The profile's sections in the credentials file and the config file, each empty when absent."""
        credentials, config = aws_files(self.environ)
        keys, settings = read_ini(credentials), read_ini(config)
        name = self.profile
        found = dict(keys[name]) if keys.has_section(name) else {}
        section = "default" if name == "default" else f"profile {name}"
        configured = dict(settings[section]) if settings.has_section(section) else {}
        return found, configured

    def region(self) -> Optional[str]:
        try:
            return self.sections()[1].get("region") or None
        except CredentialsError:
            return None

    def get(self) -> Credentials:
        """Credentials good for at least 5 more minutes, or the freshest the source has, never ones that have
        expired."""
        with self.lock:
            now = self.clock()
            signature = tuple(file_signature(path) for path in aws_files(self.environ))
            cached = self.cached
            if cached is not None and cached.source == "environment":
                cached = None
            if cached is not None and cached.expires is None and signature != self.signature:
                cached = None
            if cached is None or (cached.expires is not None and cached.expires - now < REFRESH):
                cached = self.find(now)
            if cached.expires is not None and cached.expires <= now:
                raise CredentialsError(f"the credentials from {cached.source} expired at {stamp_of(cached.expires)}",
                                       "expired", cached.source, self.sign_in_fix())
            self.cached, self.signature = cached, signature
            return cached

    def find(self, now: float) -> Credentials:
        environ = self.environ
        if environ.get("AWS_ACCESS_KEY_ID") and environ.get("AWS_SECRET_ACCESS_KEY"):
            token = environ.get("AWS_SESSION_TOKEN") or ""
            return Credentials(environ["AWS_ACCESS_KEY_ID"], environ["AWS_SECRET_ACCESS_KEY"], token,
                               source="environment", refreshable=not token)
        keys, configured = self.sections()
        source = f"profile {self.profile}"
        for section in (keys, configured):
            if section.get("aws_access_key_id") and section.get("aws_secret_access_key"):
                return Credentials(section["aws_access_key_id"], section["aws_secret_access_key"],
                                   section.get("aws_session_token") or "", source=source)
        command = keys.get("credential_process") or configured.get("credential_process")
        if command:
            return self.run_process(command)
        raise CredentialsError(f"no AWS credentials: the environment has no AWS_ACCESS_KEY_ID, and profile "
                               f"{self.profile} has no keys and no credential_process", "missing", source)

    def sign_in_fix(self) -> str:
        """The command that signs in again for the profile's `credential_process`: `aws sso login` for the profile
        its command names, else for this profile."""
        try:
            keys, configured = self.sections()
        except CredentialsError:
            return ""
        command = keys.get("credential_process") or configured.get("credential_process") or ""
        try:
            words = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            words = []
        named = next((words[index + 1] for index, word in enumerate(words[:-1]) if word == "--profile"), None)
        return f"aws sso login --profile {named or self.profile}" if command else ""

    def run_process(self, command: str) -> Credentials:
        source = f"profile {self.profile} (credential_process)"
        try:
            words = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            raise CredentialsError(f"the credential_process of profile {self.profile} does not parse as a command",
                                   "failed", source) from None
        try:
            done = run_bounded(words, timeout=PROCESS_TIMEOUT, env=dict(self.environ))
        except subprocess.TimeoutExpired:
            raise CredentialsError(f"the credential_process of profile {self.profile} did not finish within "
                                   f"{PROCESS_TIMEOUT:g} seconds, so it was stopped", "timeout", source) from None
        except OSError as error:
            raise CredentialsError(f"the credential_process of profile {self.profile} could not start: "
                                   f"{error.strerror or type(error).__name__}", "failed", source) from None
        if done.returncode != 0:
            lines = done.stderr.decode("utf-8", "replace").strip().splitlines()
            said = (lines[-1] if lines else f"exited with status {done.returncode}")[:LAST_LINE]
            if EXPIRED.search(said):
                raise CredentialsError(f"the AWS sign-in for profile {self.profile} expired", "expired", source,
                                       self.sign_in_fix())
            raise CredentialsError(f"the credential_process of profile {self.profile} failed: {said}", "failed",
                                   source)
        try:
            found = json.loads(done.stdout)
        except ValueError:
            found = None
        if (not isinstance(found, dict) or found.get("Version") != 1
                or not isinstance(found.get("AccessKeyId"), str) or not isinstance(found.get("SecretAccessKey"), str)
                or not isinstance(found.get("SessionToken", ""), str)):
            raise CredentialsError(f"the credential_process of profile {self.profile} printed no credentials of "
                                   "Version 1", "failed", source)
        expires = expiry(found.get("Expiration"))
        if found.get("Expiration") is not None and expires is None:
            raise CredentialsError(f"the credential_process of profile {self.profile} printed an Expiration that is "
                                   "not an ISO 8601 time", "failed", source)
        return Credentials(found["AccessKeyId"], found["SecretAccessKey"], found.get("SessionToken") or "",
                           expires=expires, source=source)


def stamp_of(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Requests

def connect(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    """A connection to `host` over TLS, checked against the system's certificate authorities."""
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())


def parse_xml(body: bytes) -> ElementTree.Element:
    """The XML document in a response body, refusing one that declares a document type, so no entity it could
    declare is expanded."""
    if DOCTYPE.search(body):
        raise S3Error("the response declares a document type, which S3 never sends, so it was not parsed")
    try:
        return ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise S3Error("the response is not XML") from None


def child(element: ElementTree.Element, name: str) -> str:
    found = element.find(NAMESPACE + name)
    if found is None:
        found = element.find(name)
    return (found.text or "") if found is not None else ""


def children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return element.findall(NAMESPACE + name) or element.findall(name)


class Client:
    """One bucket on AWS or on the store `endpoint` names, an `https://` URL with no path. Requests go to
    `https://<bucket>.s3.<region>.amazonaws.com` on AWS, or path-style to the endpoint, and to AWS for a bucket
    whose name holds a dot, which no certificate for a virtual host matches."""

    def __init__(self, bucket: str, *, endpoint: Optional[str] = None, region: Optional[str] = None,
                 profile: Optional[str] = None, environ: Optional[dict[str, str]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.bucket = bucket
        self.environ = dict(os.environ) if environ is None else environ
        self.clock = clock
        self.credentials = Provider(profile, self.environ, clock)
        self.region = (region or self.environ.get("AWS_REGION") or self.environ.get("AWS_DEFAULT_REGION")
                       or self.credentials.region() or DEFAULT_REGION)
        if endpoint:
            parts = urlsplit(endpoint)
            self.host, self.base = parts.netloc, f"/{bucket}"
            self.address = (parts.hostname or "", parts.port or 443)
        else:
            self.host, self.base = ((f"s3.{self.region}.amazonaws.com", f"/{bucket}") if "." in bucket
                                    else (f"{bucket}.s3.{self.region}.amazonaws.com", ""))
            self.address = (self.host, 443)

    def __repr__(self) -> str:
        return f"Client({self.host}{self.base}, {self.credentials!r})"

    def source(self) -> str:
        """Where the credentials come from, such as `profile acme`, without reading them."""
        return self.credentials.cached.source if self.credentials.cached else f"profile {self.credentials.profile}"

    def request(self, method: str, name: Optional[str], *, query: Optional[list[tuple[str, str]]] = None,
                body: bytes = b"", headers: Optional[dict[str, str]] = None, signed: bool = True) -> Response:
        """Send one request for the object `name`, or for the bucket when it is None, and answer its response,
        raising `S3Error` for one that did not finish within `TIMEOUT` seconds or holds more than 1 MiB."""
        query = query or []
        path = f"{self.base}/{name}" if name is not None else (self.base or "/")
        payload = hashlib.sha256(body).hexdigest()
        sent = {"host": self.host, **(headers or {})}
        if signed:
            credentials = self.credentials.get()
            sent.update({"x-amz-date": amz_date(self.clock()), "x-amz-content-sha256": payload})
            if credentials.token:
                sent["x-amz-security-token"] = credentials.token
            sent["authorization"] = authorization(method, path, query, sent, payload, credentials, self.region)
        target = canonical_path(path) + (f"?{canonical_query(query)}" if query else "")
        connection = connect(*self.address, TIMEOUT)
        stopped = threading.Event()

        def stop() -> None:
            stopped.set()
            if connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        # The socket's timeout bounds each read, so a server that sends a byte at a time could hold a request for
        # hours; the timer bounds the whole request.
        timer = threading.Timer(TIMEOUT, stop)
        timer.daemon = True
        timer.start()
        try:
            connection.request(method, target, body=body or None, headers=sent)
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE + 1)
            found = Response(response.status, {key.lower(): value for key, value in response.getheaders()}, data)
        except (OSError, http.client.HTTPException) as error:
            if stopped.is_set() or isinstance(error, socket.timeout):
                raise S3Error(f"{method} {self.host}{path} did not finish within {TIMEOUT:g} seconds") from None
            raise S3Error(f"{method} {self.host}{path} failed: {type(error).__name__}") from None
        finally:
            timer.cancel()
            connection.close()
        if stopped.is_set():
            raise S3Error(f"{method} {self.host}{path} did not finish within {TIMEOUT:g} seconds")
        if len(found.body) > MAX_RESPONSE:
            raise S3Error(f"{method} {self.host}{path} answered with more than {MAX_RESPONSE // 1024} KiB")
        if found.status >= 300:
            raise self.error(method, path, found)
        return found

    def error(self, method: str, path: str, response: Response) -> S3Error:
        code = ""
        if response.body:
            try:
                code = child(parse_xml(response.body), "Code")[:64]
            except S3Error:
                code = ""
        said = f"{method} {self.host}{path} answered {response.status}" + (f" {code}" if code else "")
        kind = {412: PreconditionFailed, 404: NotFound}.get(response.status, S3Error)
        return kind(said, response.status, code)

    def list_objects(self, prefix: str, *, signed: bool = True, limit: Optional[int] = None) -> list[Listed]:
        """Every object whose name starts with `prefix`, following continuation tokens, or the first `limit`."""
        found: list[Listed] = []
        token = ""
        for _ in range(MAX_PAGES):
            query = [("list-type", "2"), ("prefix", prefix), *([("max-keys", str(limit))] if limit else []),
                     *([("continuation-token", token)] if token else [])]
            page = parse_xml(self.request("GET", None, query=query, signed=signed).body)
            for entry in children(page, "Contents"):
                modified = expiry(child(entry, "LastModified"))
                size = child(entry, "Size")
                if not child(entry, "Key") or modified is None or not size.isdigit():
                    raise S3Error("the listing holds an object with no name, size, or time")
                found.append(Listed(child(entry, "Key"), child(entry, "ETag"), int(size), modified))
            token = child(page, "NextContinuationToken")
            if limit or child(page, "IsTruncated") != "true" or not token:
                return found
        raise S3Error(f"the listing of {prefix} holds more than {MAX_PAGES} pages")

    def get_object(self, name: str, if_match: str) -> bytes:
        return self.request("GET", name, headers={"if-match": if_match}).body

    def put_object(self, name: str, body: bytes, if_match: Optional[str] = None, if_none_match: bool = False) -> str:
        """Write the object and answer its new ETag."""
        headers = {"content-type": "application/json",
                   **({"if-match": if_match} if if_match else {}), **({"if-none-match": "*"} if if_none_match else {})}
        etag = self.request("PUT", name, body=body, headers=headers).headers.get("etag")
        if not etag:
            raise S3Error(f"PUT {self.host}{self.base}/{name} answered with no ETag")
        return etag

    def delete_object(self, name: str, if_match: str) -> None:
        self.request("DELETE", name, headers={"if-match": if_match})

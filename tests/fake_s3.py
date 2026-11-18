"""A fake S3 server on a local port, for the S3 client's and the S3 relay's tests.

It serves path-style requests for any bucket: `ListObjectsV2` with continuation tokens and last-modified times,
and `GetObject`, `PutObject`, and `DeleteObject` with `If-Match` and `If-None-Match`. It checks every request's
Signature Version 4 from the bytes on the wire, with signing code of its own, against the keys a test gives it,
and refuses a request signed with keys past their expiry. A test can make it ignore a condition, answer an unsigned
listing, hang on an operation, or replace a listing's body. Tests reach it over plain HTTP by patching
`s3.connect` with `plain`.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qsl, quote, unquote
from xml.sax.saxutils import escape

XMLNS = "http://s3.amazonaws.com/doc/2006-03-01/"
# What `ignoring` names: the condition the server then treats as holding.
CONDITIONS = ("put-if-none-match", "put-if-match", "delete-if-match")


def plain(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    """A connection without TLS, which `s3.connect` is patched with to reach the fake server."""
    return http.client.HTTPConnection(host, port, timeout=timeout)


@dataclass
class Stored:
    body: bytes
    etag: str
    modified: float


@dataclass
class Keys:
    secret: str
    token: str = ""
    expires: Optional[float] = None


@dataclass
class Seen:
    """One request the server answered: what it was, the access key that signed it, if any, and its status."""

    method: str
    bucket: str
    name: str
    query: dict[str, str]
    headers: dict[str, str]
    signed_by: str
    status: int = 0


def key_of(secret: str, date: str, region: str) -> bytes:
    key = f"AWS4{secret}".encode()
    for part in (date, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


class FakeS3:
    def __init__(self, region: str = "us-east-1") -> None:
        self.region = region
        self.objects: dict[tuple[str, str], Stored] = {}
        self.keys: dict[str, Keys] = {}
        self.clock: Callable[[], float] = time.time
        self.ignoring: set[str] = set()
        self.public = False
        self.page = 1000
        self.hanging: set[str] = set()
        self.release = threading.Event()
        self.listing_body: Optional[bytes] = None
        self.requests: list[Seen] = []
        # Each request signed with keys whose expiry had passed, by access key.
        self.expired_uses: list[str] = []
        self.lock = threading.Lock()
        self.sequence = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def handle_one(self) -> None:
                server.answer(self)

            do_GET = do_PUT = do_DELETE = do_POST = handle_one

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"https://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def grant(self, access_key: str, secret: str, token: str = "", expires: Optional[float] = None) -> None:
        self.keys[access_key] = Keys(secret, token, expires)

    def put(self, bucket: str, name: str, body: bytes, modified: Optional[float] = None) -> str:
        """Store an object directly, as another writer with access to the bucket could, and answer its ETag."""
        with self.lock:
            self.sequence += 1
            etag = f'"{hashlib.md5(body + str(self.sequence).encode()).hexdigest()}"'
            self.objects[(bucket, name)] = Stored(body, etag, self.clock() if modified is None else modified)
            return etag

    def names(self, bucket: str, prefix: str = "") -> list[str]:
        return sorted(name for found, name in self.objects if found == bucket and name.startswith(prefix))

    def seen(self, method: str, bucket: Optional[str] = None) -> list[Seen]:
        return [found for found in self.requests if found.method == method and bucket in (None, found.bucket)]

    # Answering

    def answer(self, request: BaseHTTPRequestHandler) -> None:
        raw_path, _, raw_query = request.path.partition("?")
        length = int(request.headers.get("content-length") or 0)
        body = request.rfile.read(length) if length else b""
        headers = {name.lower(): value for name, value in request.headers.items()}
        bucket, _, name = unquote(raw_path).lstrip("/").partition("/")
        query = dict(parse_qsl(raw_query, keep_blank_values=True))
        seen = Seen(request.command, bucket, name, query, headers, "")
        self.requests.append(seen)
        operation = {"GET": "list" if not name else "get", "PUT": "put", "DELETE": "delete"}.get(request.command, "")
        if operation in self.hanging:
            self.release.wait(60)
        status, sent, extra = self.respond(request.command, raw_path, raw_query, headers, body, bucket, name, query,
                                           operation, seen)
        seen.status = status
        request.send_response(status)
        for header, value in extra.items():
            request.send_header(header, value)
        request.send_header("content-length", str(len(sent)))
        request.end_headers()
        request.wfile.write(sent)

    def respond(self, method: str, raw_path: str, raw_query: str, headers: dict[str, str], body: bytes,
                bucket: str, name: str, query: dict[str, str], operation: str,
                seen: Seen) -> tuple[int, bytes, dict[str, str]]:
        if "authorization" not in headers:
            if operation == "list" and self.public:
                return self.listing(bucket, query)
            return self.fail(403, "AccessDenied")
        signer = self.verify(method, raw_path, raw_query, headers, body)
        if isinstance(signer, tuple):
            return signer
        seen.signed_by = signer
        if operation == "list":
            return self.listing(bucket, query)
        with self.lock:
            stored = self.objects.get((bucket, name))
            if operation == "get":
                if stored is None:
                    return self.fail(404, "NoSuchKey")
                if headers.get("if-match") not in (None, stored.etag):
                    return self.fail(412, "PreconditionFailed")
                return 200, stored.body, {"etag": stored.etag}
            if operation == "put":
                if headers.get("if-none-match") == "*" and stored is not None \
                        and "put-if-none-match" not in self.ignoring:
                    return self.fail(412, "PreconditionFailed")
                if "if-match" in headers and "put-if-match" not in self.ignoring \
                        and (stored is None or stored.etag != headers["if-match"]):
                    return self.fail(412 if stored is not None else 404,
                                     "PreconditionFailed" if stored is not None else "NoSuchKey")
                self.sequence += 1
                etag = f'"{hashlib.md5(body + str(self.sequence).encode()).hexdigest()}"'
                self.objects[(bucket, name)] = Stored(body, etag, self.clock())
                return 200, b"", {"etag": etag}
            if operation == "delete":
                if stored is None:
                    return self.fail(404, "NoSuchKey") if "if-match" in headers else (204, b"", {})
                if "if-match" in headers and stored.etag != headers["if-match"] \
                        and "delete-if-match" not in self.ignoring:
                    return self.fail(412, "PreconditionFailed")
                del self.objects[(bucket, name)]
                return 204, b"", {}
        return self.fail(405, "MethodNotAllowed")

    def verify(self, method: str, raw_path: str, raw_query: str, headers: dict[str, str],
               body: bytes) -> str | tuple[int, bytes, dict[str, str]]:
        """The access key that signed the request, or the error S3 answers a request whose signature, payload
        hash, date, or token does not hold."""
        try:
            scheme, rest = headers["authorization"].split(" ", 1)
            fields = dict(part.strip().split("=", 1) for part in rest.split(","))
            access_key, date, region, service, terminal = fields["Credential"].split("/")
            signed = fields["SignedHeaders"].split(";")
            signature = fields["Signature"]
        except (KeyError, ValueError):
            return self.fail(400, "AuthorizationHeaderMalformed")
        keys = self.keys.get(access_key)
        if scheme != "AWS4-HMAC-SHA256" or keys is None or service != "s3" or terminal != "aws4_request":
            return self.fail(403, "InvalidAccessKeyId")
        if region != self.region:
            return self.fail(400, "AuthorizationHeaderMalformed")
        if "host" not in signed or "x-amz-date" not in signed or "x-amz-content-sha256" not in signed:
            return self.fail(403, "AccessDenied")
        if headers.get("x-amz-content-sha256") != hashlib.sha256(body).hexdigest():
            return self.fail(400, "XAmzContentSHA256Mismatch")
        stamp = headers.get("x-amz-date", "")
        try:
            when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return self.fail(403, "AccessDenied")
        if abs(when - self.clock()) > 900 or stamp[:8] != date:
            return self.fail(403, "RequestTimeTooSkewed")
        if keys.expires is not None and self.clock() >= keys.expires:
            self.expired_uses.append(access_key)
            return self.fail(400, "ExpiredToken")
        if keys.token and (headers.get("x-amz-security-token") != keys.token
                           or "x-amz-security-token" not in signed):
            return self.fail(403, "InvalidToken")
        # S3 decodes what it receives and encodes it again by the signing rules, so a client that signs a name or
        # query encoded any other way fails here as it would there.
        pairs = sorted((quote(unquote(key), safe="-_.~"), quote(unquote(value), safe="-_.~"))
                       for key, _, value in (part.partition("=") for part in raw_query.split("&") if part))
        path = "/".join(quote(unquote(segment), safe="-_.~") for segment in raw_path.split("/"))
        canonical = "\n".join([method, path, "&".join(f"{key}={value}" for key, value in pairs),
                               "".join(f"{header}:{' '.join(headers.get(header, '').split())}\n"
                                       for header in signed),
                               ";".join(signed), headers["x-amz-content-sha256"]])
        scope = f"{date}/{region}/s3/aws4_request"
        to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        expected = hmac.new(key_of(keys.secret, date, region), to_sign.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return self.fail(403, "SignatureDoesNotMatch")
        return access_key

    def listing(self, bucket: str, query: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
        if self.listing_body is not None:
            return 200, self.listing_body, {"content-type": "application/xml"}
        prefix = query.get("prefix", "")
        limit = min(self.page, int(query.get("max-keys") or self.page))
        with self.lock:
            names = [name for name in self.names(bucket, prefix) if name > query.get("continuation-token", "")]
            page, more = names[:limit], len(names) > limit
            entries = "".join(self.entry(name, self.objects[(bucket, name)]) for name in page)
        token = f"<NextContinuationToken>{escape(page[-1])}</NextContinuationToken>" if more else ""
        body = (f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{XMLNS}"><Name>{bucket}</Name>'
                f"<Prefix>{escape(prefix)}</Prefix><KeyCount>{len(page)}</KeyCount><MaxKeys>{limit}</MaxKeys>"
                f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{token}{entries}</ListBucketResult>")
        return 200, body.encode(), {"content-type": "application/xml"}

    def entry(self, name: str, stored: Stored) -> str:
        modified = datetime.fromtimestamp(stored.modified, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return (f"<Contents><Key>{escape(name)}</Key><LastModified>{modified}</LastModified>"
                f"<ETag>{escape(stored.etag, {chr(34): '&quot;'})}</ETag><Size>{len(stored.body)}</Size>"
                "<StorageClass>STANDARD</StorageClass></Contents>")

    def fail(self, status: int, code: str) -> tuple[int, bytes, dict[str, str]]:
        body = f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{code}</Message></Error>'
        return status, body.encode(), {"content-type": "application/xml"}

"""The client of a hosted Projector service, at the address `hosted.url` names.

`project hosted login` signs in through GitHub's device flow and keeps the account token the service returns in
the OS keychain, under the service name `projector-hosted` and the service's address, so a token goes only to the
service that issued it. It then registers `agent.key`'s public key: the service seals a labelled random challenge
to the key, and the CLI opens it with `crypto.unseal`, checks the label, and sends back the random bytes, which
proves it holds the private key without sending it. `status`, `subscribe`, `claim`, and `logout` call the service
with that token.

Login then points the relay at the hosted relay: `agent.relay`, `agent.s3_endpoint`, and `agent.s3_profile` in
`~/.projector.toml`, and an AWS profile whose `credential_process` runs `project hosted credentials`, which trades
the token and this machine's ID for short-lived keys. It leaves a relay that file already names elsewhere alone,
and logout removes only the values that are still the hosted relay's.

`review_token_step` is the review action's step for its `app: projector` input. It requests the job's GitHub
Actions OIDC token for the service's address as the audience, and trades it for an installation token that posts
the review as the service's app, so the workflow holds no private key.

Requests go through `urllib` alone. A refusal reads the service's `error` text, says to run `project hosted
login` when the token is refused, and gives the address the service names to subscribe at.
"""

from __future__ import annotations

import base64
import configparser
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

from . import crypto, keychain, relay, s3
from .config import (HOSTED_RELAY, HOSTED_URL, Config, ConfigError, hosted_url, load as load_config, s3_endpoint,
                     user_config_path, user_value, write_value)
from .core import ProjectorError, state_home, write_through
from .keychain import KeychainError

SERVICE = "projector-hosted"
TIMEOUT = 30.0
LOGIN = "run `project hosted login`"
# The plan sold by the seat, the one plan whose checkout names how many seats it buys.
TEAM = "team"
# What a key challenge's plaintext is: this label, then this many random bytes, which alone go back.
CHALLENGE_PREFIX = b"projector hosted key challenge v1\x00"
CHALLENGE_BYTES = 32
# An account token as the service issues one: `pjh_` and the unpadded base64url of 32 random bytes. The token
# comes from a server and goes into the keychain, so nothing else is kept.
ACCOUNT_TOKEN = re.compile(r"pjh_[A-Za-z0-9_-]{43}")
# The step's input that has the review post as the hosted service's app.
APP = "projector"
# The hosted relay: the bucket `agent.relay` names on the store `hosted.relay` names, signed with the keys the AWS
# profile's `credential_process` asks the service for.
RELAY = "s3://projector"
PROFILE = "projector-hosted"
CREDENTIALS = ["hosted", "credentials"]
# What names this machine to the service. Paired machines share `agent.key`, so the key cannot.
MACHINE_HEADER = "projector-machine"
MACHINE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MACHINE_BYTES = 16
# A section header and an option's name in the AWS config file, as `configparser` reads them.
SECTION = re.compile(r"\[(?P<name>.+)\]")
OPTION = re.compile(r"(?P<name>[^=:]+?)\s*[=:]")


class HostedError(ProjectorError):
    """A hosted service that cannot be reached or refuses a request."""


def address(config: Config) -> str:
    """The service's address from `hosted.url`, or the default."""
    value = config.setting("hosted.url")
    return hosted_url(value) if value else HOSTED_URL


def refusal(status: int, answer: dict) -> str:
    """What a refused request says: the service's own words, with the command or address that fixes it."""
    text = str(answer.get("error") or f"the service answered HTTP {status}")
    if status == 401 and "project hosted login" not in text:
        text += f"; {LOGIN}"
    if status == 402 and answer.get("subscribe"):
        text += f"; subscribe at {answer['subscribe']}"
    return text


def send(url: str, method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None,
         headers: Optional[dict[str, str]] = None) -> tuple[int, bytes]:
    """`method` on the service's `path`, with `body` as JSON, `token` as the bearer, and `headers` besides,
    answering the status and the bytes of the reply."""
    sent = {"accept": "application/json", "user-agent": "projector", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent["content-type"] = "application/json"
    if token:
        sent["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + path, data=data, headers=sent, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise HostedError(f"could not reach {url}: {reason}") from None


def parsed(raw: bytes) -> dict:
    """The JSON object a reply holds, or an empty one."""
    try:
        answer = json.loads(raw) if raw else {}
    except ValueError:
        answer = {}
    return answer if isinstance(answer, dict) else {}


def call(url: str, method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None,
         ok: tuple[int, ...] = (200,)) -> tuple[int, dict]:
    """`method` on the service's `path`, with `body` as JSON and `token` as the bearer, answering the status and
    the JSON object of the reply. A status outside `ok` raises the refusal."""
    status, raw = send(url, method, path, body, token)
    answer = parsed(raw)
    if status not in ok:
        raise HostedError(refusal(status, answer))
    return status, answer


# The account token

def stored_token(url: str) -> str:
    try:
        return keychain.read(url, service=SERVICE)
    except KeychainError as error:
        raise HostedError(f"no account token for {url} in the keychain ({error}); {LOGIN}") from None


def account(url: str) -> dict:
    """`GET /v1/account` with the stored token."""
    return call(url, "GET", "/v1/account", token=stored_token(url))[1]


# Sign-in

def seconds(value: object, default: float) -> float:
    """A count of seconds from the service, or `default` when it sent none."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 \
        else default


def sign_in(url: str, say: Callable[[str], None], sleep: Callable[[float], None] = time.sleep) -> dict:
    """Run the device flow: print the code and where to enter it, then poll until the person approves it, and
    answer the service's token and login."""
    started = call(url, "POST", "/v1/login", {})[1]
    device = started.get("device_code")
    if not isinstance(device, str) or not device:
        raise HostedError(f"{url} started no sign-in")
    say(f"Open {started.get('verification_uri')} and enter the code {started.get('user_code')}")
    interval = seconds(started.get("interval"), 5)
    deadline = time.monotonic() + seconds(started.get("expires_in"), 900)
    while time.monotonic() < deadline:
        sleep(interval)
        status, answer = call(url, "POST", "/v1/login/poll", {"device_code": device}, ok=(200, 202))
        if status == 200:
            if not isinstance(answer.get("token"), str) or not ACCOUNT_TOKEN.fullmatch(answer["token"]):
                raise HostedError(f"{url} signed you in, but what it answered is not an account token, so nothing "
                                  "was kept")
            return answer
        interval = seconds(answer.get("interval"), interval)
    raise HostedError(f"the code expired before it was approved; {LOGIN} again")


def register_key(url: str, token: str, key: Any) -> str:
    """Register `key`'s public key to the account by opening the challenge the service seals to it, and answer
    the key's ID. The service seals from a fresh key of its own, so the sender proves nothing and is not read.

    `agent.key` also opens the relay's records, so a service that passed one off as a challenge would learn its
    plaintext from the answer. A challenge is therefore exactly `CHALLENGE_PREFIX` and `CHALLENGE_BYTES` random
    bytes, and only those bytes go back. Anything else is refused before any answer is sent, and never shown."""
    public = base64.b64encode(key.public).decode("ascii")
    challenge = call(url, "POST", "/v1/keys", {"public_key": public}, token)[1]
    try:
        _, opened = crypto.unseal(key.private, challenge.get("sealed"))
    except crypto.CryptoError as error:
        raise HostedError(f"the challenge {url} sealed to key {key.id} does not open: {error}") from None
    if len(opened) != len(CHALLENGE_PREFIX) + CHALLENGE_BYTES or not opened.startswith(CHALLENGE_PREFIX):
        raise HostedError(f"{url} sealed something to key {key.id} that is not a key challenge, so nothing was sent "
                          "back; a service that asks the key to open anything else could read your relay records")
    answer = call(url, "POST", "/v1/keys", {"public_key": public, "challenge_id": challenge.get("challenge_id"),
                                            "challenge": base64.b64encode(opened[len(CHALLENGE_PREFIX):])
                                            .decode("ascii")}, token, ok=(200, 201))[1]
    return str(answer.get("key_id") or key.id)


def login(url: str, key: Any, no_key: str, say: Callable[[str], None], settings: dict[str, str]) -> dict:
    """Sign in, keep the account token in the keychain, and register `key`, or say `no_key` when there is none.
    Once the key is registered, point the relay at the hosted relay's `settings`, unless `~/.projector.toml`
    already names another relay. Answers what was done."""
    missing = keychain.usable()
    if missing:
        raise HostedError(f"the account token goes in the OS keychain, which cannot be used here: {missing}")
    signed = sign_in(url, say)
    try:
        store = keychain.store(url, signed["token"], service=SERVICE)
    except KeychainError as error:
        raise HostedError(f"signed in as {signed.get('login')}, but could not keep the account token: {error}") \
            from None
    done = {"url": url, "login": signed.get("login"), "store": store, "key_id": None}
    if key is None:
        say(f"signed in to {url} as {signed.get('login')}, and the {store} holds the account token; registered no "
            f"key, because {no_key}")
        return done
    try:
        done["key_id"] = register_key(url, signed["token"], key)
    except HostedError as error:
        raise HostedError(f"signed in as {signed.get('login')}, but could not register key {key.id}: {error}") \
            from None
    say(f"signed in to {url} as {signed.get('login')}, the {store} holds the account token, and key "
        f"{done['key_id']} is registered to the account")
    try:
        say(use_relay(settings, switch=False))
    except (ProjectorError, OSError) as error:
        raise HostedError(f"signed in and registered key {done['key_id']}, but could not point the relay at the "
                          f"hosted relay: {error}") from None
    return done


def logout(url: str, settings: dict[str, str]) -> str:
    """Remove the hosted relay's `settings` and profile where they still hold what login wrote, then revoke the
    stored token and remove it from the keychain. A token the service no longer knows is removed too; one that
    could not be revoked for another reason is kept, so the command can run again."""
    removed = drop_relay(settings)
    dropped = f"; removed {', '.join(removed)}, which pointed the relay at the hosted relay" if removed else ""
    try:
        status, _ = call(url, "POST", "/v1/logout", token=stored_token(url), ok=(200, 204, 401))
    except HostedError as error:
        raise HostedError(f"{error}{dropped}") from None
    revoked = "the service no longer knew the account token" if status == 401 else "revoked the account token"
    try:
        keychain.delete(url, service=SERVICE)
    except KeychainError as error:
        raise HostedError(f"{revoked}, but could not remove it from the keychain: {error}{dropped}") from None
    return f"signed out of {url}: {revoked}, and removed it from the keychain{dropped}"


# The hosted relay

def relay_settings(config: Config) -> dict[str, str]:
    """The settings that point the relay at the hosted relay, on the store `hosted.relay` names."""
    value = config.setting("hosted.relay")
    return {"agent.relay": RELAY, "agent.s3_endpoint": s3_endpoint(value, "hosted.relay") if value else HOSTED_RELAY,
            "agent.s3_profile": PROFILE}


def holds(key: str, value: Any, wanted: str) -> bool:
    """Whether a setting's `value` is the hosted relay's, reading an endpoint as a location's identity does."""
    if key == "agent.s3_endpoint" and isinstance(value, str):
        try:
            return s3_endpoint(value) == wanted
        except ConfigError:
            return False
    return value == wanted


def use_relay(settings: dict[str, str], *, switch: bool) -> str:
    """Write the hosted relay's AWS profile, then its `settings` to `~/.projector.toml`. Without `switch`, a file
    that already sets any of them to another value is left alone, so a relay never moves unless you ask, and the
    answer names the command that switches."""
    path = user_config_path()
    with relay.config_lock():
        taken = {key: value for key in settings
                 if (value := user_value(key)) is not None and not holds(key, value, settings[key])}
        if taken and not switch:
            named = ", ".join(f"{key} = {value!r}" for key, value in taken.items())
            return (f"left the relay as {path} sets it, {named}; run `project hosted relay` to switch to the hosted "
                    "relay")
        write_profile(s3.aws_files(dict(os.environ))[1], credentials_command())
        for key, value in settings.items():
            write_value(path, key, value)
    return (f"the relay now goes through the hosted relay, {RELAY} on {settings['agent.s3_endpoint']}, with keys "
            f"from AWS profile {PROFILE}, whose credential_process runs `project hosted credentials`")


def drop_relay(settings: dict[str, str]) -> list[str]:
    """Remove each of `settings` that `~/.projector.toml` still sets to the hosted relay's value, and the AWS
    profile's `credential_process` while it still runs `project hosted credentials`. Answers what was removed."""
    path = user_config_path()
    removed = []
    with relay.config_lock():
        for key, wanted in settings.items():
            if holds(key, user_value(key), wanted):
                write_value(path, key, None)
                removed.append(key)
        if remove_profile(s3.aws_files(dict(os.environ))[1]):
            removed.append(f"AWS profile {PROFILE}")
    return removed


def credentials_command() -> str:
    """The command the hosted profile's `credential_process` runs: the `project` running now, by its absolute
    path, so a daemon without your PATH finds it, or else this Python running the package, quoted as the S3 client
    splits it."""
    running = Path(sys.argv[0])
    words = ([os.path.abspath(running), *CREDENTIALS] if running.stem == "project" and running.is_file()
             else [sys.executable, "-m", "projector", *CREDENTIALS])
    return subprocess.list2cmdline(words) if os.name == "nt" else shlex.join(words)


def runs_credentials(command: Any) -> bool:
    """Whether a `credential_process` runs `project hosted credentials`."""
    try:
        return isinstance(command, str) and shlex.split(command, posix=os.name != "nt")[-2:] == CREDENTIALS
    except ValueError:
        return False


def machine_id() -> str:
    """This machine's ID for the service, made once from random bytes and kept in Projector's state directory
    with mode 0600. When two runs make one at once, both keep the first, because a link never replaces a file."""
    path = state_home() / "hosted" / "machine"
    for _ in range(2):
        try:
            found = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            found = ""
        except (OSError, UnicodeError) as error:
            raise HostedError(f"{path} could not be read: {type(error).__name__}") from None
        if MACHINE_ID.fullmatch(found):
            return found
        path.parent.mkdir(parents=True, exist_ok=True)
        made = path.with_name(f".machine.{secrets.token_hex(8)}")
        descriptor = os.open(made, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(base64.urlsafe_b64encode(secrets.token_bytes(MACHINE_BYTES)).rstrip(b"=").decode() + "\n")
        try:
            if path.exists():
                # A file that holds no ID, such as one cut short, is replaced.
                os.replace(made, path)
            else:
                os.link(made, path)
        except FileExistsError:
            pass
        except OSError:
            # A file system without hard links, where a replace is the closest step.
            os.replace(made, path)
        finally:
            made.unlink(missing_ok=True)
    raise HostedError(f"{path} holds no machine ID after one was written")


def credentials(url: str) -> str:
    """The keys the hosted relay signs with, exactly as the service answers them: the JSON a
    `credential_process` prints. A refusal says what fixes it and holds no secret."""
    status, raw = send(url, "POST", "/v1/credentials", token=stored_token(url),
                       headers={MACHINE_HEADER: machine_id()})
    if status == 402:
        # The S3 client reads a last line that says "expired" as an AWS sign-in to renew, so the service's own
        # words, which could say it, stay out of this one.
        page = parsed(raw).get("subscribe")
        raise HostedError("the subscription lapsed, so the hosted relay gives no keys; run `project hosted "
                          "subscribe`" + (f", or subscribe at {page}" if page else ""))
    if status != 200:
        raise HostedError(refusal(status, parsed(raw)))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HostedError(f"{url} answered credentials that are not UTF-8") from None


# The AWS config file

def read_profiles(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    """The AWS config file's lines and each section's options."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeError) as error:
        raise HostedError(f"{path} could not be read: {type(error).__name__}") from None
    return text.splitlines(keepends=True), sections(text, path)


def sections(text: str, path: Path) -> dict[str, dict[str, str]]:
    """Each section's options as `configparser` reads them, refusing a file it cannot read, because the S3
    client could not read it either."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text, source=str(path))
    except configparser.Error as error:
        raise HostedError(f"{path} does not read as an AWS config file ({type(error).__name__}), so it is "
                          "unchanged") from None
    return {name: dict(parser[name]) for name in parser.sections()}


def find_profile(lines: list[str]) -> tuple[Optional[int], int, Optional[tuple[int, int]]]:
    """The line of the hosted profile's header, the line its section ends before, and the span of its
    `credential_process` with the indented lines that continue it."""
    header: Optional[int] = None
    option: Optional[tuple[int, int]] = None
    for index, line in enumerate(lines):
        indented = line[:1].isspace()
        named = None if indented else SECTION.fullmatch(line.strip())
        if named and header is not None:
            return header, index, option
        if named:
            header = index if named["name"] == f"profile {PROFILE}" else None
        elif header is not None and option is not None and option[1] == index and indented and line.strip():
            option = (option[0], index + 1)
        elif header is not None and option is None and not indented:
            found = OPTION.match(line)
            if found and found["name"].strip().lower() == "credential_process":
                option = (index, index + 1)
    return header, len(lines), option


def write_profiles(path: Path, lines: list[str], expected: dict[str, dict[str, str]]) -> None:
    """Write `lines` once they read back as `expected`, so no other profile or option changes."""
    text = "".join(lines)
    if sections(text, path) != expected:
        raise HostedError(f"{path} would not read back with only profile {PROFILE} changed, so it is unchanged")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_through(path, text)


def write_profile(path: Path, command: str) -> None:
    """Set the hosted profile's `credential_process` to `command`, adding the profile when the file has none."""
    lines, before = read_profiles(path)
    section = f"profile {PROFILE}"
    if before.get(section, {}).get("credential_process") == command:
        return
    header, _, option = find_profile(lines)
    line = f"credential_process = {command}\n"
    if option is not None:
        lines[option[0]:option[1]] = [line]
    elif header is not None:
        lines.insert(header + 1, line)
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += [*(["\n"] if lines else []), f"[{section}]\n", line]
    write_profiles(path, lines, {**before, section: {**before.get(section, {}), "credential_process": command}})


def remove_profile(path: Path) -> bool:
    """Remove the hosted profile's `credential_process` while it runs `project hosted credentials`, and the
    profile with it when nothing else is left in it. Answers whether anything was removed."""
    lines, before = read_profiles(path)
    section = f"profile {PROFILE}"
    header, end, option = find_profile(lines)
    if header is None or option is None or not runs_credentials(before[section].get("credential_process")):
        return False
    del lines[option[0]:option[1]]
    end -= option[1] - option[0]
    expected = {**before, section: {name: value for name, value in before[section].items()
                                    if name != "credential_process"}}
    if not expected[section] and not any(line.strip() for line in lines[header + 1:end]):
        # The blank line login wrote before the profile goes with it when the profile ends the file.
        start = header - 1 if header and end == len(lines) and not lines[header - 1].strip() else header
        del lines[start:end]
        del expected[section]
    write_profiles(path, lines, expected)
    return True


# Installations

def claim(url: str, owner: str) -> str:
    """Become the payer of the service app's installation on `owner`, which has none, such as one made before the
    service recorded installations."""
    status, answer = call(url, "POST", "/v1/installations/claim", {"owner": owner}, stored_token(url),
                          ok=(200, 402, 409))
    if status == 402:
        raise HostedError(f"claiming an installation needs a paid plan first; run `project hosted subscribe`, then "
                          f"claim it again ({refusal(status, answer)})")
    if status == 409:
        raise HostedError(f"the installation on {owner} already has a payer, so it cannot be claimed "
                          f"({refusal(status, answer)})")
    return (f"you now pay for the installation on {answer.get('owner') or owner}, so its reviews count against your "
            "plan")


# Subscription

def checkout(url: str, plan: str, seats: Optional[int]) -> str:
    """The address of the page that subscribes the account to `plan`, refusing a team plan without seats, and
    seats for any other plan, before any request."""
    if plan == TEAM and seats is None:
        raise HostedError(f"the {TEAM} plan is sold by the seat; pass --seats with how many")
    if plan != TEAM and seats is not None:
        raise HostedError(f"only the {TEAM} plan is sold by the seat, so --seats goes only with {TEAM}")
    body: dict[str, Any] = {"plan": plan}
    if seats is not None:
        body["seats"] = seats
    answer = call(url, "POST", "/v1/billing/checkout", body, stored_token(url))[1]
    found = answer.get("url")
    if not isinstance(found, str) or not found:
        raise HostedError(f"{url} gave no address to subscribe at")
    return found


def subscribe(url: str, plan: str, seats: Optional[int], browser: bool,
              open_url: Callable[[str], Any] = webbrowser.open) -> str:
    page = checkout(url, plan, seats)
    if browser:
        try:
            if open_url(page):
                return f"opened {page} in your browser; finish subscribing there"
        except (webbrowser.Error, OSError):
            pass
    return f"open {page} to finish subscribing"


def status_lines(url: str, found: dict) -> list[str]:
    keys = found.get("keys") or []
    listed = ", ".join(f"{entry.get('key_id')} (registered {entry.get('registered')})"
                       for entry in keys if isinstance(entry, dict)) or "none"
    return [f"service: {url}", f"login: {found.get('login')}", f"plan: {found.get('plan') or 'none'}",
            f"subscription: {found.get('subscription_status') or 'none'}",
            f"paid seats: {found.get('paid_seats') or 0}", f"keys: {listed}"]


# The review action's step

def review_token_step(environ: Optional[dict] = None, cwd: Optional[Path] = None) -> int:
    """The review action's step for `app: projector`: trade the job's OIDC token for the service's installation
    token, mask it, and write it and its login to the step's outputs. Any refusal fails the step with the
    service's words."""
    environ = os.environ if environ is None else environ
    app, app_id = environ.get("APP", ""), environ.get("APP_ID", "")
    if app != APP:
        return failed(f"the app input takes {APP}, for the hosted service's app, not {app!r}")
    if app_id:
        return failed("give the action app or app-id, not both")
    request_url, request_token = environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), environ.get(
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        return failed("the job cannot request an OIDC token; give it `permissions: id-token: write`")
    try:
        url = address(load_config(cwd or Path.cwd(), environ=dict(environ)))
        oidc = actions_token(request_url, request_token, url)
        answer = call(url, "POST", "/v1/review-token", review_request(environ), oidc)[1]
    except (ConfigError, HostedError) as error:
        return failed(str(error))
    token, login = answer.get("token"), answer.get("login")
    if not isinstance(token, str) or not token or not isinstance(login, str) or not login:
        return failed(f"{url} answered with no token or no login")
    print(f"::add-mask::{token}", flush=True)
    with open(environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"token={token}\nlogin={login}\n")
    print(f"the review posts as {login}, with a token {url} minted until {answer.get('expires_at')}")
    return 0


def review_request(environ: dict) -> dict:
    """The body of `/v1/review-token`: the repository, and the pull request a `pull_request` job's merge ref names,
    as a number, which the service checks against the OIDC token's ref. A `workflow_dispatch` job names none, and
    the service counts the user who started the run."""
    body: dict[str, Any] = {"repository": environ.get("GITHUB_REPOSITORY", "")}
    merge = re.fullmatch(r"refs/pull/(\d+)/merge", environ.get("GITHUB_REF", ""))
    if merge:
        body["pull_request"] = int(merge.group(1))
    return body


def actions_token(request_url: str, request_token: str, audience: str) -> str:
    """The job's OIDC token for `audience`, from the endpoint GitHub Actions gives a job with `id-token: write`."""
    separator = "&" if "?" in request_url else "?"
    request = urllib.request.Request(f"{request_url}{separator}audience={urllib.parse.quote(audience, safe='')}",
                                     headers={"authorization": f"Bearer {request_token}",
                                              "accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            value = json.loads(response.read()).get("value")
    except (urllib.error.URLError, OSError, ValueError, AttributeError) as error:
        raise HostedError(f"GitHub Actions gave no OIDC token: {getattr(error, 'reason', error)}") from None
    if not isinstance(value, str) or not value:
        raise HostedError("GitHub Actions gave no OIDC token")
    return value


def failed(message: str) -> int:
    print(f"::error::{message}", flush=True)
    return 1

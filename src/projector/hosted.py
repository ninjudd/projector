"""The client of a hosted Projector service, at the address `hosted.url` names.

`project hosted login` signs in through GitHub's device flow and keeps the account token the service returns in
the OS keychain, under the service name `projector-hosted` and the service's address, so a token goes only to the
service that issued it. It then registers `agent.key`'s public key: the service seals a random challenge to the
key, and the CLI opens it with `crypto.unseal` and sends it back, which proves it holds the private key without
sending it. `status`, `subscribe`, and `logout` call the service with that token.

`review_token_step` is the review action's step for its `app: projector` input. It requests the job's GitHub
Actions OIDC token for the service's address as the audience, and trades it for an installation token that posts
the review as the service's app, so the workflow holds no private key.

Requests go through `urllib` alone. A refusal reads the service's `error` text, says to run `project hosted
login` when the token is refused, and gives the address the service names to subscribe at.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

from . import crypto, keychain
from .config import HOSTED_URL, Config, ConfigError, hosted_url, load as load_config
from .core import ProjectorError
from .keychain import KeychainError

SERVICE = "projector-hosted"
TIMEOUT = 30.0
LOGIN = "run `project hosted login`"
# The plan sold by the seat, the one plan whose checkout names how many seats it buys.
TEAM = "team"
# The step's input that has the review post as the hosted service's app.
APP = "projector"


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


def call(url: str, method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None,
         ok: tuple[int, ...] = (200,)) -> tuple[int, dict]:
    """`method` on the service's `path`, with `body` as JSON and `token` as the bearer, answering the status and
    the JSON object of the reply. A status outside `ok` raises the refusal."""
    headers = {"accept": "application/json", "user-agent": "projector"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise HostedError(f"could not reach {url}: {reason}") from None
    try:
        answer = json.loads(raw) if raw else {}
    except ValueError:
        answer = {}
    if not isinstance(answer, dict):
        answer = {}
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
            if not isinstance(answer.get("token"), str) or not answer["token"]:
                raise HostedError(f"{url} signed you in but gave no account token")
            return answer
        interval = seconds(answer.get("interval"), interval)
    raise HostedError(f"the code expired before it was approved; {LOGIN} again")


def register_key(url: str, token: str, key: Any) -> str:
    """Register `key`'s public key to the account by opening the challenge the service seals to it, and answer
    the key's ID. The service seals from a fresh key of its own, so the sender proves nothing and is not read."""
    public = base64.b64encode(key.public).decode("ascii")
    challenge = call(url, "POST", "/v1/keys", {"public_key": public}, token)[1]
    try:
        _, opened = crypto.unseal(key.private, challenge.get("sealed"))
    except crypto.CryptoError as error:
        raise HostedError(f"the challenge {url} sealed to key {key.id} does not open: {error}") from None
    answer = call(url, "POST", "/v1/keys", {"public_key": public, "challenge_id": challenge.get("challenge_id"),
                                            "challenge": base64.b64encode(opened).decode("ascii")}, token,
                  ok=(200, 201))[1]
    return str(answer.get("key_id") or key.id)


def login(url: str, key: Any, no_key: str, say: Callable[[str], None]) -> dict:
    """Sign in, keep the account token in the keychain, and register `key`, or say `no_key` when there is none.
    Answers what was done."""
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
    return done


def logout(url: str) -> str:
    """Revoke the stored token and remove it from the keychain. A token the service no longer knows is removed
    too; one that could not be revoked for another reason is kept, so the command can run again."""
    status, _ = call(url, "POST", "/v1/logout", token=stored_token(url), ok=(200, 204, 401))
    revoked = "the service no longer knew the account token" if status == 401 else "revoked the account token"
    try:
        keychain.delete(url, service=SERVICE)
    except KeychainError as error:
        raise HostedError(f"{revoked}, but could not remove it from the keychain: {error}") from None
    return f"signed out of {url}: {revoked}, and removed it from the keychain"


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

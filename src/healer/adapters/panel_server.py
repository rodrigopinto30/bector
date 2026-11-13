"""The panel's web server: the built pages and a read-only JSON snapshot, for this computer
only. A plain ASGI application, run by uvicorn."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path, PurePosixPath
from typing import Any

from healer.domain.actions import ActionOutcome
from healer.domain.errors import HealerError
from healer.domain.panel import PanelSnapshot, RunView
from healer.domain.ports import PanelCommands

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
SNAPSHOT_PATH = "/api/snapshot"
RUNS_PATH = "/api/runs/"
SESSION_PATH = "/api/session"
ACTIONS_PATH = "/api/actions/"
TOKEN_HEADER = b"x-healer-token"
MAX_ACTION_BYTES = 4_096
_RUN_ID = re.compile(r"[0-9a-f]{16}")
_FIX_ID = re.compile(r"[0-9a-f]{64}")

_HOST = re.compile(r"(?P<name>\[[0-9a-f:.]+\]|[^:\[\]]+)(?::[0-9]{1,5})?")
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}
_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), interest-cohort=()"),
]
_STRICT_POLICY = "default-src 'none'; frame-ancestors 'none'"
_NOT_BUILT = (
    "The pages of the panel are not built. Run ./start.sh, which builds them into the image; "
    f"the data is at {SNAPSHOT_PATH}.\n"
)


class PanelApp:
    """Answers only GET and HEAD, and only to a Host of this computer: a page from any other
    site that resolves its own name to 127.0.0.1 (DNS rebinding) is refused. Each page may
    run only its own scripts, allowed by hash, and fetch only from this server.

    ``/api/runs/<id>`` gives one run step by step, asked for only when a page opens it, so
    the snapshot fetched every few seconds stays small.

    With ``actions``, a POST to ``/api/actions/...`` changes this node's own data (ADR
    0025). It must come from a page of this panel: its Origin must be this server, or one of
    ``allowed_origins``, it must be JSON, and it must carry the token that ``/api/session``
    gives only to such pages. A page of another site fails all three: browsers never let it
    read the token or send the token's header without this server's consent, which it
    never gives."""

    def __init__(
        self,
        snapshot: Callable[[], PanelSnapshot],
        pages: Path | None,
        *,
        run: Callable[[str], RunView | None] | None = None,
        actions: PanelCommands | None = None,
        allowed_hosts: frozenset[str] = LOCAL_HOSTS,
        allowed_origins: frozenset[str] = frozenset(),
    ) -> None:
        self._snapshot = snapshot
        self._run = run
        self._actions = actions
        self._pages = pages.resolve() if pages is not None and pages.is_dir() else None
        self._allowed = allowed_hosts
        self._origins = allowed_origins
        self._token = secrets.token_urlsafe(32)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        head = scope["method"] == "HEAD"
        if not self._local(scope):
            await _respond(send, 403, b"This panel only answers to localhost.\n", head=head)
            return
        path: str = scope["path"]
        if path.startswith(ACTIONS_PATH) and self._actions is not None:
            await self._act(scope, receive, send, path.removeprefix(ACTIONS_PATH))
            return
        if scope["method"] not in ("GET", "HEAD"):
            await _respond(
                send,
                405,
                b"The panel is read only.\n",
                head=head,
                extra=[(b"allow", b"GET, HEAD")],
            )
            return
        if path == SESSION_PATH:
            token = self._token if self._actions is not None else None
            body = json.dumps({"token": token, "actions": token is not None}).encode()
            await _respond(send, 200, body, "application/json", head=head)
            return
        if path == SNAPSHOT_PATH:
            body = await asyncio.to_thread(lambda: self._snapshot().model_dump_json().encode())
            await _respond(send, 200, body, "application/json", head=head)
            return
        if path.startswith(RUNS_PATH) and self._run is not None:
            await self._run_page(send, path.removeprefix(RUNS_PATH), head=head)
            return
        if path.startswith("/api/"):
            await _respond(send, 404, b'{"error":"not found"}', "application/json", head=head)
            return
        await self._page(send, path, head=head)

    async def _run_page(self, send: Send, identifier: str, *, head: bool) -> None:
        if not _RUN_ID.fullmatch(identifier):
            await _respond(send, 404, b'{"error":"not found"}', "application/json", head=head)
            return
        read = self._run
        assert read is not None
        try:
            found = await asyncio.to_thread(read, identifier)
        except HealerError as exc:
            body = json.dumps({"error": str(exc)}).encode()
            await _respond(send, 500, body, "application/json", head=head)
            return
        if found is None:
            body = b'{"error":"this run is no longer kept"}'
            await _respond(send, 404, body, "application/json", head=head)
            return
        await _respond(send, 200, found.model_dump_json().encode(), "application/json", head=head)

    async def _act(self, scope: Scope, receive: Receive, send: Send, name: str) -> None:
        if scope["method"] != "POST":
            await _json(send, 405, {"error": "an action is a POST"}, [(b"allow", b"POST")])
            return
        refused = self._refusal(scope)
        if refused is not None:
            status, reason = refused
            await _json(send, status, {"error": reason})
            return
        body = await _body(receive, MAX_ACTION_BYTES)
        if body is None:
            await _json(send, 413, {"error": f"an action is at most {MAX_ACTION_BYTES} bytes"})
            return
        try:
            request = json.loads(body or b"{}")
        except ValueError:
            request = None
        if not isinstance(request, dict):
            await _json(send, 400, {"error": "an action takes a JSON object"})
            return
        call = self._action(name, request)
        if call is None:
            await _json(send, 400, {"error": "unknown action or wrong fields"})
            return
        try:
            outcome = await asyncio.to_thread(call)
        except HealerError as exc:
            await _json(send, 500, {"error": str(exc)})
            return
        await _respond(send, 200, outcome.model_dump_json().encode(), "application/json")

    def _refusal(self, scope: Scope) -> tuple[int, str] | None:
        """Why a POST cannot be an action of this panel's page, or None if it can."""
        headers: dict[bytes, list[bytes]] = {}
        for header, value in scope["headers"]:
            headers.setdefault(header, []).append(value)
        host = headers[b"host"][0].decode("latin-1").strip().lower()
        origins = headers.get(b"origin", [])
        if len(origins) != 1:
            return 403, "an action must come from the panel's own page"
        origin = origins[0].decode("latin-1").strip().lower()
        if origin != f"http://{host}" and origin not in self._origins:
            return 403, "an action must come from the panel's own page"
        kinds = headers.get(b"content-type", [])
        if len(kinds) != 1 or kinds[0].split(b";")[0].strip().lower() != b"application/json":
            return 415, "an action must be sent as application/json"
        tokens = headers.get(TOKEN_HEADER, [])
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self._token.encode()):
            return 403, "an action needs the token of this panel's session"
        return None

    def _action(self, name: str, request: dict[str, Any]) -> Callable[[], ActionOutcome] | None:
        actions = self._actions
        assert actions is not None
        fields = set(request)
        fix = request.get("fix")
        topic = request.get("topic")
        if not isinstance(fix, str) or not _FIX_ID.fullmatch(fix):
            fix = None
        if name == "outbox/send" and not fields:
            return actions.send_outbox
        if (
            name == "outbox/discard"
            and fields == {"topic", "fix"}
            and fix is not None
            and isinstance(topic, str)
        ):
            fix_id, topic_name = fix, topic
            return lambda: actions.discard(topic_name, fix_id)
        if name == "team/forget" and fields == {"fix"} and fix is not None:
            forgotten = fix
            return lambda: actions.forget(forgotten)
        return None

    def _local(self, scope: Scope) -> bool:
        hosts = [value for name, value in scope["headers"] if name == b"host"]
        if len(hosts) != 1:
            return False
        match = _HOST.fullmatch(hosts[0].decode("latin-1").strip().lower())
        return match is not None and match.group("name") in self._allowed

    async def _page(self, send: Send, path: str, *, head: bool) -> None:
        if self._pages is None:
            await _respond(send, 503, _NOT_BUILT.encode(), head=head)
            return
        file = self._find(path)
        status = 200
        if file is None:
            status = 404
            file = self._find("/404")
            if file is None:
                await _respond(send, 404, b"Not found.\n", head=head)
                return
        body = await asyncio.to_thread(file.read_bytes)
        kind = _TYPES.get(file.suffix.lower(), "application/octet-stream")
        cache = (
            b"public, max-age=31536000, immutable"
            if path.startswith("/_next/static/") and status == 200
            else b"no-cache"
        )
        policy = page_policy(body) if file.suffix.lower() == ".html" else _STRICT_POLICY
        await _respond(send, status, body, kind, head=head, policy=policy, cache=cache)

    def _find(self, path: str) -> Path | None:
        """The built file for ``path``, never one outside the pages or a hidden one."""
        assert self._pages is not None
        relative = path.lstrip("/")
        if "\0" in relative or "\\" in relative:
            return None
        parts = PurePosixPath(relative).parts
        if any(part == ".." or part.startswith(".") for part in parts):
            return None
        names = (
            ["index.html"]
            if not parts
            else [relative, f"{relative}.html", f"{relative}/index.html"]
        )
        for name in names:
            candidate = (self._pages / name).resolve()
            if candidate.is_relative_to(self._pages) and candidate.is_file():
                return candidate
        return None


def page_policy(html: bytes) -> str:
    """The Content-Security-Policy of a built page: scripts from this server, plus the
    inline ones the page itself carries, each allowed by its SHA-256."""
    scripts = dict.fromkeys(
        match.group(1).encode() for match in _INLINE_SCRIPT.finditer(html.decode())
    )
    sources = ["'self'"] + [
        "'sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode() + "'"
        for script in scripts
    ]
    return (
        f"default-src 'none'; script-src {' '.join(sources)}; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )


async def _respond(
    send: Send,
    status: int,
    body: bytes,
    kind: str = "text/plain; charset=utf-8",
    *,
    head: bool = False,
    policy: str = _STRICT_POLICY,
    cache: bytes = b"no-store",
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers = [
        (b"content-type", kind.encode()),
        (b"content-length", str(len(body)).encode()),
        (b"content-security-policy", policy.encode()),
        (b"cache-control", cache),
        *_HEADERS,
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else body})


async def _json(
    send: Send,
    status: int,
    body: dict[str, str],
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    await _respond(send, status, json.dumps(body).encode(), "application/json", extra=extra)


async def _body(receive: Receive, limit: int) -> bytes | None:
    """The request's body, or None when it is longer than ``limit`` bytes."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return b"".join(chunks)
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return

"""The panel's web server: the built pages and a read-only JSON snapshot, for this computer
only. A plain ASGI application, run by uvicorn."""

import asyncio
import base64
import hashlib
import re
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path, PurePosixPath
from typing import Any

from healer.domain.panel import PanelSnapshot

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
SNAPSHOT_PATH = "/api/snapshot"

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
    run only its own scripts, allowed by hash, and fetch only from this server."""

    def __init__(
        self,
        snapshot: Callable[[], PanelSnapshot],
        pages: Path | None,
        *,
        allowed_hosts: frozenset[str] = LOCAL_HOSTS,
    ) -> None:
        self._snapshot = snapshot
        self._pages = pages.resolve() if pages is not None and pages.is_dir() else None
        self._allowed = allowed_hosts

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
        if scope["method"] not in ("GET", "HEAD"):
            await _respond(
                send,
                405,
                b"The panel is read only.\n",
                head=head,
                extra=[(b"allow", b"GET, HEAD")],
            )
            return
        path: str = scope["path"]
        if path == SNAPSHOT_PATH:
            body = await asyncio.to_thread(lambda: self._snapshot().model_dump_json().encode())
            await _respond(send, 200, body, "application/json", head=head)
            return
        if path.startswith("/api/"):
            await _respond(send, 404, b'{"error":"not found"}', "application/json", head=head)
            return
        await self._page(send, path, head=head)

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


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return

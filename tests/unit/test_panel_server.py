import asyncio
import base64
import hashlib
import json
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from healer.adapters.panel_server import PanelApp, page_policy
from healer.domain.actions import ActionOutcome
from healer.domain.errors import HistoryError
from healer.domain.ledger import LedgerSummary
from healer.domain.panel import PanelSnapshot, ResolutionCounts, RunView, Savings

RUN_FIXTURE = Path(__file__).parents[2] / "panel" / "src" / "lib" / "fixtures" / "run.json"

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
PAGE = (
    b'<!DOCTYPE html><html><head><script src="/_next/static/app.js"></script>'
    b"<script>self.__next_f.push([1])</script></head><body>"
    b'<script id="theme">document.documentElement.className="dark"</script></body></html>'
)


def snapshot() -> PanelSnapshot:
    return PanelSnapshot(
        taken_at=NOW,
        version="1.0.0",
        node="laptop-luis",
        topic="healer/solutions/v1",
        sharing=False,
        checks=[],
        listener=None,
        savings=Savings(
            total=LedgerSummary(
                key="total",
                incidents=0,
                fixed=0,
                from_cache=0,
                from_team=0,
                model_attempts=0,
                tokens_used=0,
                tokens_saved=0,
            ),
            resolutions=ResolutionCounts(),
            days=[],
        ),
        incidents=[],
        projects=[],
        team=[],
        outbox=[],
    )


@pytest.fixture
def pages(tmp_path: Path) -> Path:
    root = tmp_path / "out"
    (root / "_next" / "static").mkdir(parents=True)
    (root / "index.html").write_bytes(PAGE)
    (root / "404.html").write_bytes(b"<html><body>Not here</body></html>")
    (root / "index.txt").write_text("rsc payload")
    (root / "_next" / "static" / "app.js").write_text("console.log(1)")
    (root / ".hidden").write_text("secret")
    (tmp_path / "outside.txt").write_text("outside")
    (root / "escape.txt").symlink_to(tmp_path / "outside.txt")
    return root


Response = tuple[int, dict[str, str], bytes]


def call(
    app: PanelApp,
    path: str = "/",
    *,
    method: str = "GET",
    hosts: tuple[str, ...] = ("localhost:8765",),
    headers: tuple[tuple[str, str], ...] = (),
    body: tuple[bytes, ...] = (b"",),
) -> Response:
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"host", host.encode()) for host in hosts]
        + [(name.encode(), value.encode()) for name, value in headers],
    }
    sent: list[MutableMapping[str, Any]] = []
    chunks = list(body)

    async def receive() -> MutableMapping[str, Any]:
        chunk = chunks.pop(0) if chunks else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start, answer = sent
    answered = {name.decode(): value.decode() for name, value in start["headers"]}
    return start["status"], answered, answer["body"]


def test_the_snapshot_is_served_as_json(pages: Path) -> None:
    status, headers, body = call(PanelApp(snapshot, pages), "/api/snapshot")
    assert status == 200
    assert headers["content-type"] == "application/json"
    assert headers["cache-control"] == "no-store"
    assert json.loads(body)["node"] == "laptop-luis"


@pytest.mark.parametrize(
    "host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "LOCALHOST", "localhost"]
)
def test_this_computer_may_read_the_panel(pages: Path, host: str) -> None:
    status, _, _ = call(PanelApp(snapshot, pages), "/api/snapshot", hosts=(host,))
    assert status == 200


@pytest.mark.parametrize(
    "hosts",
    [
        ("evil.example:8765",),
        ("localhost.evil.example",),
        ("127.0.0.1.nip.io:8765",),
        ("[::1].evil:8765",),
        ("localhost:8765:1",),
        ("localhost:http",),
        ("[::1]x",),
        (),
        ("localhost:8765", "evil.example"),
    ],
)
def test_other_sites_cannot_read_it_even_through_dns_rebinding(
    pages: Path, hosts: tuple[str, ...]
) -> None:
    status, _, body = call(PanelApp(snapshot, pages), "/api/snapshot", hosts=hosts)
    assert status == 403
    assert b"only answers to localhost" in body


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_it_is_read_only(pages: Path, method: str) -> None:
    status, headers, _ = call(PanelApp(snapshot, pages), "/api/snapshot", method=method)
    assert status == 405
    assert headers["allow"] == "GET, HEAD"


def test_head_sends_the_headers_without_the_body(pages: Path) -> None:
    status, headers, body = call(PanelApp(snapshot, pages), "/", method="HEAD")
    assert status == 200
    assert int(headers["content-length"]) == len(PAGE)
    assert body == b""


def test_a_page_may_run_only_its_own_scripts(pages: Path) -> None:
    status, headers, body = call(PanelApp(snapshot, pages), "/")
    assert (status, body) == (200, PAGE)
    assert headers["content-type"] == "text/html; charset=utf-8"
    policy = headers["content-security-policy"]
    for inline in (b"self.__next_f.push([1])", b'document.documentElement.className="dark"'):
        digest = base64.b64encode(hashlib.sha256(inline).digest()).decode()
        assert f"'sha256-{digest}'" in policy
    assert "script-src 'self' 'sha256-" in policy
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_a_page_without_inline_scripts_allows_only_its_own_files() -> None:
    assert "script-src 'self';" in page_policy(b"<html><script src='/a.js'></script></html>")


def test_built_files_are_served_with_their_type_and_cache(pages: Path) -> None:
    app = PanelApp(snapshot, pages)
    status, headers, body = call(app, "/_next/static/app.js")
    assert (status, body) == (200, b"console.log(1)")
    assert headers["content-type"] == "text/javascript; charset=utf-8"
    assert headers["cache-control"] == "public, max-age=31536000, immutable"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    status, headers, _ = call(app, "/index.txt")
    assert status == 200
    assert headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    [
        "/../outside.txt",
        "/_next/../../outside.txt",
        "/escape.txt",
        "/.hidden",
        "/_next/static/..\\..\\outside.txt",
        "/index.html\0.js",
        "/missing",
    ],
)
def test_nothing_outside_the_built_pages_is_served(pages: Path, path: str) -> None:
    status, _, body = call(PanelApp(snapshot, pages), path)
    assert status == 404
    assert body == b"<html><body>Not here</body></html>"


def test_an_unknown_api_path_is_not_a_page(pages: Path) -> None:
    status, _, body = call(PanelApp(snapshot, pages), "/api/other")
    assert (status, body) == (404, b'{"error":"not found"}')


def test_without_built_pages_it_says_how_to_build_them(tmp_path: Path) -> None:
    app = PanelApp(snapshot, tmp_path / "not-built")
    status, _, body = call(app, "/")
    assert status == 503
    assert b"./start.sh" in body
    assert call(app, "/api/snapshot")[0] == 200
    assert call(PanelApp(snapshot, None), "/")[0] == 503


def test_it_answers_the_server_lifespan(pages: Path) -> None:
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return next(messages)

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    asyncio.run(PanelApp(snapshot, pages)({"type": "lifespan"}, receive, send))
    assert [m["type"] for m in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]


RUN = RunView.model_validate(json.loads(RUN_FIXTURE.read_text()))


def runs(identifier: str) -> RunView | None:
    if identifier == RUN.run_id:
        return RUN
    if identifier == "0" * 16:
        raise HistoryError("Cannot use the history at /data/history.sqlite3: disk I/O error")
    return None


def test_a_run_is_served_when_a_page_asks_for_it(pages: Path) -> None:
    app = PanelApp(snapshot, pages, run=runs)
    status, headers, body = call(app, f"/api/runs/{RUN.run_id}")
    assert (status, headers["content-type"]) == (200, "application/json")
    assert headers["cache-control"] == "no-store"
    assert RunView.model_validate_json(body) == RUN


@pytest.mark.parametrize(
    ("identifier", "status", "says"),
    [
        ("f" * 16, 404, "no longer kept"),
        ("0" * 16, 500, "disk I/O error"),
        ("not-an-id", 404, "not found"),
        ("F" * 16, 404, "not found"),
        ("f" * 17, 404, "not found"),
        (f"{'f' * 16}/../snapshot", 404, "not found"),
    ],
)
def test_a_run_that_cannot_be_served_says_why(
    pages: Path, identifier: str, status: int, says: str
) -> None:
    answered, headers, body = call(PanelApp(snapshot, pages, run=runs), f"/api/runs/{identifier}")
    assert (answered, headers["content-type"]) == (status, "application/json")
    assert says in json.loads(body)["error"]


def test_runs_are_not_found_without_a_history(pages: Path) -> None:
    status, _, _ = call(PanelApp(snapshot, pages), f"/api/runs/{RUN.run_id}")
    assert status == 404


class Commands:
    """The actions, recorded instead of done."""

    def __init__(self) -> None:
        self.done: list[tuple[str, ...]] = []

    def send_outbox(self) -> ActionOutcome:
        self.done.append(("send",))
        return ActionOutcome(ok=True, message="Sent 1 fix.")

    def discard(self, topic: str, fix: str) -> ActionOutcome:
        self.done.append(("discard", topic, fix))
        return ActionOutcome(ok=True, message="Discarded.")

    def forget(self, fix: str) -> ActionOutcome:
        if fix == "0" * 64:
            raise HistoryError("Cannot use the history at /data/history.sqlite3: locked")
        self.done.append(("forget", fix))
        return ActionOutcome(ok=True, message="Forgotten.")


def token_of(app: PanelApp) -> str:
    status, headers, body = call(app, "/api/session")
    assert (status, headers["cache-control"]) == (200, "no-store")
    token = json.loads(body)["token"]
    assert isinstance(token, str) and len(token) >= 32
    return token


def act(
    app: PanelApp,
    name: str,
    payload: object = None,
    *,
    origin: str | None = "http://localhost:8765",
    kind: str = "application/json",
    token: str | None = "",
    method: str = "POST",
    body: tuple[bytes, ...] | None = None,
) -> Response:
    headers: list[tuple[str, str]] = [("content-type", kind)]
    if origin is not None:
        headers.append(("origin", origin))
    if token == "":
        token = token_of(app)
    if token is not None:
        headers.append(("x-healer-token", token))
    data = (json.dumps(payload).encode(),) if payload is not None else (b"",)
    return call(
        app, f"/api/actions/{name}", method=method, headers=tuple(headers), body=body or data
    )


FIX = "f" * 64


def test_a_page_of_the_panel_can_send_discard_and_forget(pages: Path) -> None:
    commands = Commands()
    app = PanelApp(snapshot, pages, actions=commands)
    for name, payload in (
        ("outbox/send", None),
        ("outbox/discard", {"topic": "healer/solutions/v1", "fix": FIX}),
        ("team/forget", {"fix": FIX}),
    ):
        status, headers, body = act(app, name, payload)
        assert (status, headers["content-type"]) == (200, "application/json")
        assert json.loads(body)["ok"] is True
    assert commands.done == [("send",), ("discard", "healer/solutions/v1", FIX), ("forget", FIX)]


@pytest.mark.parametrize(
    ("changes", "status", "says"),
    [
        ({"origin": None}, 403, "panel's own page"),
        ({"origin": "https://evil.example"}, 403, "panel's own page"),
        ({"origin": "http://127.0.0.1:8765"}, 403, "panel's own page"),
        ({"kind": "text/plain"}, 415, "application/json"),
        ({"kind": "application/x-www-form-urlencoded"}, 415, "application/json"),
        ({"token": None}, 403, "token"),
        ({"token": "not-the-token"}, 403, "token"),
        ({"method": "GET"}, 405, "a POST"),
    ],
)
def test_an_action_that_does_not_come_from_the_panel_is_refused(
    pages: Path, changes: dict[str, Any], status: int, says: str
) -> None:
    commands = Commands()
    app = PanelApp(snapshot, pages, actions=commands)
    answered, _, body = act(app, "team/forget", {"fix": FIX}, **changes)
    assert answered == status
    assert says in json.loads(body)["error"]
    assert commands.done == []


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("team/forget", {"fix": "F" * 64}),
        ("team/forget", {"fix": FIX, "extra": 1}),
        ("team/forget", {}),
        ("outbox/discard", {"fix": FIX}),
        ("outbox/discard", {"topic": 3, "fix": FIX}),
        ("outbox/send", {"now": True}),
        ("outbox/apply", {"fix": FIX}),
        ("team/forget", ["not", "an", "object"]),
    ],
)
def test_an_unknown_action_or_wrong_fields_change_nothing(
    pages: Path, name: str, payload: object
) -> None:
    commands = Commands()
    status, _, body = act(PanelApp(snapshot, pages, actions=commands), name, payload)
    assert status == 400 and commands.done == []
    assert json.loads(body)["error"]


def test_a_body_that_is_not_json_or_too_long_is_refused(pages: Path) -> None:
    commands = Commands()
    app = PanelApp(snapshot, pages, actions=commands)
    assert act(app, "team/forget", body=(b"{not json",))[0] == 400
    big = (b'{"fix": "', b"f" * 5_000, b'"}')
    assert act(app, "team/forget", body=big)[0] == 413
    assert commands.done == []


def test_a_failing_store_answers_500_with_the_reason(pages: Path) -> None:
    status, _, body = act(
        PanelApp(snapshot, pages, actions=Commands()), "team/forget", {"fix": "0" * 64}
    )
    assert status == 500 and json.loads(body)["error"].endswith("locked")


def test_the_dev_server_may_take_actions_from_its_own_origin(pages: Path) -> None:
    app = PanelApp(
        snapshot,
        pages,
        actions=Commands(),
        allowed_origins=frozenset({"http://localhost:3000"}),
    )
    assert act(app, "outbox/send", origin="http://localhost:3000")[0] == 200


def test_without_actions_the_panel_only_reads(pages: Path) -> None:
    app = PanelApp(snapshot, pages)
    status, _, body = call(app, "/api/session")
    assert (status, json.loads(body)) == (200, {"token": None, "actions": False})
    answered, _, text = call(
        app,
        "/api/actions/outbox/send",
        method="POST",
        headers=(("origin", "http://localhost:8765"), ("content-type", "application/json")),
    )
    assert (answered, text) == (405, b"The panel is read only.\n")


def test_a_page_of_another_site_never_gets_the_token(pages: Path) -> None:
    status, _, _ = call(
        PanelApp(snapshot, pages, actions=Commands()), "/api/session", hosts=("evil.example",)
    )
    assert status == 403


def test_each_start_of_the_panel_has_its_own_token(pages: Path) -> None:
    first = token_of(PanelApp(snapshot, pages, actions=Commands()))
    assert first != token_of(PanelApp(snapshot, pages, actions=Commands()))

"""Events for the panel demo (./panel.sh demo), run in the cli container with the demo's data.

project   write the demo project, with its first bug, to /demo/project
teammate  a teammate, laptop-ana, shares the fix for the first bug, signed with the team key
forged    someone without the team key sends a fix
share     this node keeps its own fix for a second bug, as healer run does after Claude
          finds one, and shares it with the team
bug       the second bug shows up in the project
"""

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from healer.config import Settings
from healer.domain.diagnosis import Diagnosis
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import Evidence, SharedSolution, seal
from healer.domain.solution import Solution
from healer.factory import (
    build_publisher,
    build_sandbox_runner,
    build_solution_sharer,
    build_solutions,
)

PROJECT = Path("/demo/project")
FILES = {
    "app/__init__.py": "",
    "app/cfg.py": '''def read_db(config):
    """Return the database name from the configuration."""
    return config["db"]
''',
    "tests/test_cfg.py": """from app.cfg import read_db


def test_the_database_has_a_default():
    assert read_db({}) == "postgres"
""",
}
SECOND_BUG = {
    "app/net.py": '''def parse_port(address):
    """Return the port of a "host:port" address."""
    return int(address.split(":")[1])
''',
    "tests/test_net.py": """from app.net import parse_port


def test_an_address_without_a_port_uses_the_default():
    assert parse_port("localhost") == 5432
""",
}
TEAMMATE_FIX = Patch(
    edits=(
        FileEdit(
            path="app/cfg.py",
            old='    return config["db"]',
            new='    return config.get("db", "postgres")',
        ),
    )
)
OWN_FIX = Patch(
    edits=(
        FileEdit(
            path="app/net.py",
            old='    return int(address.split(":")[1])',
            new=('    _, _, port = address.partition(":")\n    return int(port) if port else 5432'),
        ),
    )
)


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def diagnose(root: Path, settings: Settings, error_type: str) -> Diagnosis:
    """The error of that type the tests of ``root`` show now, as healer run would see it."""
    report = build_sandbox_runner(root, settings).run_tests(settings.test_command)
    for diagnosis in report.diagnoses:
        if diagnosis.error.error_type == error_type:
            return diagnosis
    raise SystemExit(f"the demo project shows no {error_type}; run: project")


def teammate_fix(settings: Settings, node: str) -> SharedSolution:
    target = diagnose(PROJECT, settings, "KeyError")
    now = datetime.now(UTC)
    return SharedSolution(
        node=node,
        published_at=now,
        signature=target.signature,
        error=target.error.summary,
        patch=TEAMMATE_FIX,
        explanation="The configuration may not name the database: use postgres by default.",
        model="claude-sonnet-5-5",
        tokens_spent=2_900,
        evidence=Evidence(command=settings.test_command, exit_code_before=1, verified_at=now),
    )


def own_fix(settings: Settings) -> Solution:
    """The fix for the second bug, worked out on a copy that has it."""
    with tempfile.TemporaryDirectory(dir=PROJECT.parent) as folder:
        copy = Path(folder)
        write(copy, FILES)
        write(copy, SECOND_BUG)
        cfg = copy / "app" / "cfg.py"
        cfg.write_text(cfg.read_text().replace('config["db"]', 'config.get("db", "postgres")'))
        target = diagnose(copy, settings, "IndexError")
    return Solution(
        signature=target.signature,
        error=target.error.summary,
        patch=OWN_FIX,
        explanation="An address without a port falls back to the default port, 5432.",
        model="claude-sonnet-5-5",
        tokens_spent=4_100,
        command=settings.test_command,
        exit_code_before=1,
        verified_at=datetime.now(UTC),
    )


def main(command: str) -> None:
    settings = Settings()
    key = settings.share_key.get_secret_value().encode() if settings.share_key else b""
    if command == "project":
        write(PROJECT, FILES)
    elif command == "teammate":
        build_publisher(settings).publish(seal(teammate_fix(settings, "laptop-ana"), key))
    elif command == "forged":
        forged = seal(teammate_fix(settings, "intruder"), b"not-the-team-key-0123456789")
        build_publisher(settings).publish(forged)
    elif command == "share":
        solution = own_fix(settings)
        build_solutions(PROJECT, settings).save(solution)
        receipt = build_solution_sharer(settings).share(solution)
        print("shared" if receipt.delivered else f"waiting in the outbox: {receipt.failure}")
    elif command == "bug":
        write(PROJECT, SECOND_BUG)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")

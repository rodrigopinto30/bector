import json
import re
import tomllib
from pathlib import Path

from healer import __version__

ROOT = Path(__file__).parents[2]


def test_every_place_names_the_same_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    found = [project]
    for app in ("panel", "desktop"):
        found.append(json.loads((ROOT / app / "package.json").read_text())["version"])
        lock = json.loads((ROOT / app / "package-lock.json").read_text())
        found += [lock["version"], lock["packages"][""]["version"]]
    assert found == [__version__] * 7


def test_the_version_is_a_semantic_version() -> None:
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", __version__)


def test_the_changelog_describes_the_current_version() -> None:
    assert f"\n## [{__version__}] - " in (ROOT / "CHANGELOG.md").read_text()

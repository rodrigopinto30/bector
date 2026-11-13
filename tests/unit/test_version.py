import json
import re
import tomllib
from pathlib import Path

from healer import __version__

ROOT = Path(__file__).parents[2]


def test_every_place_names_the_same_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    panel = json.loads((ROOT / "panel" / "package.json").read_text())["version"]
    lock = json.loads((ROOT / "panel" / "package-lock.json").read_text())
    assert [project, panel, lock["version"], lock["packages"][""]["version"]] == [__version__] * 4


def test_the_version_is_a_semantic_version() -> None:
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", __version__)


def test_the_changelog_describes_the_current_version() -> None:
    assert f"\n## [{__version__}] - " in (ROOT / "CHANGELOG.md").read_text()

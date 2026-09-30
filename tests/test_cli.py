from typer.testing import CliRunner

from healer.cli import app


def test_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "healer" in result.stdout

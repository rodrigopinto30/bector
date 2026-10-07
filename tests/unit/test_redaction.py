"""Fake credentials are assembled at runtime so no file holds a complete, valid-looking key."""

from pathlib import Path

import pytest

from healer.domain.diagnosis import Diagnosis, StackFrame, TracedError
from healer.domain.models import Symbol, SymbolKind
from healer.domain.redaction import Redactor

REDACTOR = Redactor()
FIXTURES = Path(__file__).parent.parent / "fixtures"

ANTHROPIC = "sk-" + "ant-api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
OPENAI = "sk-" + "proj-" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK = "xox" + "b-" + "1234567890-abcdefABCDEF"
STRIPE = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
GOOGLE = "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
JWT = (
    "eyJ"
    + "hbGciOiJIUzI1NiJ9"
    + ".eyJ"
    + "zdWIiOiIxMjM0NTY3ODkwIn0"
    + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
RANDOM_KEY = "q7Wv2Lk9Xz4Rt8Bn3Mp6Hd1Fs5Gj0Ya"
PRIVATE_KEY = (
    "-----BEGIN "
    + "RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7bq\nabc123\n-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    ("text", "secret", "kind"),
    [
        (f"key = {ANTHROPIC}", ANTHROPIC, "anthropic_key"),
        (f"client = OpenAI(api_key_value={OPENAI!r})", OPENAI, "openai_key"),
        (f"aws {AWS} configured", AWS, "aws_access_key"),
        (f"git clone https://{GITHUB}@github.com/x/y", GITHUB, "github_token"),
        (f"SLACK={SLACK}", SLACK, "slack_token"),
        (f"stripe.api_key_set({STRIPE!r})", STRIPE, "stripe_key"),
        (f"maps?key={GOOGLE}", GOOGLE, "google_api_key"),
        (f"cookie: session={JWT}", JWT, "jwt"),
        (PRIVATE_KEY, "MIIEpAIBAAKCAQEA7bq", "private_key"),
    ],
)
def test_known_credential_formats(text: str, secret: str, kind: str) -> None:
    result = REDACTOR.redact(text)
    assert secret not in result.text
    assert f"[REDACTED:{kind}]" in result.text
    assert result.counts.get(kind, 0) >= 1


def test_private_key_without_end_marker_is_still_hidden() -> None:
    text = "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBg\nlog truncated here"
    result = REDACTOR.redact(text)
    assert "MIIEvQIBADANBg" not in result.text


@pytest.mark.parametrize(
    ("text", "secret", "kept"),
    [
        ("postgres://admin:hunter2@db:5432/app", "hunter2", "postgres://admin:"),
        ("redis://default:s3cr3t@cache:6379", "s3cr3t", "@cache:6379"),
        ("Authorization: Bearer abcdef123456xyz", "abcdef123456xyz", "Authorization: Bearer"),
        ('headers = {"Authorization": "Token 9f8e7d6c5b4a"}', "9f8e7d6c5b4a", "Authorization"),
        ("curl -H 'bearer abcdefghijklmnop123'", "abcdefghijklmnop123", "curl"),
    ],
)
def test_credentials_inside_urls_and_headers(text: str, secret: str, kept: str) -> None:
    result = REDACTOR.redact(text)
    assert secret not in result.text
    assert kept in result.text


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('password = "hunter2"', "hunter2"),
        ("DB_PASSWORD='p4ss w0rd'", "p4ss w0rd"),
        ('{"api_key": "abc-123"}', "abc-123"),
        ('client_secret: "xyz"', "xyz"),
        ('api_token: str = "tok_live"', "tok_live"),
        ("DB_PASSWORD=hunter2", "hunter2"),
        ("password: hunter2", "hunter2"),
        ("--auth-key=abcdef", "abcdef"),
        ("export GITHUB_TOKEN=plainvalue", "plainvalue"),
        ("secret = mysecretvalue", "mysecretvalue"),
        ('PASSPHRASE="x"', "x"),
    ],
)
def test_values_assigned_to_secret_names(text: str, secret: str) -> None:
    result = REDACTOR.redact(text)
    assert secret not in result.text
    assert "[REDACTED:secret_assignment]" in result.text


@pytest.mark.parametrize(
    "text",
    [
        "token = None",
        "password: str",
        "anthropic_api_key: SecretStr | None = None",
        "api_key = settings.api_key",
        "token = tokenize(text)",
        "password = os.environ['DB_PASSWORD']",
        "secret = tmp_path / 'secret.txt'",
        "TOKEN=${GITHUB_TOKEN}",
        "if token == expected:",
        "tokens = [t for t in text.split()]",
    ],
)
def test_code_that_only_mentions_secrets_is_kept(text: str) -> None:
    assert REDACTOR.redact(text).text == text


def test_random_looking_strings_are_hidden() -> None:
    result = REDACTOR.redact(
        f"value = '{RANDOM_KEY}' and hash 11f6ad8ec52a2984abaafd7c3b516503785c2072"
    )
    assert RANDOM_KEY not in result.text
    assert "11f6ad8ec52a2984abaafd7c3b516503785c2072" not in result.text
    assert result.counts["high_entropy"] == 2


@pytest.mark.parametrize(
    "text",
    [
        "test_symlink_escaping_the_workspace_is_rejected",
        "python3_11_site_packages_compat_layer_v2",
        "/usr/local/lib/python3.11/site-packages/_pytest/assertion/rewrite.py",
        "8043bfed-0f1b-44af-a446-c98fc6c540a3",
        "platform linux -- Python 3.11.16, pytest-9.1.1, pluggy-1.6.0",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
    ],
)
def test_identifiers_paths_and_uuids_are_not_random(text: str) -> None:
    assert REDACTOR.redact(text).text == text


@pytest.mark.parametrize("trace", sorted((FIXTURES / "traces").glob("*.txt")), ids=lambda p: p.name)
def test_real_tracebacks_are_left_untouched(trace: Path) -> None:
    text = trace.read_text()
    assert REDACTOR.redact(text).text == text


def test_only_the_random_value_is_hidden_not_its_name() -> None:
    result = REDACTOR.redact(f"DEBUG session={RANDOM_KEY} user=42")
    assert result.text == "DEBUG session=[REDACTED:high_entropy] user=42"


def test_base64_padding_is_part_of_the_random_value() -> None:
    result = REDACTOR.redact(f"blob {RANDOM_KEY}== end")
    assert result.text == "blob [REDACTED:high_entropy] end"


def test_placeholders_are_never_rewritten_by_later_rules() -> None:
    result = REDACTOR.redact(f"git clone https://{GITHUB}@github.com/x/y")
    assert result.text == "git clone https://[REDACTED:github_token]@github.com/x/y"
    assert result.counts == {"github_token": 1}


def test_redacting_twice_changes_nothing() -> None:
    once = REDACTOR.redact(f'password = "{ANTHROPIC}"\nurl = postgres://u:pw@h/db')
    twice = REDACTOR.redact(once.text)
    assert twice.text == once.text
    assert twice.total == 0


def test_counts_never_contain_the_values() -> None:
    result = REDACTOR.redact(f"{ANTHROPIC} {AWS}")
    assert result.counts == {"anthropic_key": 1, "aws_access_key": 1}
    assert ANTHROPIC not in str(result.counts)


def test_clean_text_is_unchanged_and_counts_are_empty() -> None:
    result = REDACTOR.redact("def add(a, b):\n    return a + b\n")
    assert result.text == "def add(a, b):\n    return a + b\n"
    assert (result.total, result.counts) == (0, {})


def test_diagnosis_free_text_fields_are_redacted() -> None:
    frame = StackFrame(
        file="/w/app/db.py",
        line=3,
        function="connect",
        code=f'connect("postgres://app:hunter2@db/x", key="{ANTHROPIC}")',
        workspace_file="app/db.py",
    )
    diagnosis = Diagnosis(
        error=TracedError(
            error_type="OperationalError",
            message="could not connect to postgres://app:hunter2@db/x",
            frames=(frame,),
            causes=(f"ValueError: bad token {AWS}",),
        ),
        origin=frame,
        location=Symbol(
            file="app/db.py",
            kind=SymbolKind.FUNCTION,
            name="connect",
            qualified_name="connect",
            start_line=1,
            end_line=3,
            source=f'def connect():\n    PASSWORD = "hunter2"\n    KEY = "{ANTHROPIC}"\n',
        ),
        signature="0" * 64,
        signature_basis="OperationalError|could not connect to <path>|app/db.py:connect",
    )
    redacted, counts = REDACTOR.redact_diagnosis(diagnosis)
    dumped = redacted.model_dump_json()
    for secret in ("hunter2", ANTHROPIC, AWS):
        assert secret not in dumped
    assert redacted.signature == diagnosis.signature
    assert redacted.origin is not None and redacted.origin.workspace_file == "app/db.py"
    assert counts["anthropic_key"] == 3
    assert counts["url_credentials"] == 3
    assert sum(counts.values()) >= 7

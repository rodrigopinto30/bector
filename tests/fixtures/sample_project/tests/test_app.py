from app.cfg import parse_port, read_db


def helper():
    return read_db({})


def test_key():
    helper()


def test_assert():
    assert parse_port("2") == 3


def test_ok():
    assert True

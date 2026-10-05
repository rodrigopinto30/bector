def read_db(config):
    return config["db"]


def load(path):
    try:
        return open(path).read()
    except FileNotFoundError as exc:
        raise RuntimeError(f"cannot load {path}") from exc


def parse_port(value):
    return int(value)

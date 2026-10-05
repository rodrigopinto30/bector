import json
import sys

from app.cfg import load, parse_port, read_db

mode = sys.argv[1]
if mode == "key":
    read_db({"host": "x"})
elif mode == "chain":
    load("/nope/settings.toml")
elif mode == "lib":
    json.loads("{bad")
elif mode == "handling":
    try:
        read_db({})
    except KeyError:
        parse_port("abc")

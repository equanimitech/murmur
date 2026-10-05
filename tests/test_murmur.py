import json
from datetime import timedelta

from murmur import cd_to_unix, decide, tsrp_text

RUNS = ["Hello", {"timeRange": [0, 1]}, " world", {"timeRange": [1, 2]}]


def atom(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return b"\x00" * 64 + (8 + len(body)).to_bytes(4, "big") + b"tsrp" + body + b"\x00" * 16


def test_tsrp_flat_list():
    assert tsrp_text(atom({"attributedString": RUNS, "locale": {"identifier": "en_US"}})) == "Hello world"


def test_tsrp_runs_dict():
    assert tsrp_text(atom({"attributedString": {"runs": RUNS, "attributeTable": [{}]}})) == "Hello world"


def test_tsrp_absent_or_garbage():
    assert tsrp_text(b"\x00" * 128) is None
    assert tsrp_text(b"\x00\x00\x00\x20tsrpnot json") is None


def test_core_data_epoch():
    assert cd_to_unix(0) == 978307200  # 2001-01-01T00:00:00Z


def test_decide():
    old, young = timedelta(days=3), timedelta(hours=5)
    assert decide(True, "en", old) == "apple"
    assert decide(True, "pt", young) == "whisper"
    assert decide(False, None, young) is None
    assert decide(False, None, old) == "whisper"
    assert decide(False, None, None) == "whisper"  # transcribe: no age rule

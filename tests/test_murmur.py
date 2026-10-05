import json
from datetime import timedelta

import pytest

from murmur import APP_SUPPORT, MODEL_FILE, Evicted, cd_to_unix, decide, download, find_model, own_model, transcribe, tsrp_text

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


def test_find_model_order(tmp_path):
    assert find_model(tmp_path, None) is None
    vibe = tmp_path / APP_SUPPORT / "github.com.thewh1teagle.vibe" / MODEL_FILE
    vibe.parent.mkdir(parents=True)
    vibe.touch()
    assert find_model(tmp_path, None) == vibe
    own = own_model(tmp_path)
    own.parent.mkdir(parents=True)
    own.touch()
    assert find_model(tmp_path, None) == own  # own dir before Vibe
    env = tmp_path / "custom.bin"
    env.touch()
    assert find_model(tmp_path, str(env)) == env  # env var wins
    assert find_model(tmp_path, str(tmp_path / "nope.bin")) is None  # set env is authoritative


def test_download_part_then_rename(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 3000)
    dest = tmp_path / "models" / MODEL_FILE
    download(src.as_uri(), dest)
    assert dest.read_bytes() == src.read_bytes()
    assert not dest.with_name(dest.name + ".part").exists()
    with pytest.raises(OSError):
        download((tmp_path / "missing.bin").as_uri(), tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


def test_transcribe_evicted_raises(tmp_path):
    with pytest.raises(Evicted):
        transcribe(tmp_path / "gone.m4a")

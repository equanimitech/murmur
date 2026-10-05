"""murmur: Apple Voice Memos -> markdown transcripts."""

import argparse
import fcntl
import json
import mmap
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

REC = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
MODEL_FILE = "ggml-large-v3-turbo.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_FILE}"
APP_SUPPORT = "Library/Application Support"
CORE_DATA_EPOCH = 978307200
FRESH = timedelta(days=2)  # Apple writes tsrp late; wait this long before falling back to whisper
for _d in ("/usr/local/bin", "/opt/homebrew/bin"):  # unattended runs (launchd) lack brew on PATH
    if Path(_d).is_dir() and _d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")


def own_model(home: Path = Path.home()) -> Path:
    return home / APP_SUPPORT / "murmur" / MODEL_FILE


def find_model(home: Path = Path.home(), env: str | None = os.environ.get("MURMUR_MODEL")) -> Path | None:
    """$MURMUR_MODEL (authoritative when set) -> murmur's own dir -> Vibe's copy, if one happens to exist."""
    if env:
        return p if (p := Path(env).expanduser()).exists() else None
    vibe = home / APP_SUPPORT / "github.com.thewh1teagle.vibe" / MODEL_FILE
    return next((p for p in (own_model(home), vibe) if p.exists()), None)


def model() -> str:
    """Never downloads: sync runs unattended. A missing model means `murmur setup` hasn't run."""
    if not (m := find_model()):
        sys.exit("murmur: no whisper model found; run `murmur setup` (or set MURMUR_MODEL)")
    return str(m)


def download(url: str, dest: Path) -> None:
    """Stream to dest.part, rename on completion so dest is never a truncated model."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url) as r, open(part, "wb") as f:
        total, done, shown = int(r.headers.get("Content-Length") or 0), 0, 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total and done * 10 // total > shown:
                shown = done * 10 // total
                print(f"  {shown * 10}%  {done >> 20} MB", flush=True)
    if total and done != total:
        raise OSError(f"short download: {done} of {total} bytes")
    os.replace(part, dest)


def setup() -> int:
    """Idempotent: check tools, fetch the model only if none resolves."""
    brew = {"ffmpeg": "ffmpeg", "whisper-cli": "whisper-cpp"}
    missing = [t for t in brew if not shutil.which(t)]
    for t in missing:
        print(f"missing  {t}  ->  brew install {brew[t]}")
    if m := find_model():
        print(f"model    {m}")
    elif env := os.environ.get("MURMUR_MODEL"):
        print(f"model    MURMUR_MODEL={env} does not exist; fix it or unset it")
        return 1
    else:
        dest = own_model()
        print(f"downloading {MODEL_URL} (~1.6 GB) -> {dest}")
        download(MODEL_URL, dest)
        print(f"model    {dest}")
    return 1 if missing else 0


def cd_to_unix(ts: float) -> float:
    return ts + CORE_DATA_EPOCH


def memos() -> list[dict]:
    db = sqlite3.connect(f"file:{REC / 'CloudRecordings.db'}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT ZUNIQUEID, ZDATE, ZDURATION, ZENCRYPTEDTITLE, ZCUSTOMLABEL, ZPATH "
        "FROM ZCLOUDRECORDING WHERE ZPATH IS NOT NULL ORDER BY ZDATE DESC"
    ).fetchall()
    db.close()
    return [
        dict(id=i, date=datetime.fromtimestamp(cd_to_unix(d)).astimezone(), duration=round(s or 0),
             title=t or l or "", path=REC / p)
        for i, d, s, t, l, p in rows
    ]


def tsrp_text(data) -> str | None:
    """Apple's transcript: MP4 atom `tsrp` (4-byte BE size before the tag, JSON payload), near file end."""
    i = data.rfind(b"tsrp")
    if i < 4:
        return None
    try:
        size = int.from_bytes(data[i - 4 : i], "big")
        a = json.loads(data[i + 4 : i - 4 + size])["attributedString"]
    except (ValueError, KeyError, TypeError):
        return None
    runs = a["runs"] if isinstance(a, dict) else a
    return "".join(r for r in runs if isinstance(r, str)).strip() or None


def apple_transcript(path: Path) -> str | None:
    if not path.exists() or not path.stat().st_size:
        return None
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        return tsrp_text(m)


def decide(has_tsrp: bool, lang: str | None, age: timedelta | None) -> str | None:
    """apple | whisper | None (wait: Apple may still write a transcript)."""
    if has_tsrp:
        return "apple" if lang == "en" else "whisper"
    return None if age is not None and age < FRESH else "whisper"


@contextmanager
def to_wav(src: Path):
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(src),
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], capture_output=True, text=True, check=True)
        yield wav


def whisper(mdl: str, wav: Path, *args: str) -> tuple[str, str | None]:
    p = subprocess.run(["whisper-cli", "-m", mdl, "-f", str(wav), *args],
                       capture_output=True, text=True, check=True)
    m = re.search(r"auto-detected language: (\w+)", p.stderr)
    return "\n".join(l.strip() for l in p.stdout.splitlines() if l.strip()), m and m.group(1)


class Evicted(Exception):
    """Memo audio is in iCloud, not on disk."""


def transcribe(path: Path, age: timedelta | None = None) -> tuple[str, str, str | None] | None:
    """-> (text, source, language), or None to wait."""
    if not path.exists():
        raise Evicted(path)
    apple = apple_transcript(path)
    if decide(bool(apple), None, age) is None:
        return None
    mdl = model()  # every memo with work to do needs whisper, if only for the language gate
    with to_wav(path) as wav:
        lang = whisper(mdl, wav, "-l", "auto", "-dl")[1] if apple else None
        if decide(bool(apple), lang, age) == "apple":
            return apple, "apple", lang
        text, lang = whisper(mdl, wav, "-l", "auto", "-nt")
        return text, "whisper", lang


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "memo"


def render(m: dict, text: str, source: str, lang: str | None) -> str:
    return (f"---\nid: {m['id']}\ndate: {m['date'].isoformat(timespec='seconds')}\n"
            f"duration: {m['duration']}\ntitle: {json.dumps(m['title'])}\n"
            f"source: {source}\nlanguage: {lang or 'unknown'}\n---\n\n{text}\n")


def written_ids(out: Path) -> set[str]:
    return {m.group(1) for f in out.glob("*.md") if (m := re.search(r"^id: (\S+)", f.read_text(), re.M))}


def sync(out: Path, since: date | None, force: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    lock = open(out / ".murmur.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another sync is running
    done = set() if force else written_ids(out)
    now, failed = datetime.now().astimezone(), 0
    for m in memos():
        if (since and m["date"].date() < since) or m["id"] in done:
            continue
        try:
            r = transcribe(m["path"], now - m["date"])
        except Evicted:
            print(f"evicted  {m['date']:%Y-%m-%d %H:%M}  {m['title']}  (audio in iCloud)")
            continue
        except subprocess.CalledProcessError as e:
            failed += 1
            print(f"failed   {m['date']:%Y-%m-%d %H:%M}  {m['title']}  ({e.cmd[0]} exit {e.returncode})")
            continue
        if r is None:
            continue  # young, no tsrp yet: Apple may still write one
        dest = out / f"{m['date']:%Y-%m-%d-%H%M}-{slug(m['title'])}.md"
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(render(m, *r))
        os.replace(tmp, dest)
        print(f"{r[1]:<8} {m['date']:%Y-%m-%d %H:%M}  {r[2] or '?'}  {dest.name}")
    return 1 if failed else 0


def resolve(arg: str) -> Path:
    for p in (Path(arg).expanduser(), REC / arg):
        if p.exists():
            return p
    hit = next((m for m in memos() if m["id"] == arg), None)
    if not hit:
        sys.exit(f"murmur: no memo or file '{arg}'")
    return hit["path"]


def main() -> None:
    ap = argparse.ArgumentParser(prog="murmur", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int)
    ls.add_argument("--json", action="store_true")
    sy = sub.add_parser("sync")
    sy.add_argument("--out", type=Path, default=Path.home() / "Documents/murmur")
    sy.add_argument("--since", type=date.fromisoformat)
    sy.add_argument("--force", action="store_true")
    tr = sub.add_parser("transcribe")
    tr.add_argument("target", help="memo id, filename, or path")
    sub.add_parser("setup", help="check ffmpeg/whisper-cli, download the model if none is found")
    a = ap.parse_args()
    try:
        run(a)
    except Evicted:
        sys.exit("murmur: audio not on disk (in iCloud) — open it in Voice Memos to download")
    except subprocess.CalledProcessError as e:
        why = (e.stderr or "").strip().splitlines()[-1:]
        sys.exit(f"murmur: {e.cmd[0]} failed (exit {e.returncode})" + "".join(f": {w}" for w in why))
    except FileNotFoundError as e:  # ffmpeg / whisper-cli not on PATH
        sys.exit(f"murmur: {e.filename or e} not found; run `murmur setup`")


def run(a: argparse.Namespace) -> None:
    if a.cmd == "list":
        rows = [dict(date=m["date"].isoformat(timespec="seconds"), duration=m["duration"], title=m["title"],
                     id=m["id"], has_apple_transcript=bool(apple_transcript(m["path"])))
                for m in memos()[: a.limit]]
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        for r in [] if a.json else rows:
            print(f"{r['date'][:16]}  {r['duration']:>5}s  {'T' if r['has_apple_transcript'] else '-'}  "
                  f"{r['title']:<24}  {r['id']}")
    elif a.cmd == "setup":
        sys.exit(setup())
    elif a.cmd == "sync":
        sys.exit(sync(a.out.expanduser(), a.since, a.force))
    else:
        text, source, lang = transcribe(resolve(a.target))
        print(f"source={source} language={lang}", file=sys.stderr)
        print(text)


if __name__ == "__main__":
    main()

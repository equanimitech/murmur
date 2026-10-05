"""murmur: Apple Voice Memos -> markdown transcripts."""

import argparse
import fcntl
import json
import mmap
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

REC = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
MODEL = os.environ.get(
    "MURMUR_MODEL",
    str(Path.home() / "Library/Application Support/github.com.thewh1teagle.vibe/ggml-large-v3-turbo.bin"),
)
CORE_DATA_EPOCH = 978307200
FRESH = timedelta(days=2)  # Apple writes tsrp late; wait this long before falling back to whisper
os.environ["PATH"] += ":/opt/homebrew/bin"  # unattended runs (launchd) lack brew on PATH


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
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
        yield wav


def whisper(wav: Path, *args: str) -> tuple[str, str | None]:
    p = subprocess.run(["whisper-cli", "-m", MODEL, "-f", str(wav), *args],
                       capture_output=True, text=True, check=True)
    m = re.search(r"auto-detected language: (\w+)", p.stderr)
    return "\n".join(l.strip() for l in p.stdout.splitlines() if l.strip()), m and m.group(1)


def transcribe(path: Path, age: timedelta | None = None) -> tuple[str, str, str | None] | None:
    """-> (text, source, language), or None to wait."""
    apple = apple_transcript(path)
    if decide(bool(apple), None, age) is None:
        return None
    with to_wav(path) as wav:
        lang = whisper(wav, "-l", "auto", "-dl")[1] if apple else None
        if decide(bool(apple), lang, age) == "apple":
            return apple, "apple", lang
        text, lang = whisper(wav, "-l", "auto", "-nt")
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
        if not m["path"].exists():
            print(f"evicted  {m['date']:%Y-%m-%d %H:%M}  {m['title']}  (audio in iCloud)")
            continue
        try:
            r = transcribe(m["path"], now - m["date"])
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
    a = ap.parse_args()

    if a.cmd == "list":
        rows = [dict(date=m["date"].isoformat(timespec="seconds"), duration=m["duration"], title=m["title"],
                     id=m["id"], has_apple_transcript=bool(apple_transcript(m["path"])))
                for m in memos()[: a.limit]]
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        for r in [] if a.json else rows:
            print(f"{r['date'][:16]}  {r['duration']:>5}s  {'T' if r['has_apple_transcript'] else '-'}  "
                  f"{r['title']:<24}  {r['id']}")
    elif a.cmd == "sync":
        sys.exit(sync(a.out.expanduser(), a.since, a.force))
    else:
        text, source, lang = transcribe(resolve(a.target))
        print(f"source={source} language={lang}", file=sys.stderr)
        print(text)


if __name__ == "__main__":
    main()

# murmur

Turns Apple Voice Memos (macOS) into markdown transcripts. Local only, stdlib only.

Uses Apple's own transcript when it exists and the memo is English; otherwise transcribes with whisper.cpp.

## Install

```sh
brew install whisper-cpp ffmpeg
uv tool install .
```

Model defaults to `~/Library/Application Support/github.com.thewh1teagle.vibe/ggml-large-v3-turbo.bin`. Override with `MURMUR_MODEL=/path/to/ggml-model.bin`.

## Usage

```sh
murmur list [--limit N] [--json]           # date, duration, title, id, has Apple transcript
murmur sync [--out DIR] [--since YYYY-MM-DD] [--force]
murmur transcribe <id | filename | path>    # one transcript to stdout
```

`sync` writes `DIR/YYYY-MM-DD-HHMM-<slug>.md` (default `~/Documents/murmur`) with frontmatter: `id`, `date`, `duration`, `title`, `source` (apple|whisper), `language`. It skips memos already written (unless `--force`), skips audio evicted to iCloud, and holds a lock in `DIR`, so a file watch can fire it repeatedly.

## Caveats

- **Apple transcripts are English-only.** Apple forces an English locale, so PT/FR memos get garbage text. murmur trusts Apple's transcript only when whisper detects `en`.
- Apple writes its transcript hours or a day after recording. Memos under 2 days old with no Apple transcript are left for a later sync.
- Voice Memos data sits in a protected Group Container. The process running murmur needs **Full Disk Access**: your terminal, or the host process (launchd agent, file-watch daemon) for unattended runs.

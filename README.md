# murmur

Turns Apple Voice Memos (macOS) into markdown transcripts. Local only, stdlib only.

Uses Apple's own transcript when it exists and the memo is English; otherwise transcribes with whisper.cpp.

## Install

```sh
brew install whisper-cpp ffmpeg
uv tool install git+https://github.com/equanimitech/murmur
murmur setup
```

`murmur setup` checks for `ffmpeg` and `whisper-cli`, then finds a whisper model or downloads `ggml-large-v3-turbo.bin` (~1.6 GB) into `~/Library/Application Support/murmur/`. It is idempotent. Model lookup order: `$MURMUR_MODEL` (used as-is when set), murmur's own folder, then a copy Vibe already downloaded, if any. `sync` and `transcribe` never download. With no model they exit non-zero and point to `murmur setup`.

## Usage

```sh
murmur list [--limit N] [--json]           # date, duration, title, id, has Apple transcript
murmur sync [--out DIR] [--since YYYY-MM-DD] [--force]
murmur transcribe <id | filename | path>    # one transcript to stdout
murmur setup                                # check tools, fetch the model if missing
```

`sync` writes `DIR/YYYY-MM-DD-HHMM-<slug>.md` (default `~/Documents/murmur`) with frontmatter: `id`, `date`, `duration`, `title`, `source` (apple|whisper), `language`. It skips memos already written (unless `--force`), skips audio evicted to iCloud, and holds a lock in `DIR`, so a file watch can fire it repeatedly.

## Caveats

- **Apple transcripts are English-only.** Apple forces an English locale, so PT/FR memos get garbage text. murmur trusts Apple's transcript only when whisper detects `en`.
- Apple writes its transcript hours or a day after recording. Memos under 2 days old with no Apple transcript are left for a later sync.
- Voice Memos data sits in a protected Group Container. The process running murmur needs **Full Disk Access**: your terminal for manual runs, the host process for unattended ones. For zenborg that's the `zenborg-daemon` binary inside `zenborg.app` (System Settings → Privacy & Security → Full Disk Access).

## Unattended sync (zenborg)

A `jobs.json` entry that runs `murmur sync` five minutes after Voice Memos stops writing:

```json
{
  "voicememos": {
    "id": "voicememos",
    "name": "voicememos",
    "enabled": true,
    "program": "murmur",
    "args": ["sync"],
    "env": { "PATH": "~/.local/bin:/opt/homebrew/bin:/usr/bin:/bin" },
    "trigger": {
      "kind": "watch",
      "paths": [
        "~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/CloudRecordings.db",
        "~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/CloudRecordings.db-wal"
      ],
      "debounceSeconds": 300
    }
  }
}
```

If your zenborg version doesn't expand `~` or look up bare program names on `PATH`, write the absolute paths instead.

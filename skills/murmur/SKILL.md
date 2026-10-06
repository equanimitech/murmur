---
name: murmur
description: This skill should be used when the user asks to "sync my voice memos", "transcribe my voice memos", "review my memos", "what did I record", or types "/murmur". Runs the murmur CLI to turn Apple Voice Memos into markdown transcripts, then reviews the unreviewed ones (gist, decisions, action items, ideas, people) and marks them reviewed on confirmation.
---

# murmur: sync and review voice memos

Transcripts are personal. Keep their content in this session: send nothing from them to an external service (task app, web search, another model) without the user's explicit consent for that specific send.

## 1. Check the CLI

Run `which murmur`. If absent, stop and point to the install steps in the murmur README (https://github.com/equanimitech/murmur#install): `brew install whisper-cpp ffmpeg`, `uv tool install git+https://github.com/equanimitech/murmur`, `murmur setup`.

## 2. Sync

Run `murmur sync` (add `--out DIR` if the user keeps transcripts elsewhere; default `~/Documents/murmur`). Never pass `--force`: it rewrites every file and drops `reviewed:` marks.

Tell the user it can take minutes when Whisper runs. Read the output by its first word:

| Output | Meaning and next step |
| --- | --- |
| `apple` / `whisper` line | New transcript written. |
| nothing, exit 0 | Nothing new, or another sync (often the background job) holds the lock. Both are fine. |
| `evicted …` | Audio lives in iCloud. Open that memo in Voice Memos to download it, then sync again. Not an error. |
| `failed …` (exit 1) | That memo failed; the rest synced. Report the line. |
| `no whisper model found` or `… not found; run murmur setup` | Run `murmur setup`. |
| `can't open … CloudRecordings.db` | The terminal needs Full Disk Access (System Settings → Privacy & Security). |

## 3. Find unreviewed transcripts

Unreviewed means the frontmatter has no `reviewed:` field. Filenames start with `YYYY-MM-DD-HHMM`, so reverse sort is newest first:

```sh
grep -L '^reviewed:' ~/Documents/murmur/*.md | sort -r
```

Report the count and offer to narrow by date (glob the prefix, e.g. `2026-10-*.md`). Review newest first.

## 4. Review each transcript

Read the file. Frontmatter carries `id`, `date`, `duration`, `title`, `source` (apple|whisper), `language`. Write, in the transcript's own language unless the user asks otherwise:

- **Gist**: one or two lines.
- **Decisions**, **Action items**, **Ideas**, **People mentioned**: omit empty headings.

Quote sparingly: a short phrase only where the exact wording matters.

When `source: apple` and the text reads as garbled (Apple transcribes in English only, so non-English speech comes out as nonsense), say so. Offer `murmur transcribe <id>` for a fresh transcript on stdout, or deleting the file and re-running `murmur sync`. Both re-run the language gate: if Whisper detects English they keep Apple's text, so the fix only takes when the speech is detected as non-English.

## 5. Hand off action items (optional)

Offer to send the action items to whatever task or notes system this session can reach (an MCP server, a CLI, a file the user names). Send only after the user says yes, and send the items, not transcript text.

## 6. Mark reviewed

After the user confirms a transcript is done, add `reviewed: YYYY-MM-DD` (today) as the last frontmatter line, just before the closing `---`. Change nothing else in the file. This is safe for sync: it matches already-written memos on the `id:` line, so a reviewed file is still skipped.

# x2md

**Convert an X (Twitter) URL into clean markdown — including tweets, threads, photos, videos, long-form X Articles, and the top replies.**

```
$ x2md https://x.com/yarotheslav/status/2015892349688111187
---
title: "Tweet by Yaroslav Shmarov"
author: "Yaroslav Shmarov (@yarotheslav)"
source_tweet: "https://x.com/yarotheslav/status/2015892349688111187"
published: "Mon Jan 26 20:59:24 +0000 2026"
saved_via: "x2md (api.fxtwitter.com)"
saved_on: "2026-05-25"
---

# Tweet by Yaroslav Shmarov

> Saved from [@yarotheslav](https://x.com/yarotheslav/status/2015892349688111187).

💩 nginx
🥲 localtunnel
🤩 Cloudflare tunnels

...
```

No login, no API key, no rate limits to worry about. One file. Python stdlib plus `curl`.

## Why this exists

X content is increasingly hard to read without an account, hard to archive, and hard to feed into LLMs. The official API is paid and restrictive. Mirrors come and go. `x2md` quietly stitches together two unauthenticated sources — [FxTwitter](https://github.com/FixTweet/FxTwitter) for the tweet body / media / X Article content, and Nitter for the conversation — and produces a single self-contained markdown blob you (or your LLM) can read, search, and store.

## Install

**With [pipx](https://pipx.pypa.io) (recommended):**

```bash
pipx install git+https://github.com/yshmarov/x2md
```

**With pip:**

```bash
pip install git+https://github.com/yshmarov/x2md
```

**No-install (single file, just clone or curl it down):**

```bash
curl -O https://raw.githubusercontent.com/yshmarov/x2md/main/x2md.py
chmod +x x2md.py
./x2md.py <url>
```

Requirements: Python 3.9+ and `curl` on PATH (curl is shelled out for the replies fetch because Nitter sits behind Anubis proof-of-work bot challenges that block Python's urllib but pass curl's TLS fingerprint).

## Usage

```bash
x2md <url>                    # markdown → stdout
x2md <url> -o notes/          # writes notes/YYYY-MM-DD-<handle>-<slug>.md
x2md <url> -o tweet.md        # writes exactly tweet.md
x2md <url> --top 5            # include top 5 replies (default: 2)
x2md <url> --no-replies       # skip the replies section
x2md <url> --quiet            # suppress stderr warnings
```

`<url>` can be from `x.com`, `twitter.com`, `fxtwitter.com`, `vxtwitter.com`, or `fixupx.com` — they all resolve to the same status ID.

## What you get

- **YAML frontmatter** — title, author, source URL, canonical URL (for X Articles), publish date, cover image URL.
- **The body** — tweet text for plain tweets; the full draft-js → markdown render for X Articles, with inline images spliced in at their original positions.
- **Attached media** — photo URLs as `![]()`, video URLs as direct MP4 links (downloadable with `curl`).
- **Top replies** — the top N third-party replies ranked by like count, each with author, like count, body (as a blockquote), and permalink. Replies from the original poster are filtered out so what you see is genuine commentary.

## Use it as an LLM tool

`x2md` is built to be discoverable by coding agents. The output is markdown so it slots straight into any prompt; the CLI is small and predictable so any agent with shell access can use it.

**Claude Code / Cursor / aider / etc.** Tell the agent:

> Use `x2md <url>` to read tweets. It returns markdown including the tweet body, any attached media, X Article content if applicable, and the top replies.

**Pipe directly into a chat:**

```bash
x2md <url> | pbcopy           # macOS: copy to clipboard
x2md <url> | xclip -sel clip  # Linux
x2md <url> > /tmp/t.md && claude /tmp/t.md
```

**Add as a Claude Code permission** (so it never prompts):

```jsonc
// ~/.claude/settings.json
{
  "permissions": {
    "allow": ["Bash(x2md:*)"]
  }
}
```

**Use from a script / agent loop:**

```python
import subprocess
md = subprocess.check_output(["x2md", url], text=True)
# md is ready to feed into a model
```

## How it works

1. The status URL is parsed into `(handle, status_id)`.
2. **Tweet body & media** come from `https://api.fxtwitter.com/<handle>/status/<id>`. This returns JSON with the tweet text, attached photos/videos (including raw MP4 URLs), and — crucially — the full structured content of long-form X Articles, which the standard X embed APIs don't expose.
3. **X Article rendering** converts the draft-js block array into markdown: `header-two` → `##`, `unordered-list-item` → `-`, `atomic` blocks look up an image URL in `entityMap` → `media_entities` and emit `![]()`. Inline images land at their original positions in the prose.
4. **Top replies** are scraped from a working Nitter mirror's conversation HTML. Authors, body text, like counts, and permalinks are paired in document order; the OP's own replies are filtered out; the rest are sorted by like count.
5. The whole thing is assembled with YAML frontmatter on top.

## Limitations

- **No auth** — protected accounts, age-gated content, and deleted tweets won't work.
- **Nitter is flaky.** When all mirrors are down, the tweet body still saves fine — only the replies section is skipped (with a stderr warning). The `NITTER_INSTANCES` list at the top of `x2md.py` is what to update if all current mirrors die.
- **Single tweet, not threads.** If the URL is a thread root, only the root post is captured (plus its top replies). Capturing whole threads is a future feature.
- **Best-effort reply scraping.** Nitter's HTML structure changes occasionally; if reply parsing ever returns garbage, the regexes in `fetch_top_replies` are where to adjust.

## License

MIT — see [LICENSE](LICENSE).

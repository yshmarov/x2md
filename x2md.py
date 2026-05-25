#!/usr/bin/env python3
"""x2md — convert an X (Twitter) URL into clean markdown.

Handles plain tweets, tweets with photos/videos, and long-form X Articles.
Fetches via api.fxtwitter.com (unauthenticated, no rate limits) and optionally
appends the top replies scraped from a working Nitter instance.

By default the markdown is printed to stdout. Pass -o PATH to save to a file
or directory instead.

Usage:
    x2md <url>                          # markdown → stdout
    x2md <url> -o notes/                # writes notes/YYYY-MM-DD-<handle>-<slug>.md
    x2md <url> -o tweet.md              # writes exactly tweet.md
    x2md <url> --top 5                  # include top 5 replies (default: 2)
    x2md <url> --no-replies             # skip the replies section

Examples:
    x2md https://x.com/yarotheslav/status/2015892349688111187
    x2md https://x.com/rohit4verse/status/2058238863655714909 -o ~/notes/
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.1.0"

NITTER_INSTANCES = [
    "nitter.tiekoetter.com",
    "nitter.net",
    "nitter.poast.org",
    "nitter.privacydev.net",
]


# ---------- URL parsing & fetching ------------------------------------------

URL_RE = re.compile(
    r"(?:x|twitter|fxtwitter|vxtwitter|fixupx)\.com/([^/?#]+)/status/(\d+)"
)


def parse_url(url: str) -> tuple[str, str]:
    m = URL_RE.search(url)
    if not m:
        sys.exit(f"x2md: couldn't parse user/status from URL: {url}")
    return m.group(1), m.group(2)


def fetch_tweet(user: str, sid: str) -> dict:
    api_url = f"https://api.fxtwitter.com/{user}/status/{sid}"
    req = urllib.request.Request(
        api_url, headers={"User-Agent": f"x2md/{__version__}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"x2md: fxtwitter returned HTTP {e.code} for {api_url}")
    except urllib.error.URLError as e:
        sys.exit(f"x2md: network error fetching {api_url}: {e}")
    if data.get("code") != 200:
        sys.exit(f"x2md: fxtwitter API error: {data}")
    return data["tweet"]


# ---------- Rendering -------------------------------------------------------


def slugify(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", (text or "").lower())
    s = re.sub(r"\s+", "-", s.strip())
    return s[:maxlen].rstrip("-") or "untitled"


def render_article(article: dict) -> str:
    """Convert an X Article's draft-js blocks into markdown, inlining images."""
    media_by_id = {
        m["media_id"]: m["media_info"]["original_img_url"]
        for m in article.get("media_entities", [])
    }
    raw_entity_map = article["content"].get("entityMap", [])
    if isinstance(raw_entity_map, list):
        entity_map = {str(e["key"]): e["value"] for e in raw_entity_map}
    else:
        entity_map = raw_entity_map

    lines: list[str] = []
    img_counter = 0
    for block in article["content"]["blocks"]:
        t = block["type"]
        text = block.get("text", "")
        if t == "atomic":
            for er in block.get("entityRanges", []):
                entity = entity_map.get(str(er["key"]), {})
                if entity.get("type") == "MEDIA":
                    for item in entity["data"].get("mediaItems", []):
                        url = media_by_id.get(item["mediaId"])
                        if url:
                            img_counter += 1
                            lines.append(f"![Inline image {img_counter}]({url})")
                            lines.append("")
        elif t == "header-two":
            lines += [f"## {text}", ""]
        elif t == "header-three":
            lines += [f"### {text}", ""]
        elif t == "unordered-list-item":
            lines.append(f"- {text}")
        elif t == "ordered-list-item":
            lines.append(f"1. {text}")
        else:
            lines += [text, ""]
    return "\n".join(lines).strip()


def render_tweet(tweet: dict) -> str:
    """Render a plain tweet (no long-form article) with text + media."""
    text = (tweet.get("text") or "").strip()
    if not text:
        text = (tweet.get("raw_text", {}) or {}).get("text", "").strip()
    parts: list[str] = [text] if text else []
    media = tweet.get("media") or {}
    for photo in media.get("photos") or []:
        parts.append(f"![Image]({photo['url']})")
    for video in media.get("videos") or []:
        parts.append(f"**Video:** {video.get('url', '')}")
    return "\n\n".join(p for p in parts if p)


# ---------- Frontmatter -----------------------------------------------------


def build_frontmatter(tweet: dict, article: dict | None, url: str) -> dict:
    name = tweet["author"]["name"]
    handle = tweet["author"]["screen_name"]
    fm: dict = {
        "title": article["title"] if article else f"Tweet by {name}",
        "author": f"{name} (@{handle})",
        "source_tweet": tweet.get("url", url),
    }
    if article:
        fm["canonical_url"] = f"https://x.com/i/article/{article['id']}"
        fm["article_id"] = article["id"]
        fm["published"] = article["created_at"]
        if article.get("modified_at") and article["modified_at"] != article["created_at"]:
            fm["modified"] = article["modified_at"]
        cover = (
            article.get("cover_media", {})
            .get("media_info", {})
            .get("original_img_url")
        )
        if cover:
            fm["cover_image"] = cover
    else:
        fm["published"] = tweet.get("created_at", "")
    fm["saved_via"] = "x2md (api.fxtwitter.com)"
    fm["saved_on"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return fm


def yaml_dump(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        if v in (None, ""):
            continue
        if isinstance(v, str):
            escaped = v.replace('"', '\\"')
            lines.append(f'{k}: "{escaped}"')
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


# ---------- Replies (Nitter scraping via curl) -------------------------------


def fetch_top_replies(user: str, sid: str, n: int, quiet: bool) -> list[dict]:
    """Return top-N third-party replies by like count via a working Nitter mirror.

    Nitter is increasingly behind Anubis proof-of-work bot challenges that
    block Python's urllib but pass curl's TLS fingerprint, so we shell out.
    Returns an empty list (with a stderr warning) if no mirror works.
    """
    if n <= 0:
        return []
    if not shutil.which("curl"):
        if not quiet:
            print("x2md: curl not found on PATH; skipping replies", file=sys.stderr)
        return []
    path = f"/{user}/status/{sid}"
    body = ""
    for host in NITTER_INSTANCES:
        try:
            result = subprocess.run(
                ["curl", "-sL", "-m", "12", f"https://{host}{path}"],
                capture_output=True, text=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        candidate = result.stdout or ""
        if len(candidate) > 5000 and "tweet-content" in candidate:
            body = candidate
            break
    if not body:
        if not quiet:
            print("x2md: no working Nitter mirror; skipping replies", file=sys.stderr)
        return []

    def clean(s: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

    def parse_count(s: str) -> int:
        s = (s or "").strip().replace(",", "")
        if not s:
            return 0
        if s.endswith(("K", "k")):
            return int(float(s[:-1]) * 1000)
        if s.endswith(("M", "m")):
            return int(float(s[:-1]) * 1_000_000)
        try:
            return int(float(s))
        except ValueError:
            return 0

    authors = [m.group(1).strip() for m in re.finditer(r'<a class="username"[^>]*>([^<]+)</a>', body)]
    contents = [m.group(1) for m in re.finditer(r'<div class="tweet-content media-body"[^>]*>(.*?)</div>', body, re.DOTALL)]
    likes = [m.group(1) for m in re.finditer(r'<span class="icon-heart"[^>]*></span>\s*([\d,\.KkMm]*)\s*</div>', body)]
    permalinks = [m.group(1) for m in re.finditer(r'<a class="tweet-link"[^>]*href="([^"]+)"', body)]
    paired = min(len(authors), len(contents), len(likes), len(permalinks))
    if paired < 2:
        return []
    tweets = [
        {
            "author": authors[i],
            "text": clean(contents[i]),
            "likes": parse_count(likes[i]),
            "permalink": f"https://x.com{permalinks[i].rsplit('#', 1)[0]}",
        }
        for i in range(paired)
    ]
    op = tweets[0]["author"]
    third_party = [t for t in tweets[1:] if t["author"] != op]
    return sorted(third_party, key=lambda t: -t["likes"])[:n]


def render_replies(replies: list[dict]) -> str:
    if not replies:
        return ""
    lines = ["## Top replies", ""]
    for r in replies:
        lines.append(f"**{r['author']}** — ♥ {r['likes']} — [permalink]({r['permalink']})")
        lines.append("")
        for para in r["text"].split("\n"):
            lines.append(f"> {para}" if para.strip() else ">")
        lines.append("")
    return "\n".join(lines).strip()


# ---------- Assembly --------------------------------------------------------


def date_for_filename(tweet: dict) -> str:
    raw = tweet.get("created_at", "")
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")


def build_markdown(tweet: dict, url: str, top: int, quiet: bool) -> tuple[str, dict]:
    article = tweet.get("article")
    fm = build_frontmatter(tweet, article, url)
    body = render_article(article) if article else render_tweet(tweet)
    user = tweet["author"]["screen_name"]
    sid = tweet["id"]

    has_replies = int(tweet.get("replies", 0) or 0) > 0
    replies_md = (
        render_replies(fetch_top_replies(user, sid, top, quiet))
        if has_replies and top > 0
        else ""
    )

    parts = [yaml_dump(fm), "", f"# {fm['title']}", ""]
    if article and fm.get("cover_image"):
        parts += [f"![Cover image]({fm['cover_image']})", ""]
    parts += [
        f"> Saved from [@{user}]({fm['source_tweet']}).",
        "",
        body,
        "",
    ]
    if replies_md:
        parts += ["---", "", replies_md, ""]
    return "\n".join(parts), fm


def resolve_output_path(output: str, tweet: dict, fm: dict) -> Path:
    """If output is an existing dir (or ends with /), build the canonical filename inside it.
    Otherwise treat it as the exact destination file path."""
    p = Path(output).expanduser()
    looks_like_dir = output.endswith(("/", "\\")) or p.is_dir()
    if looks_like_dir:
        p.mkdir(parents=True, exist_ok=True)
        user = tweet["author"]["screen_name"]
        name = f"{date_for_filename(tweet)}-{user}-{slugify(fm['title'])}.md"
        return p / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------- CLI -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="x2md",
        description="Convert an X (Twitter) URL into clean markdown (with frontmatter, images, and top replies).",
        epilog="More info: https://github.com/yshmarov/x2md",
    )
    p.add_argument("url", help="X/Twitter status URL")
    p.add_argument(
        "-o", "--output",
        help="Write to PATH instead of stdout. If PATH is a directory, the file is named YYYY-MM-DD-<handle>-<slug>.md inside it.",
    )
    p.add_argument(
        "--top", type=int, default=2, metavar="N",
        help="Number of top replies to include (default: 2). Use 0 to skip.",
    )
    p.add_argument("--no-replies", action="store_true", help="Shortcut for --top 0.")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress stderr warnings.")
    p.add_argument("-V", "--version", action="version", version=f"x2md {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    top = 0 if args.no_replies else max(0, args.top)

    user, sid = parse_url(args.url)
    tweet = fetch_tweet(user, sid)
    md, fm = build_markdown(tweet, args.url, top, args.quiet)

    if args.output:
        path = resolve_output_path(args.output, tweet, fm)
        path.write_text(md, encoding="utf-8")
        if not args.quiet:
            print(f"wrote {path}", file=sys.stderr)
    else:
        sys.stdout.write(md)
        if not md.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

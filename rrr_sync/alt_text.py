"""Replaces the Alt Text King app: write alt text for product photos that don't have any.

Alt text is the short description Google and screen readers use for an image. Alt Text King wrote one
per photo describing what's actually in the picture. This job does the same:

- Finds product photos whose alt text is empty (by default only products listed in the last 30 days,
  which is where new blank photos show up; use --all to scan the whole catalog).
- If ANTHROPIC_API_KEY is set, Claude looks at each photo and writes a description in the same style
  (what's in the picture, under ~125 characters, no "image of", follows the RRR brand rules).
- Without a key it still fills in a plain description from the product title, so nothing stays blank.
- Never overwrites alt text that already exists. Dry run unless --apply. --max caps photos per run.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from . import http

DEFAULT_DAYS = 30
DEFAULT_MAX = 200
MAX_LEN = 125

PRODUCTS_QUERY = """
query Products($cursor: String, $q: String) {
  products(first: 25, after: $cursor, query: $q, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title
      media(first: 50) {
        nodes { ... on MediaImage { id alt image { url } } }
      }
    }
  }
}
"""

FILE_UPDATE = """
mutation Alt($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) { files { id alt } userErrors { field message } }
}
"""

PROMPT = """You write alt text for product photos in an online shop that sells brand-new retro-inspired \
collectibles (keychains, plush, pins, stickers, figures, gifts) featuring pop-culture characters.

Product title: {title}
This is photo {pos} of {count}.

Write ONE alt text for this photo:
- Describe what is actually visible: the item, its colors, the character or artwork, and what this photo shows \
(front, packaging, close-up, open, size comparison...).
- Start with the item type. Under {max_len} characters. Plain words.
- Do not start with "Image of", "Photo of" or "Picture of".
- Do not call it vintage, antique, original or one-of-a-kind. No emojis, no hashtags, no prices, no quotes \
around the whole thing.

Examples of the style:
A7 sticker photo album planner with translucent orange cover and cartoon sticker sheet
Snoopy plush keychain in red quilted puffer jacket with silver clip

Reply with only the alt text."""


def _clean(text, max_len=MAX_LEN):
    text = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"^(an?\s+)?(image|photo|picture)\s+of\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        cut = text[:max_len].rsplit(" ", 1)[0]
        text = cut.rstrip(",;:-–— ")
    return text[:1].upper() + text[1:] if text else text


def template_alt(title, pos, count):
    """Fallback when there's no API key: product title plus which photo it is."""
    title = re.sub(r"\s+", " ", title).strip()
    suffix = "" if count <= 1 else (" - main photo" if pos == 1 else f" - photo {pos} of {count}")
    room = MAX_LEN - len(suffix)
    if len(title) > room:
        title = title[:room].rsplit(" ", 1)[0].rstrip(",;:-– ")
    return title + suffix


class ClaudeAlt:
    """Ask Claude to look at a photo and describe it."""

    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key, model):
        self.api_key = api_key
        self.model = model

    def describe(self, image_url, title, pos, count):
        sized = image_url + ("&" if "?" in image_url else "?") + "width=800"
        payload = {
            "model": self.model,
            "max_tokens": 120,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": sized}},
                    {"type": "text", "text": PROMPT.format(title=title, pos=pos, count=count, max_len=MAX_LEN)},
                ],
            }],
        }
        resp = http.request_json("POST", self.URL, headers={
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, payload=payload)
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        return _clean(text)


def find_missing(shopify, days=DEFAULT_DAYS, scan_all=False, now=None):
    """Return [(product_title, media_id, image_url, position, count)] for photos with empty alt text."""
    now = now or datetime.now(timezone.utc)
    q = None if scan_all else "created_at:>='" + (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ") + "'"
    out, cursor = [], None
    while True:
        data = shopify.graphql(PRODUCTS_QUERY, {"cursor": cursor, "q": q})
        conn = data["products"]
        for p in conn["nodes"]:
            imgs = [m for m in p["media"]["nodes"] if m and m.get("image")]
            for i, m in enumerate(imgs, 1):
                if not (m.get("alt") or "").strip():
                    out.append((p["title"], m["id"], m["image"]["url"], i, len(imgs)))
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]


def run(shopify, cfg, do_apply=False, days=DEFAULT_DAYS, scan_all=False, max_images=DEFAULT_MAX,
        log=print, describer=None):
    missing = find_missing(shopify, days, scan_all)
    if describer is None and cfg.anthropic_api_key:
        describer = ClaudeAlt(cfg.anthropic_api_key, cfg.alt_text_model)
    how = f"Claude ({cfg.alt_text_model}) looks at each photo" if describer else "no ANTHROPIC_API_KEY: using product titles"
    mode = "APPLYING" if do_apply else "DRY RUN (add --apply to save)"
    scope = "whole catalog" if scan_all else f"products from the last {days} days"
    log(f"Alt text: {len(missing)} photos missing alt text ({scope}). {how}. {mode}")
    if len(missing) > max_images:
        log(f"  Doing the first {max_images} this run (--max). The rest get picked up next run.")
        missing = missing[:max_images]

    done, errors = 0, []
    for title, media_id, url, pos, count in missing:
        alt = ""
        if describer:
            try:
                alt = _clean(describer.describe(url, title, pos, count))
            except Exception as e:  # one bad photo shouldn't stop the run
                errors.append(f"{title} photo {pos}: Claude failed ({str(e)[:120]}), used title instead")
        alt = alt or template_alt(title, pos, count)
        log(f"  {title[:50]} [{pos}/{count}]  ->  {alt}")
        if do_apply:
            data = shopify.graphql(FILE_UPDATE, {"files": [{"id": media_id, "alt": alt}]})
            errs = data["fileUpdate"]["userErrors"]
            if errs:
                errors.extend(f"{title} photo {pos}: {e['message']}" for e in errs)
                continue
        done += 1
    log(f"{'Saved' if do_apply else 'Would save'} alt text for {done} photos.")
    for e in errors:
        log("  NOTE " + e)
    return done, errors

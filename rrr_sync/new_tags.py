"""Replaces the Shopify Flow app: keep the "New Finds" collection fresh.

The collection "Retro Relics Rescued - New Finds" is a smart collection whose rule is
"product tag is equal to `new item`". This job keeps that tag on exactly the products listed
in the last N days (default 14):

- A product created within the window that lacks the tag  -> tag is ADDED.
- A product with the tag that is older than the window    -> tag is REMOVED.
  (It stays in every other collection; it just cycles out of New Finds.)
- A product tagged `keep-new` is never stripped, for anything you want to pin as new.

Only the one tag is touched. Nothing else about a product changes. Run it on a schedule
(the same Windows Task Scheduler job as the inventory sync works fine); every run is safe
to repeat. It is a dry run unless --apply is given.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEFAULT_TAG = "new item"
PIN_TAG = "keep-new"
DEFAULT_DAYS = 14

PRODUCTS_QUERY = """
query Products($cursor: String, $q: String!) {
  products(first: 100, after: $cursor, query: $q) {
    pageInfo { hasNextPage endCursor }
    nodes { id title tags createdAt status }
  }
}
"""

TAGS_ADD = """
mutation Add($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

TAGS_REMOVE = """
mutation Remove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) { userErrors { field message } }
}
"""


@dataclass
class Product:
    id: str
    title: str
    tags: list
    created_at: datetime
    status: str


@dataclass
class Change:
    action: str  # "add" or "remove"
    product: Product

    def describe(self):
        age = (datetime.now(timezone.utc) - self.product.created_at).days
        verb = "ADD   " if self.action == "add" else "REMOVE"
        return f"{verb} '{self.product.title}' (listed {age} days ago)"


def _has(tags, tag):
    return any(t.strip().lower() == tag.lower() for t in tags)


def plan(products, now=None, days=DEFAULT_DAYS, tag=DEFAULT_TAG):
    """Decide which products need the tag added or removed. Pure function, easy to test."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    changes, seen = [], set()
    for p in products:
        if p.id in seen:
            continue
        seen.add(p.id)
        is_recent = p.created_at >= cutoff
        tagged = _has(p.tags, tag)
        if is_recent and not tagged and p.status != "ARCHIVED":
            changes.append(Change("add", p))
        elif not is_recent and tagged and not _has(p.tags, PIN_TAG):
            changes.append(Change("remove", p))
    return changes


def _parse(node):
    return Product(
        id=node["id"],
        title=node["title"],
        tags=node["tags"],
        created_at=datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")),
        status=node["status"],
    )


def fetch_candidates(shopify, days=DEFAULT_DAYS, tag=DEFAULT_TAG, now=None):
    """Only two small searches: products listed recently, and products currently tagged."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    queries = [f"created_at:>='{since}'", f"tag:'{tag}'"]
    out = []
    for q in queries:
        cursor = None
        while True:
            data = shopify.graphql(PRODUCTS_QUERY, {"cursor": cursor, "q": q})
            conn = data["products"]
            out.extend(_parse(n) for n in conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                break
            cursor = conn["pageInfo"]["endCursor"]
    return out


def apply(shopify, changes, tag=DEFAULT_TAG):
    """Make the changes. Returns a list of error strings (empty when everything worked)."""
    errors = []
    for c in changes:
        mutation = TAGS_ADD if c.action == "add" else TAGS_REMOVE
        data = shopify.graphql(mutation, {"id": c.product.id, "tags": [tag]})
        key = "tagsAdd" if c.action == "add" else "tagsRemove"
        for e in data[key]["userErrors"]:
            errors.append(f"{c.product.title}: {e['message']}")
    return errors


def run(shopify, do_apply=False, days=DEFAULT_DAYS, tag=DEFAULT_TAG, log=print):
    changes = plan(fetch_candidates(shopify, days, tag), days=days, tag=tag)
    adds = sum(c.action == "add" for c in changes)
    removes = len(changes) - adds
    mode = "APPLYING" if do_apply else "DRY RUN (add --apply to make changes)"
    log(f"New Finds tag '{tag}', {days}-day window: {adds} to tag, {removes} to untag. {mode}")
    for c in changes:
        log("  " + c.describe())
    errors = apply(shopify, changes, tag) if (do_apply and changes) else []
    for e in errors:
        log("  ERROR " + e)
    return changes, errors

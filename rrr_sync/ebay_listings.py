"""Create, finish and publish eBay listings from Shopify products.

RRR business rules (keep these):
  * eBay price is whatever Stephanie says. Never calculated from Shopify
    (eBay's fees are far more than a flat markup covers).
  * eBay shows quantity 1; the inventory sync keeps it there while Shopify has stock.
  * Best Offer on every listing, no auto-decline floor (lowballers get blocked by hand).
  * Condition NEW unless told otherwise.
  * eBay SKU = the numeric ID from the Shopify product GID, so the two stay in sync.
  * Default policies: USPS Ground Advantage $8.99 flat, eBay Managed Payments, no returns.
"""

import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from . import ebay_inventory as inv
from . import ebay_store
from .shopify import Shopify

DEFAULT_CONDITION = "NEW"
MAX_IMAGES = 24          # eBay's limit per listing
MAX_TITLE = 80           # eBay's title limit

# Overridable in .env (read at call time, after .env is loaded).
DEFAULT_POLICIES = {
    "fulfillmentPolicyId": ("EBAY_FULFILLMENT_POLICY_ID", "284028239015"),  # USPS GA $8.99 flat
    "paymentPolicyId": ("EBAY_PAYMENT_POLICY_ID", "283965435015"),          # eBay Managed Payments
    "returnPolicyId": ("EBAY_RETURN_POLICY_ID", "291780816015"),            # No returns accepted
}

PRODUCT_QUERY = """
query Product($id: ID!) {
  node(id: $id) {
    ... on Product { ...P }
    ... on ProductVariant { product { ...P } }
  }
}
fragment P on Product {
  id title descriptionHtml totalInventory
  media(first: 24) { nodes { ... on MediaImage { image { url } } } }
  variants(first: 1) { nodes { price } }
}
"""

VARIANT_BY_SKU_QUERY = """
query BySku($q: String!) {
  productVariants(first: 1, query: $q) { nodes { product { id } } }
}
"""


# --- small pure helpers (unit tested) ---------------------------------------

def sku_from_shopify_id(shopify_id):
    """'gid://shopify/Product/15420259402024' or '15420259402024' -> '15420259402024'."""
    m = re.search(r"(\d+)\s*$", str(shopify_id))
    if not m:
        raise ValueError(f"Can't find a numeric Shopify ID in {shopify_id!r}")
    return m.group(1)


def shopify_id_from_sku(sku):
    """Numeric SKU (optionally with a suffix like '-A') -> Shopify product number, else None."""
    m = re.match(r"\s*(\d{6,})", str(sku))
    return m.group(1) if m else None


def ebay_price(price):
    """'$24.5' / 24.5 / '24.50' -> '24.50'. Refuses blanks, zero and nonsense."""
    try:
        value = Decimal(str(price).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{price!r} isn't a price") from None
    if value <= 0:
        raise ValueError(f"eBay price must be more than $0 (got {price!r})")
    return str(value.quantize(Decimal("0.01"), ROUND_HALF_UP))


def standard_policies(existing=None):
    """RRR's listing policies + Best Offer with no floor, keeping any unrelated fields."""
    policies = dict(existing or {})
    for field, (env, default) in DEFAULT_POLICIES.items():
        policies[field] = os.environ.get(env) or default
    policies["bestOfferTerms"] = {"bestOfferEnabled": True}
    return policies


def clean_image_urls(urls):
    if urls is None:
        return []
    if isinstance(urls, str):
        urls = [u for u in re.split(r"[\s,]+", urls) if u]
    out = []
    for u in urls:
        u = str(u).strip()
        if u.startswith("http://"):
            u = "https://" + u[len("http://"):]   # eBay only takes HTTPS images
        if u and u not in out:
            out.append(u)
    return out[:MAX_IMAGES]


def offer_summary(offer):
    price = offer.get("pricingSummary", {}).get("price", {})
    listing_id = offer.get("listing", {}).get("listingId")
    return {
        "offer_id": offer.get("offerId"),
        "sku": offer.get("sku"),
        "title": offer.get("_title", ""),
        "status": offer.get("status"),
        "price": price.get("value"),
        "quantity": offer.get("availableQuantity"),
        "category_id": offer.get("categoryId"),
        "listing_id": listing_id,
        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
    }


# --- Shopify -----------------------------------------------------------------

def shopify_product(cfg, sku_or_id):
    """Look a product up by numeric ID / GID, falling back to a variant SKU search.
    Returns {id, number, title, description, price, quantity, images} or None."""
    shop = Shopify(cfg)
    number = shopify_id_from_sku(sku_or_id)
    node = None
    if number:
        for kind in ("Product", "ProductVariant"):
            node = shop.graphql(PRODUCT_QUERY, {"id": f"gid://shopify/{kind}/{number}"})["node"]
            if node:
                break
    else:
        hits = shop.graphql(VARIANT_BY_SKU_QUERY, {"q": f"sku:{sku_or_id}"})["productVariants"]["nodes"]
        if hits:
            node = shop.graphql(PRODUCT_QUERY, {"id": hits[0]["product"]["id"]})["node"]
    if not node:
        return None
    product = node.get("product", node)
    variants = product["variants"]["nodes"]
    return {
        "id": product["id"],
        "number": sku_from_shopify_id(product["id"]),
        "title": product["title"],
        "description": product.get("descriptionHtml") or product["title"],
        "price": variants[0]["price"] if variants else None,
        "quantity": product.get("totalInventory"),
        "images": clean_image_urls(
            m["image"]["url"] for m in product["media"]["nodes"] if m.get("image")
        ),
    }


# --- eBay --------------------------------------------------------------------

def merchant_location_key(cfg):
    key = os.environ.get("EBAY_MERCHANT_LOCATION_KEY")
    if key:
        return key
    locs = inv.locations(cfg)
    enabled = [l for l in locs if l.get("merchantLocationStatus", "ENABLED") == "ENABLED"]
    if not enabled:
        raise RuntimeError(
            "No eBay inventory location exists, and eBay won't publish without one "
            "(it's where 'Item location' comes from). Create one in Seller Hub or via "
            "POST /sell/inventory/v1/location/{key}, or set EBAY_MERCHANT_LOCATION_KEY in .env."
        )
    return enabled[0]["merchantLocationKey"]


CATEGORY_SUGGESTIONS = 10   # how many of eBay's suggestions count as "fits"


def check_category(cfg, category_id, titles, confirmed=False):
    """Refuse a category eBay itself wouldn't suggest for this item.

    Guards against a made-up or remembered category ID (e.g. 10 toys listed as Golf Clubs).
    Returns {"category_id", "category_name", "in_ebay_suggestions"}.
    confirmed=True: Stephanie has OK'd this exact category, so list it anyway.
    """
    category_id = str(category_id).strip()
    name = inv.category_name(cfg, category_id)
    if name is None:
        raise RuntimeError(
            f"eBay has no category {category_id}. Don't guess category IDs: "
            "call ebay_suggest_category with the item title and use one it returns."
        )
    suggestions = []
    for t in dict.fromkeys(t for t in titles if t):
        for s in inv.category_suggestions(cfg, t, limit=CATEGORY_SUGGESTIONS):
            if s["category_id"] not in [x["category_id"] for x in suggestions]:
                suggestions.append(s)
    fits = category_id in [s["category_id"] for s in suggestions]
    if not fits and not confirmed:
        options = "; ".join(f'{s["category_id"]} = {s["path"]}' for s in suggestions[:5]) or "none"
        raise RuntimeError(
            f'WRONG CATEGORY? {category_id} is "{name}", which eBay does not suggest for '
            f'"{titles[0]}". Nothing was changed. eBay suggests: {options}. '
            "Use one of those, or show Stephanie the category name and only pass "
            "category_confirmed=true if she says it's right."
        )
    return {"category_id": category_id, "category_name": name, "in_ebay_suggestions": fits}


def create_draft(cfg, shopify_product_id, price, category_id=None, quantity=None,
                 condition=DEFAULT_CONDITION, title=None, aspects=None, image_urls=None,
                 category_confirmed=False, store_category=None):
    """Build an eBay inventory item + unpublished offer from a Shopify product.

    price: the eBay price Stephanie gives. Required; nothing is worked out from Shopify.
    quantity: defaults to 1 (eBay always shows 1; the sync relists as Shopify sells down).
    """
    if price in (None, ""):
        raise RuntimeError("No eBay price given. Ask Stephanie what the eBay price is.")
    price = ebay_price(price)
    product = shopify_product(cfg, shopify_product_id)
    if not product:
        raise RuntimeError(f"Shopify product {shopify_product_id} not found")
    sku = product["number"]
    images = clean_image_urls(image_urls) or product["images"]
    if quantity is None and int(product["quantity"] or 0) < 1:
        raise RuntimeError(f"{product['title']} has 0 in stock on Shopify; pass quantity to override")
    qty = int(quantity) if quantity is not None else 1
    ebay_title = (title or product["title"])[:MAX_TITLE]
    category = None
    if category_id:   # checked before anything is written to eBay
        category = check_category(cfg, category_id, [product["title"], ebay_title], category_confirmed)
    shelf = ebay_store.resolve_store_category(cfg, store_category) if store_category else None

    item = {
        "availability": {"shipToLocationAvailability": {"quantity": qty}},
        "condition": condition or DEFAULT_CONDITION,
        "product": {
            "title": ebay_title,
            "description": product["description"],
            "imageUrls": images,
        },
    }
    if aspects:
        item["product"]["aspects"] = _aspects(aspects)
    inv.put_inventory_item(cfg, sku, item)

    offer = {
        "sku": sku,
        "marketplaceId": inv.MARKETPLACE_ID,
        "format": "FIXED_PRICE",
        "availableQuantity": qty,
        "listingDescription": product["description"],
        "listingDuration": "GTC",
        "listingPolicies": standard_policies(),
        "merchantLocationKey": merchant_location_key(cfg),
        "pricingSummary": {"price": {"value": price, "currency": "USD"}},
    }
    if category_id:
        offer["categoryId"] = str(category_id)
    if shelf:
        offer["storeCategoryNames"] = [shelf["path"]]

    existing = [o for o in inv.offers_for_sku(cfg, sku) if o.get("format", "FIXED_PRICE") == "FIXED_PRICE"]
    if existing:
        offer_id = existing[0]["offerId"]
        inv.update_offer(cfg, offer_id, {**existing[0], **offer})
    else:
        offer_id = inv.create_offer(cfg, offer)

    return {
        "offer_id": offer_id,
        "sku": sku,
        "shopify_product_id": product["id"],
        "shopify_title": product["title"],
        "ebay_title": item["product"]["title"],
        "price": offer["pricingSummary"]["price"]["value"],
        "shopify_price": product["price"],
        "category_name": category["category_name"] if category else None,
        "store_category": shelf["path"] if shelf else None,
        "quantity": qty,
        "images": len(images),
        "category_id": offer.get("categoryId"),
        "updated_existing_offer": bool(existing),
        "next_step": "ebay_listing_report for this offer_id, show Stephanie, publish only after she says go",
    }


def _aspects(aspects):
    """eBay wants {"Brand": ["Funko"]}; accept plain strings too."""
    return {k: (v if isinstance(v, list) else [v]) for k, v in dict(aspects).items()}


def publish(cfg, offer_id, image_urls=None, aspects=None, category_id=None, price=None,
            category_confirmed=False, store_category=None):
    """Attach photos + RRR's standard policies to an offer and publish it.

    price: optional new eBay price. Left out, the offer keeps the price it has.

    image_urls: list of HTTPS photo URLs. If empty, the photos are pulled from the
    Shopify product whose ID is the offer's SKU.
    """
    notes = []
    offer = inv.get_offer(cfg, offer_id)
    sku = offer["sku"]

    product = None
    try:
        product = shopify_product(cfg, sku)
        if not product:
            notes.append(f"No Shopify product found for SKU {sku}")
    except Exception as e:   # Shopify being down shouldn't block a publish with given photos
        notes.append(f"Shopify lookup failed: {e}")

    item = inv.get_inventory_item(cfg, sku)
    item.setdefault("product", {})

    # 0. Category sanity check, before anything is written or published.
    final_category = category_id or offer.get("categoryId")
    if not final_category:
        raise RuntimeError(f"Offer {offer_id} has no eBay category; pass category_id")
    titles = [item["product"].get("title"), product["title"] if product else None]
    category = check_category(cfg, final_category, [t for t in titles if t], category_confirmed)
    # Store shelf: required, or the listing lands in the store's "Other" (and Stephanie
    # can't fix API listings by hand in Seller Hub).
    if store_category:
        shelf_path = ebay_store.resolve_store_category(cfg, store_category)["path"]
    elif offer.get("storeCategoryNames"):
        shelf_path = ebay_store.resolve_store_category(cfg, offer["storeCategoryNames"][0])["path"]
    else:
        names = ", ".join(c["path"] for c in ebay_store.store_categories(cfg) if c["leaf"])
        raise RuntimeError(f"No store category, so it would land in 'Other'. Nothing was changed. "
                           f"Pass store_category, one of: {names}")

    # 1. Photos (and aspects/condition) on the inventory item.
    images = clean_image_urls(image_urls) or (product["images"] if product else [])
    if images:
        item["product"]["imageUrls"] = images
    elif not item["product"].get("imageUrls"):
        raise RuntimeError(f"No photos for SKU {sku}: none passed, none on Shopify, none on eBay")
    if aspects:
        item["product"]["aspects"] = {**item["product"].get("aspects", {}), **_aspects(aspects)}
    item.setdefault("condition", DEFAULT_CONDITION)
    inv.put_inventory_item(cfg, sku, item)

    # 2. Policies, Best Offer, price, location on the offer.
    old_price = offer.get("pricingSummary", {}).get("price", {}).get("value")
    if price not in (None, ""):
        offer["pricingSummary"] = {"price": {"value": ebay_price(price), "currency": "USD"}}
    elif not old_price:
        raise RuntimeError(f"Offer {offer_id} has no price. Ask Stephanie what the eBay price is.")
    offer["listingPolicies"] = standard_policies(offer.get("listingPolicies"))
    if category_id:
        offer["categoryId"] = str(category_id)
    offer["storeCategoryNames"] = [shelf_path]
    if not offer.get("merchantLocationKey"):
        offer["merchantLocationKey"] = merchant_location_key(cfg)
    if not offer.get("listingDescription"):
        offer["listingDescription"] = item["product"].get("description") or item["product"].get("title", "")
    offer.setdefault("listingDuration", "GTC")
    inv.update_offer(cfg, offer_id, offer)

    # 3. Publish.
    resp = inv.publish_offer(cfg, offer_id)
    listing_id = resp.get("listingId")
    new_price = offer["pricingSummary"]["price"]["value"]
    if old_price and old_price != new_price:
        notes.append(f"Price changed {old_price} -> {new_price}")
    notes.extend(w.get("longMessage") or w.get("message", "") for w in resp.get("warnings", []))
    return {
        "offer_id": offer_id,
        "sku": sku,
        "listing_id": listing_id,
        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
        "ebay_title": item["product"].get("title"),
        "shopify_title": product["title"] if product else None,
        "category_id": category["category_id"],
        "category_name": category["category_name"],
        "store_category": shelf_path,
        "price": new_price,
        "images": len(item["product"]["imageUrls"]),
        "notes": notes,
    }


def update_price(cfg, offer_id, price):
    """Change the price on an offer. A live listing is revised right away."""
    new_price = ebay_price(price)
    offer = inv.get_offer(cfg, offer_id)
    old_price = offer.get("pricingSummary", {}).get("price", {}).get("value")
    offer["pricingSummary"] = {"price": {"value": new_price, "currency": "USD"}}
    inv.update_offer(cfg, offer_id, offer)
    live = offer.get("status") == "PUBLISHED"
    if live:
        inv.publish_offer(cfg, offer_id)   # re-publishing revises the live listing
    listing_id = offer.get("listing", {}).get("listingId")
    return {
        "offer_id": offer_id,
        "sku": offer.get("sku"),
        "old_price": old_price,
        "price": new_price,
        "live_listing_updated": live,
        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
    }


# --- the listing sheet ---------------------------------------------------------

REPORT_COLUMNS = (
    "status", "offer_id", "sku", "shopify_title", "ebay_title", "category_id", "category_name",
    "store_category", "ebay_price", "shopify_price", "quantity", "listing_id", "url", "problem",
)


def listing_report(cfg, offer_ids=None, skus=None, out_dir=None):
    """The listing sheet, built only from what eBay and Shopify say right now.

    offer_ids / skus: which listings; both empty = every offer on the account.
    Writes a CSV (when out_dir is given) and returns (rows, csv_path).
    Anything eBay can't find becomes a NOT FOUND row instead of disappearing.
    """
    import csv
    from datetime import datetime
    from pathlib import Path

    offers, rows = [], []
    if offer_ids or skus:
        for oid in offer_ids or []:
            try:
                offers.append(inv.get_offer(cfg, str(oid).strip()))
            except inv.EbayApiError as e:
                rows.append({"status": "NOT FOUND", "offer_id": oid, "problem": str(e)})
        for sku in skus or []:
            found = inv.offers_for_sku(cfg, str(sku).strip())
            if not found:
                rows.append({"status": "NOT FOUND", "sku": sku, "problem": "eBay has no offer for this SKU"})
            offers.extend(found)
    else:
        offers, _ = inv.all_offers(cfg)

    names = {}
    for o in offers:
        sku, problems = o.get("sku"), []
        try:
            ebay_title = inv.get_inventory_item(cfg, sku).get("product", {}).get("title", "")
        except inv.EbayApiError as e:
            ebay_title, _ = "", problems.append(f"eBay item: {e}")
        try:
            product = shopify_product(cfg, sku)
            if not product:
                problems.append("no Shopify product with this ID")
        except Exception as e:
            product, _ = None, problems.append(f"Shopify: {e}")
        cat = o.get("categoryId")
        if cat and cat not in names:
            try:
                names[cat] = inv.category_name(cfg, cat) or "UNKNOWN CATEGORY"
            except Exception as e:
                names[cat] = f"lookup failed: {e}"
        listing_id = o.get("listing", {}).get("listingId")
        rows.append({
            "status": o.get("status"),
            "offer_id": o.get("offerId"),
            "sku": sku,
            "shopify_title": product["title"] if product else "",
            "ebay_title": ebay_title,
            "category_id": cat or "",
            "category_name": names.get(cat, "") if cat else "NO CATEGORY",
            "store_category": (o.get("storeCategoryNames") or ["NONE (lands in Other)"])[0],
            "ebay_price": o.get("pricingSummary", {}).get("price", {}).get("value", ""),
            "shopify_price": product["price"] if product else "",
            "quantity": o.get("availableQuantity", ""),
            "listing_id": listing_id or "",
            "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else "",
            "problem": "; ".join(problems),
        })

    rows = [{c: r.get(c, "") for c in REPORT_COLUMNS} for r in rows]
    path = None
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        path = Path(out_dir) / f"ebay_listings_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel reads it right
            w = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        path = str(path)
    return rows, path

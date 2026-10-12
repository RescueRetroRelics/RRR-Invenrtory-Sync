"""Create, finish and publish eBay listings from Shopify products.

RRR business rules (keep these):
  * eBay price = Shopify price + $2.00, always.
  * Best Offer on every listing, no auto-decline floor (lowballers get blocked by hand).
  * Condition NEW unless told otherwise.
  * eBay SKU = the numeric ID from the Shopify product GID, so the two stay in sync.
  * Default policies: USPS Ground Advantage $8.99 flat, eBay Managed Payments, no returns.
"""

import os
import re
from decimal import Decimal, ROUND_HALF_UP

from . import ebay_inventory as inv
from .shopify import Shopify

PRICE_MARKUP = Decimal("2.00")
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


def ebay_price(shopify_price):
    total = (Decimal(str(shopify_price)) + PRICE_MARKUP).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return str(total)


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


def create_draft(cfg, shopify_product_id, category_id=None, quantity=None,
                 condition=DEFAULT_CONDITION, title=None, aspects=None, image_urls=None):
    """Build an eBay inventory item + unpublished offer from a Shopify product."""
    product = shopify_product(cfg, shopify_product_id)
    if not product:
        raise RuntimeError(f"Shopify product {shopify_product_id} not found")
    sku = product["number"]
    images = clean_image_urls(image_urls) or product["images"]
    qty = int(quantity) if quantity is not None else int(product["quantity"] or 0)
    if qty < 1:
        raise RuntimeError(f"{product['title']} has 0 in stock on Shopify; pass quantity to override")
    if product["price"] is None:
        raise RuntimeError(f"{product['title']} has no price on Shopify")

    item = {
        "availability": {"shipToLocationAvailability": {"quantity": qty}},
        "condition": condition or DEFAULT_CONDITION,
        "product": {
            "title": (title or product["title"])[:MAX_TITLE],
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
        "pricingSummary": {"price": {"value": ebay_price(product["price"]), "currency": "USD"}},
    }
    if category_id:
        offer["categoryId"] = str(category_id)

    existing = [o for o in inv.offers_for_sku(cfg, sku) if o.get("format", "FIXED_PRICE") == "FIXED_PRICE"]
    if existing:
        offer_id = existing[0]["offerId"]
        inv.update_offer(cfg, offer_id, {**existing[0], **offer})
    else:
        offer_id = inv.create_offer(cfg, offer)

    return {
        "offer_id": offer_id,
        "sku": sku,
        "title": item["product"]["title"],
        "price": offer["pricingSummary"]["price"]["value"],
        "shopify_price": product["price"],
        "quantity": qty,
        "images": len(images),
        "category_id": offer.get("categoryId"),
        "updated_existing_offer": bool(existing),
        "next_step": "ebay_publish_offer" if category_id else "set category_id, then ebay_publish_offer",
    }


def _aspects(aspects):
    """eBay wants {"Brand": ["Funko"]}; accept plain strings too."""
    return {k: (v if isinstance(v, list) else [v]) for k, v in dict(aspects).items()}


def publish(cfg, offer_id, image_urls=None, aspects=None, category_id=None):
    """Attach photos + RRR's standard policies to an offer and publish it.

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

    # 1. Photos (and aspects/condition) on the inventory item.
    item = inv.get_inventory_item(cfg, sku)
    item.setdefault("product", {})
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
    if product and product["price"] is not None:
        offer["pricingSummary"] = {"price": {"value": ebay_price(product["price"]), "currency": "USD"}}
    elif not old_price:
        raise RuntimeError(f"Offer {offer_id} has no price and Shopify price is unavailable")
    offer["listingPolicies"] = standard_policies(offer.get("listingPolicies"))
    if category_id:
        offer["categoryId"] = str(category_id)
    if not offer.get("categoryId"):
        raise RuntimeError(f"Offer {offer_id} has no eBay category; pass category_id")
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
        notes.append(f"Price changed {old_price} -> {new_price} (Shopify {product['price']} + $2)")
    notes.extend(w.get("longMessage") or w.get("message", "") for w in resp.get("warnings", []))
    return {
        "offer_id": offer_id,
        "sku": sku,
        "listing_id": listing_id,
        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
        "price": new_price,
        "images": len(item["product"]["imageUrls"]),
        "notes": notes,
    }

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


def fill_default_policies(existing=None):
    """Like standard_policies, but a policy or Best Offer setting already chosen
    for this listing (e.g. with ebay_update_listing) is kept."""
    policies = dict(existing or {})
    for field, value in standard_policies().items():
        policies.setdefault(field, value)
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
    offer_id = resolve_offer_id(cfg, offer_id)
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
    offer["listingPolicies"] = fill_default_policies(offer.get("listingPolicies"))
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


# --- viewing and editing a listing --------------------------------------------

CONDITIONS = (
    "NEW", "LIKE_NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "CERTIFIED_REFURBISHED",
    "EXCELLENT_REFURBISHED", "VERY_GOOD_REFURBISHED", "GOOD_REFURBISHED",
    "SELLER_REFURBISHED", "USED_EXCELLENT", "USED_VERY_GOOD", "USED_GOOD",
    "USED_ACCEPTABLE", "FOR_PARTS_OR_NOT_WORKING",
)

POLICY_FIELDS = {
    "fulfillment_policy_id": "fulfillmentPolicyId",
    "payment_policy_id": "paymentPolicyId",
    "return_policy_id": "returnPolicyId",
}


def _money(value):
    return {"value": str(Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP)), "currency": "USD"}


CLEAR_WORDS = ("", "none", "default", "remove", "off")


def _is_clear(value, zero_clears=True):
    """"none"/"default"/"remove"/"off"/"" (and 0, unless zero_clears=False) mean
    "remove this setting"."""
    text = str(value).strip().lower()
    if text in CLEAR_WORDS:
        return True
    return zero_clears and text in ("0", "0.0", "0.00")


def _deep_merge(base, extra):
    out = dict(base)
    for k, v in extra.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def resolve_offer_id(cfg, ref):
    """Accept an offer ID, the eBay item number from a listing's URL, or a SKU."""
    ref = str(ref).strip().rstrip("/").split("/")[-1].split("?")[0]   # tolerate a pasted ebay.com/itm/... link
    try:
        # One quick try, no retries: eBay answers an unknown ID (such as an item
        # number) with an error, sometimes a 500, and retrying that only burns time.
        inv.get_offer(cfg, ref, retries=0, timeout=10)
        return ref
    except inv.EbayApiError:
        pass
    offers, _ = inv.all_offers(cfg)
    for o in offers:
        if ref in (o.get("listing", {}).get("listingId"), o.get("sku")):
            return o["offerId"]
    raise RuntimeError(
        f"No offer found for {ref!r}. Only listings created through this tool can be edited "
        "here; Seller Hub listings are still edited in Seller Hub."
    )


def get_listing(cfg, offer_id):
    """Everything about one listing: the offer plus its inventory item."""
    offer_id = resolve_offer_id(cfg, offer_id)
    offer = inv.get_offer(cfg, offer_id)
    item = inv.get_inventory_item(cfg, offer["sku"])
    product = item.get("product", {})
    policies = offer.get("listingPolicies", {})
    best = policies.get("bestOfferTerms", {})
    listing_id = offer.get("listing", {}).get("listingId")
    return {
        "offer_id": offer.get("offerId"),
        "sku": offer.get("sku"),
        "status": offer.get("status"),
        "listing_id": listing_id,
        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
        "title": product.get("title"),
        "description": offer.get("listingDescription") or product.get("description"),
        "price": offer.get("pricingSummary", {}).get("price", {}).get("value"),
        "quantity": item.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity"),
        "available_quantity": offer.get("availableQuantity"),
        "condition": item.get("condition"),
        "condition_description": item.get("conditionDescription"),
        "category_id": offer.get("categoryId"),
        "aspects": product.get("aspects", {}),
        "image_urls": product.get("imageUrls", []),
        "fulfillment_policy_id": policies.get("fulfillmentPolicyId"),
        "payment_policy_id": policies.get("paymentPolicyId"),
        "return_policy_id": policies.get("returnPolicyId"),
        "shipping_cost_overrides": policies.get("shippingCostOverrides", []),
        "best_offer": {
            "enabled": best.get("bestOfferEnabled", False),
            "auto_accept_price": best.get("autoAcceptPrice", {}).get("value"),
            "auto_decline_price": best.get("autoDeclinePrice", {}).get("value"),
        },
        "package": item.get("packageWeightAndSize", {}),
        "quantity_limit_per_buyer": offer.get("quantityLimitPerBuyer"),
        "store_category_names": offer.get("storeCategoryNames", []),
        "merchant_location_key": offer.get("merchantLocationKey"),
        "listing_duration": offer.get("listingDuration"),
    }


def update_listing(cfg, offer_id, title=None, description=None, price=None,
                   sync_price_from_shopify=False, quantity=None, image_urls=None,
                   refresh_photos_from_shopify=False, aspects=None, remove_aspects=None,
                   condition=None, condition_description=None, category_id=None,
                   fulfillment_policy_id=None, payment_policy_id=None, return_policy_id=None,
                   shipping_cost=None, best_offer=None, auto_accept_price=None,
                   auto_decline_price=None, weight_oz=None, length_in=None, width_in=None,
                   height_in=None, quantity_limit_per_buyer=None, store_category_names=None,
                   item_fields=None, offer_fields=None):
    """Change any part of a listing. Only the arguments given are touched.

    On a PUBLISHED offer eBay revises the live listing right away; on a draft the
    change waits for ebay_publish_offer.
    """
    offer_id = resolve_offer_id(cfg, offer_id)
    offer = inv.get_offer(cfg, offer_id)
    sku = offer["sku"]
    item = inv.get_inventory_item(cfg, sku)
    product = item.setdefault("product", {})
    policies = offer.setdefault("listingPolicies", {})
    changes, notes = [], []
    item_changed = offer_changed = False

    shopify = None
    if sync_price_from_shopify or refresh_photos_from_shopify:
        shopify = shopify_product(cfg, sku)
        if not shopify:
            raise RuntimeError(f"No Shopify product found for SKU {sku}")

    # --- inventory item: what the item is ---
    if title is not None:
        if len(title) > MAX_TITLE:
            notes.append(f"Title cut to eBay's {MAX_TITLE}-character limit")
        product["title"] = title[:MAX_TITLE]
        changes.append("title")
        item_changed = True
    if description is not None:
        product["description"] = description
        offer["listingDescription"] = description
        changes.append("description")
        item_changed = offer_changed = True
    images = clean_image_urls(image_urls) or (shopify["images"] if refresh_photos_from_shopify else [])
    if image_urls is not None or refresh_photos_from_shopify:
        if not images:
            raise RuntimeError("No photos given; a listing needs at least one")
        product["imageUrls"] = images
        changes.append(f"photos ({len(images)})")
        item_changed = True
    if aspects:
        product["aspects"] = {**product.get("aspects", {}), **_aspects(aspects)}
        changes.append("item specifics: " + ", ".join(aspects))
        item_changed = True
    if remove_aspects:
        if isinstance(remove_aspects, str):
            remove_aspects = [remove_aspects]
        for name in remove_aspects:
            product.get("aspects", {}).pop(name, None)
        changes.append("removed item specifics: " + ", ".join(remove_aspects))
        item_changed = True
    if condition is not None:
        cond = str(condition).strip().upper().replace(" ", "_")
        if cond not in CONDITIONS:
            raise RuntimeError(f"Unknown condition {condition!r}. Use one of: {', '.join(CONDITIONS)}")
        item["condition"] = cond
        changes.append(f"condition {cond}")
        item_changed = True
    if condition_description is not None:
        if condition_description:
            item["conditionDescription"] = condition_description
        else:
            item.pop("conditionDescription", None)
        changes.append("condition description")
        item_changed = True
    if any(v is not None for v in (weight_oz, length_in, width_in, height_in)):
        pkg = item.setdefault("packageWeightAndSize", {})
        if weight_oz is not None:
            pkg["weight"] = {"value": float(weight_oz), "unit": "OUNCE"}
        if any(v is not None for v in (length_in, width_in, height_in)):
            dims = dict(pkg.get("dimensions", {}))
            for key, val in (("length", length_in), ("width", width_in), ("height", height_in)):
                if val is not None:
                    dims[key] = float(val)
            if not all(k in dims for k in ("length", "width", "height")):
                raise RuntimeError("Package size needs length, width and height (inches)")
            dims["unit"] = "INCH"
            pkg["dimensions"] = dims
        changes.append("package weight/size")
        item_changed = True

    # --- quantity lives on both ---
    old_qty = item.get("availability", {}).get("shipToLocationAvailability", {}).get("quantity")
    if quantity is not None:
        qty = int(quantity)
        if qty < 0:
            raise RuntimeError("Quantity can't be negative")
        item.setdefault("availability", {}).setdefault("shipToLocationAvailability", {})["quantity"] = qty
        offer["availableQuantity"] = qty
        changes.append(f"quantity {old_qty} -> {qty}")
        item_changed = offer_changed = True
        if qty == 0 and offer.get("status") == "PUBLISHED":
            notes.append("Quantity 0: eBay ends the listing unless Out-of-stock control is on")

    # --- offer: how it's sold ---
    if price is not None or sync_price_from_shopify:
        old = offer.get("pricingSummary", {}).get("price", {}).get("value")
        new = _money(price)["value"] if price is not None else ebay_price(shopify["price"])
        offer.setdefault("pricingSummary", {})["price"] = {"value": new, "currency": "USD"}
        changes.append(f"price {old} -> {new}")
        offer_changed = True
    if category_id:
        offer["categoryId"] = str(category_id)
        changes.append(f"category {category_id}")
        offer_changed = True
    chosen = {"fulfillment_policy_id": fulfillment_policy_id, "payment_policy_id": payment_policy_id,
              "return_policy_id": return_policy_id}
    for arg, field in POLICY_FIELDS.items():
        value = chosen[arg]
        if value:
            policies[field] = str(value)
            changes.append(f"{field} {value}")
            offer_changed = True
    if fulfillment_policy_id and shipping_cost is None and policies.pop("shippingCostOverrides", None):
        notes.append("Removed the old shipping cost override; it belonged to the previous shipping policy")
    if shipping_cost is not None:
        if _is_clear(shipping_cost, zero_clears=False):   # 0 means free shipping, not "remove"
            policies.pop("shippingCostOverrides", None)
            changes.append("shipping cost back to the policy's")
        else:
            policies["shippingCostOverrides"] = [{
                "priority": 1,
                "shippingServiceType": "DOMESTIC",
                "shippingCost": _money(shipping_cost),
            }]
            changes.append(f"shipping cost {_money(shipping_cost)['value']}")
        offer_changed = True
    if any(v is not None for v in (best_offer, auto_accept_price, auto_decline_price)):
        terms = dict(policies.get("bestOfferTerms", {}))
        if best_offer is not None:
            terms["bestOfferEnabled"] = bool(best_offer)
        for arg, field in ((auto_accept_price, "autoAcceptPrice"), (auto_decline_price, "autoDeclinePrice")):
            if arg is None:
                continue
            if _is_clear(arg):
                terms.pop(field, None)
            else:
                terms[field] = _money(arg)
                terms["bestOfferEnabled"] = True
        if not terms.get("bestOfferEnabled"):
            terms = {"bestOfferEnabled": False}
        policies["bestOfferTerms"] = terms
        changes.append("best offer settings")
        offer_changed = True
    if quantity_limit_per_buyer is not None:
        if _is_clear(quantity_limit_per_buyer):
            offer.pop("quantityLimitPerBuyer", None)
        else:
            offer["quantityLimitPerBuyer"] = int(quantity_limit_per_buyer)
        changes.append("quantity limit per buyer")
        offer_changed = True
    if store_category_names is not None:
        offer["storeCategoryNames"] = list(store_category_names)
        changes.append("store categories")
        offer_changed = True

    # --- escape hatch for anything else eBay supports ---
    if item_fields:
        item = _deep_merge(item, item_fields)
        changes.append("item fields: " + ", ".join(item_fields))
        item_changed = True
    if offer_fields:
        offer = _deep_merge(offer, offer_fields)
        changes.append("offer fields: " + ", ".join(offer_fields))
        offer_changed = True

    if not changes:
        raise RuntimeError("Nothing to change: pass at least one field")

    # Lowering quantity: shrink the offer first so it never asks for more than the item has.
    lowering = quantity is not None and old_qty is not None and int(quantity) < int(old_qty)
    steps = [("offer", offer_changed), ("item", item_changed)] if lowering else \
            [("item", item_changed), ("offer", offer_changed)]
    for which, needed in steps:
        if not needed:
            continue
        if which == "item":
            inv.put_inventory_item(cfg, sku, item)
        else:
            inv.update_offer(cfg, offer_id, offer)

    live = offer.get("status") == "PUBLISHED"
    listing_id = offer.get("listing", {}).get("listingId")
    return {
        "offer_id": offer_id,
        "sku": sku,
        "changed": changes,
        "live_listing_updated": live,
        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else None,
        "notes": notes + ([] if live else ["This is a draft: publish it to make the changes live"]),
    }


def end_listing(cfg, offer_id):
    """Take a live listing down. The offer is kept as a draft and can be republished."""
    offer_id = resolve_offer_id(cfg, offer_id)
    offer = inv.get_offer(cfg, offer_id)
    if offer.get("status") != "PUBLISHED":
        raise RuntimeError(f"Offer {offer_id} isn't live (status {offer.get('status')}); nothing to end")
    resp = inv.withdraw_offer(cfg, offer_id)
    return {"offer_id": offer_id, "sku": offer["sku"], "ended_listing_id": resp.get("listingId"),
            "note": "Listing ended. The draft is kept; ebay_publish_offer relists it."}


def delete_draft(cfg, offer_id):
    """Delete an unpublished offer. Refuses live listings (end them first)."""
    offer_id = resolve_offer_id(cfg, offer_id)
    offer = inv.get_offer(cfg, offer_id)
    if offer.get("status") == "PUBLISHED":
        raise RuntimeError(f"Offer {offer_id} is live. End it with ebay_end_listing before deleting")
    inv.delete_offer(cfg, offer_id)
    return {"offer_id": offer_id, "sku": offer["sku"], "deleted": True}

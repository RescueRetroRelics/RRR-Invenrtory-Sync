"""RRR eBay MCP server: lets Claude Desktop create, publish, edit and end eBay listings.

Listings created through eBay's Inventory API can't be edited in Seller Hub, so
every change to them (price, shipping, photos, specifics...) goes through here.

Run:   python rrr_ebay_mcp.py          (stdio transport, which is what Claude Desktop uses)
Test:  python rrr_ebay_mcp.py --check  (one-shot auth + offers check, prints to the terminal)

stdout belongs to the MCP protocol. Never print() here: log to stderr instead.
"""

import logging
import sys
from typing import Optional, Union

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x renamed it
    from mcp.server.mcpserver import MCPServer as _Server

from rrr_sync import ebay, ebay_auth, ebay_inventory, ebay_listings
from rrr_sync.config import Config

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s rrr-ebay %(message)s")
log = logging.getLogger("rrr-ebay")

mcp = _Server("rrr-ebay")


def _cfg():
    return Config.load()


def _fail(tool, e):
    log.exception("%s failed", tool)
    return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def ebay_check_auth() -> dict:
    """Check the eBay connection: refreshes the token if needed and makes a real API call."""
    try:
        cfg = _cfg()
        ebay_auth.get_access_token(cfg)
        resp = ebay_inventory.call(cfg, "GET", "/inventory_item", {"limit": 1})
        return {
            "ok": True,
            "inventory_items": resp.get("total", 0),
            "refresh_token_valid_until": ebay_auth.refresh_token_expiry(),
        }
    except Exception as e:
        return _fail("ebay_check_auth", e)


@mcp.tool()
def ebay_get_active_offers(status: str = "") -> dict:
    """List eBay offers for every inventory SKU (eBay has no list-all endpoint).

    status: optional filter, "UNPUBLISHED" or "PUBLISHED".
    """
    try:
        offers, errors = ebay_inventory.all_offers(_cfg())
        rows = [ebay_listings.offer_summary(o) for o in offers]
        if status:
            rows = [r for r in rows if (r["status"] or "").upper() == status.upper()]
        return {"ok": True, "count": len(rows), "offers": rows, "sku_errors": errors}
    except Exception as e:
        return _fail("ebay_get_active_offers", e)


@mcp.tool()
def ebay_create_draft(
    shopify_product_id: str,
    category_id: str = "",
    quantity: Optional[int] = None,
    condition: str = "NEW",
    title: str = "",
    aspects: Optional[dict] = None,
    image_urls: Optional[list] = None,
) -> dict:
    """Create (or refresh) an unpublished eBay offer from a Shopify product.

    shopify_product_id: Shopify product GID or its number; the number becomes the eBay SKU.
    category_id: eBay leaf category ID (needed before publishing).
    quantity: defaults to Shopify's stock count.
    aspects: item specifics, e.g. {"Brand": "Funko", "Character": "Mickey Mouse"}.
    image_urls: photo URLs; defaults to the Shopify product's photos.
    Price is Shopify + $2, Best Offer on, RRR's standard policies.
    """
    try:
        result = ebay_listings.create_draft(
            _cfg(), shopify_product_id, category_id=category_id or None, quantity=quantity,
            condition=condition or "NEW", title=title or None, aspects=aspects, image_urls=image_urls,
        )
        return {"ok": True, **result}
    except Exception as e:
        return _fail("ebay_create_draft", e)


@mcp.tool()
def ebay_publish_offer(
    offer_id: str,
    image_urls: Optional[list] = None,
    aspects: Optional[dict] = None,
    category_id: str = "",
) -> dict:
    """Attach photos and RRR's standard listing policies to an offer, then publish it live.

    offer_id: the eBay offer ID (from ebay_get_active_offers).
    image_urls: list of HTTPS photo URLs. Leave empty to pull them from the Shopify
      product whose ID matches the offer's SKU.
    aspects: item specifics to add if eBay says one is missing, e.g. {"Brand": "Funko"}.
    category_id: set/override the eBay category if the offer has none.
    Also enforces price = Shopify + $2. Shipping/payment/return policies and Best
    Offer settings already chosen for the draft (via ebay_update_listing) are kept;
    missing ones get RRR's defaults.

    If eBay rejects it, nothing goes live. Common fixes:
      "not a leaf category"           -> ebay_suggest_category, then retry with category_id
      "The item specific X is missing" -> retry with aspects={"X": "value"}
    """
    try:
        result = ebay_listings.publish(
            _cfg(), offer_id, image_urls=image_urls, aspects=aspects, category_id=category_id or None,
        )
        return {"ok": True, **result}
    except Exception as e:
        return _fail("ebay_publish_offer", e)


@mcp.tool()
def ebay_get_listing(offer_id: str) -> dict:
    """Show everything about one listing: title, description, price, quantity, photos,
    item specifics, condition, category, shipping/payment/return policy IDs, shipping
    cost overrides, Best Offer settings, package size, status and eBay link.

    offer_id: the offer ID, the eBay item number (or its ebay.com/itm link), or the SKU.
    Call this before editing so you can show Stephanie what's there now.
    """
    try:
        return {"ok": True, **ebay_listings.get_listing(_cfg(), offer_id)}
    except Exception as e:
        return _fail("ebay_get_listing", e)


@mcp.tool()
def ebay_update_listing(
    offer_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    price: Optional[float] = None,
    sync_price_from_shopify: bool = False,
    quantity: Optional[int] = None,
    image_urls: Optional[list] = None,
    refresh_photos_from_shopify: bool = False,
    aspects: Optional[dict] = None,
    remove_aspects: Optional[list] = None,
    condition: Optional[str] = None,
    condition_description: Optional[str] = None,
    category_id: Optional[str] = None,
    fulfillment_policy_id: Optional[str] = None,
    payment_policy_id: Optional[str] = None,
    return_policy_id: Optional[str] = None,
    shipping_cost: Optional[Union[float, str]] = None,
    best_offer: Optional[bool] = None,
    auto_accept_price: Optional[Union[float, str]] = None,
    auto_decline_price: Optional[Union[float, str]] = None,
    weight_oz: Optional[float] = None,
    length_in: Optional[float] = None,
    width_in: Optional[float] = None,
    height_in: Optional[float] = None,
    quantity_limit_per_buyer: Optional[Union[int, str]] = None,
    store_category_names: Optional[list] = None,
    item_fields: Optional[dict] = None,
    offer_fields: Optional[dict] = None,
) -> dict:
    """Change anything on a listing. Live listings update on eBay right away; drafts
    keep the change until ebay_publish_offer. Pass only what should change.

    offer_id: offer ID, eBay item number / ebay.com/itm link, or SKU.
    title (max 80 chars), description (HTML ok).
    price: exact price. RRR's rule is Shopify + $2; use sync_price_from_shopify=True
      to apply it, and a custom price only when Stephanie names one.
    quantity: stock count (0 may end the listing unless Out-of-stock control is on).
    image_urls: full replacement photo list, first = main photo (max 24).
      refresh_photos_from_shopify=True re-pulls them from the Shopify product.
    aspects: item specifics to add/change, e.g. {"Brand": "Funko", "Color": ["Red", "Black"]}.
      remove_aspects: names to delete.
    condition: NEW, LIKE_NEW, NEW_OTHER, NEW_WITH_DEFECTS, USED_EXCELLENT, USED_VERY_GOOD,
      USED_GOOD, USED_ACCEPTABLE, FOR_PARTS_OR_NOT_WORKING (and refurbished grades).
      condition_description: seller notes on condition ("" removes them).
    category_id: leaf category (see ebay_suggest_category).
    fulfillment_policy_id / payment_policy_id / return_policy_id: switch business
      policies; get IDs from ebay_list_policies. Shipping policies themselves are
      created/edited in Seller Hub (Account > Business policies), which still works.
    shipping_cost: override the policy's first domestic shipping service cost for this
      listing only (0 = free shipping); "default" removes the override.
    best_offer: True/False. auto_accept_price / auto_decline_price: dollar amounts,
      "none" removes. RRR's default is Best Offer on with no auto-decline floor.
    weight_oz, length_in, width_in, height_in: package weight/size (needed for
      calculated shipping).
    quantity_limit_per_buyer ("none" removes), store_category_names (eBay Store categories).
    item_fields / offer_fields: raw eBay Inventory API fields merged into the inventory
      item / offer for anything not covered above (advanced).

    Confirm with Stephanie before changing anything on a live listing.
    """
    try:
        result = ebay_listings.update_listing(
            _cfg(), offer_id, title=title, description=description, price=price,
            sync_price_from_shopify=sync_price_from_shopify, quantity=quantity,
            image_urls=image_urls, refresh_photos_from_shopify=refresh_photos_from_shopify,
            aspects=aspects, remove_aspects=remove_aspects, condition=condition,
            condition_description=condition_description, category_id=category_id,
            fulfillment_policy_id=fulfillment_policy_id, payment_policy_id=payment_policy_id,
            return_policy_id=return_policy_id, shipping_cost=shipping_cost, best_offer=best_offer,
            auto_accept_price=auto_accept_price, auto_decline_price=auto_decline_price,
            weight_oz=weight_oz, length_in=length_in, width_in=width_in, height_in=height_in,
            quantity_limit_per_buyer=quantity_limit_per_buyer,
            store_category_names=store_category_names, item_fields=item_fields,
            offer_fields=offer_fields,
        )
        return {"ok": True, **result}
    except Exception as e:
        return _fail("ebay_update_listing", e)


@mcp.tool()
def ebay_list_policies() -> dict:
    """Stephanie's eBay business policies with their IDs: shipping (services, costs,
    handling time), payment and returns. Use the IDs with ebay_update_listing.
    RRR defaults: shipping 284028239015 (USPS Ground Advantage $8.99),
    payment 283965435015, returns 291780816015 (no returns)."""
    try:
        return {"ok": True, **ebay_inventory.business_policies(_cfg())}
    except Exception as e:
        return _fail("ebay_list_policies", e)


@mcp.tool()
def ebay_end_listing(offer_id: str) -> dict:
    """End (take down) a live listing. The draft is kept and ebay_publish_offer relists it.
    offer_id: offer ID, eBay item number / link, or SKU.
    Only call this after Stephanie clearly says to end that specific listing."""
    try:
        return {"ok": True, **ebay_listings.end_listing(_cfg(), offer_id)}
    except Exception as e:
        return _fail("ebay_end_listing", e)


@mcp.tool()
def ebay_delete_draft(offer_id: str) -> dict:
    """Permanently delete an UNPUBLISHED offer (e.g. a duplicate draft). Refuses live
    listings. Only call this after Stephanie confirms that specific draft."""
    try:
        return {"ok": True, **ebay_listings.delete_draft(_cfg(), offer_id)}
    except Exception as e:
        return _fail("ebay_delete_draft", e)


@mcp.tool()
def ebay_suggest_category(query: str) -> dict:
    """eBay's suggested leaf categories for a product, e.g. "Chucky lip gloss keychain set".

    Use when publishing fails with "not a leaf category". Returns category_id + full path.
    """
    try:
        return {"ok": True, "suggestions": ebay_inventory.category_suggestions(_cfg(), query)}
    except Exception as e:
        return _fail("ebay_suggest_category", e)


@mcp.tool()
def ebay_get_active_listings() -> dict:
    """Every live eBay listing, including ones made in Seller Hub (Trading API)."""
    try:
        listings = ebay.active_listings(_cfg())
        return {"ok": True, "count": len(listings), "listings": [
            {"item_id": l.item_id, "sku": l.sku, "title": l.title, "available": l.available}
            for l in listings
        ]}
    except Exception as e:
        return _fail("ebay_get_active_listings", e)


@mcp.tool()
def ebay_get_orders(since: str = "2026-01-01") -> dict:
    """eBay orders created on or after `since` (YYYY-MM-DD, up to two years back)."""
    try:
        orders = ebay.orders_since(_cfg(), f"{since}T00:00:00.000Z")
        return {"ok": True, "count": len(orders), "orders": [
            {
                "order_id": o.get("orderId"),
                "created": o.get("creationDate"),
                "status": o.get("orderFulfillmentStatus"),
                "total": o.get("pricingSummary", {}).get("total", {}).get("value"),
                "buyer": o.get("buyer", {}).get("username"),
                "items": [li.get("title") for li in o.get("lineItems", [])],
            }
            for o in orders
        ]}
    except Exception as e:
        return _fail("ebay_get_orders", e)


def _self_check():
    """Terminal smoke test: real eBay calls, results on stderr."""
    auth = ebay_check_auth()
    log.info("ebay_check_auth: %s", auth)
    if not auth["ok"]:
        return 1
    offers = ebay_get_active_offers()
    if not offers["ok"]:
        log.info("ebay_get_active_offers: %s", offers)
        return 1
    log.info("ebay_get_active_offers: %d offers, %d SKU errors", offers["count"], len(offers["sku_errors"]))
    for o in offers["offers"]:
        log.info("  %-14s %-12s %-8s $%-7s %s", o["offer_id"], o["status"], o["sku"], o["price"], o["title"][:50])
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_self_check())
    log.info("starting MCP server on stdio")
    mcp.run()

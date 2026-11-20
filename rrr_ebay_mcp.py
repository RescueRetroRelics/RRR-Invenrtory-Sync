"""RRR eBay MCP server: lets Claude Desktop create, finish and publish eBay listings.

Run:   python rrr_ebay_mcp.py          (stdio transport, which is what Claude Desktop uses)
Test:  python rrr_ebay_mcp.py --check  (one-shot auth + offers check, prints to the terminal)

stdout belongs to the MCP protocol. Never print() here: log to stderr instead.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ImportError:  # mcp 2.x renamed it
    from mcp.server.mcpserver import MCPServer as _Server

from rrr_sync import ebay, ebay_auth, ebay_inventory, ebay_listings, ebay_specifics, ebay_store
from rrr_sync.config import Config

try:   # needs: pip install openpyxl
    from listing.publisher import Publisher
except ImportError:
    Publisher = None

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s rrr-ebay %(message)s")
log = logging.getLogger("rrr-ebay")

REPORTS = Path(__file__).resolve().parent / "reports"   # CSVs Stephanie opens in Excel

RULES = """Rules for Stephanie's eBay listings. They are not optional.
1. Never invent or "fill in" an ID, SKU, item number, title, price, category or URL. Every one
   you show her must come from a tool result in this conversation. If a tool failed, say it
   FAILED and show the error. Never present a failure as a success.
2. Never write a listing summary, sheet or table yourself. Call ebay_listing_report and show
   its rows and CSV path exactly as returned.
3. Price: only what Stephanie says. Never derive it from Shopify. Ask if she hasn't said.
4. eBay category: only an ID from ebay_suggest_category for that item. Show her the category
   NAME. category_confirmed=true only if she approved that exact name.
5. Store category: every listing needs one (ebay_store_categories). Ask her if unsure.
6. Item specifics: fill every REQUIRED and as many recommended ones as the item supports
   (ebay_category_specifics). Only facts she gave you or that are in the Shopify listing.
7. Before ebay_publish_offer: run ebay_listing_report on the offer, show her, and publish
   only after she says go. She can't edit API listings by hand, so they must be right.
8. Bulk fixes (store categories, item specifics): build the sheet, she reviews it, dry run
   (confirm=false), show the summary, and only then confirm=true when she says so.
9. Listing sheet: she hands you ONE sheet. Work through ALL of it on your own in batches
   (listing_sheet_rows / listing_sheet_fill, following next_offset until it's null) without
   stopping to ask between batches. Then one dry run of the whole sheet, one short summary
   (how many READY, what's NOT READY and why). Her single "go" covers the whole sheet:
   call listing_sheet_run(confirm=true, limit=5) again and again until ready_rows_left is 0,
   with a one-line progress note each time. Report DONE/FAILED counts and links at the end."""

try:
    mcp = _Server("rrr-ebay", instructions=RULES)
except TypeError:   # older/newer mcp without instructions=
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
    price: str,
    category_id: str = "",
    quantity: Optional[int] = None,
    condition: str = "NEW",
    title: str = "",
    aspects: Optional[dict] = None,
    image_urls: Optional[list] = None,
    store_category: str = "",
    category_confirmed: bool = False,
) -> dict:
    """Create (or refresh) an unpublished eBay offer from a Shopify product.

    shopify_product_id: Shopify product GID or its number; the number becomes the eBay SKU.
    price: the eBay price, exactly as Stephanie gives it (e.g. "24.99"). REQUIRED.
      Never work it out from the Shopify price. If she hasn't said, ask her.
    category_id: eBay leaf category ID (needed before publishing).
    quantity: defaults to 1. eBay always shows 1; the inventory sync relists as Shopify sells down.
    aspects: item specifics, e.g. {"Brand": "Funko", "Character": "Mickey Mouse"}.
    image_urls: photo URLs; defaults to the Shopify product's photos.
    store_category: Stephanie's eBay store category (name or path from ebay_store_categories).
    category_confirmed: true ONLY if Stephanie approved a category eBay doesn't suggest.
    Refuses a category eBay doesn't suggest for the item. Best Offer on, RRR's standard policies.
    Report the returned values exactly; never retype or invent IDs.
    """
    try:
        result = ebay_listings.create_draft(
            _cfg(), shopify_product_id, price, category_id=category_id or None, quantity=quantity,
            condition=condition or "NEW", title=title or None, aspects=aspects, image_urls=image_urls,
            store_category=store_category or None, category_confirmed=category_confirmed,
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
    price: str = "",
    store_category: str = "",
    category_confirmed: bool = False,
) -> dict:
    """Attach photos and RRR's standard listing policies to an offer, then publish it live.

    offer_id: the eBay offer ID (from ebay_get_active_offers).
    image_urls: list of HTTPS photo URLs. Leave empty to pull them from the Shopify
      product whose ID matches the offer's SKU.
    aspects: item specifics to add if eBay says one is missing, e.g. {"Brand": "Funko"}.
    category_id: set/override the eBay category if the offer has none.
    price: optional new eBay price from Stephanie. Leave empty to keep the offer's price.
      Never work a price out from Shopify.
    store_category: required unless the offer already has one (else it lands in "Other").
    category_confirmed: true ONLY if Stephanie approved a category eBay doesn't suggest.
    Also turns Best Offer on. Only call after Stephanie has seen ebay_listing_report and said go.

    If eBay rejects it, nothing goes live. Common fixes:
      "not a leaf category"           -> ebay_suggest_category, then retry with category_id
      "The item specific X is missing" -> retry with aspects={"X": "value"}
    """
    try:
        result = ebay_listings.publish(
            _cfg(), offer_id, image_urls=image_urls, aspects=aspects, category_id=category_id or None,
            price=price or None, store_category=store_category or None,
            category_confirmed=category_confirmed,
        )
        return {"ok": True, **result}
    except Exception as e:
        return _fail("ebay_publish_offer", e)


@mcp.tool()
def ebay_update_price(offer_id: str, price: str) -> dict:
    """Change an eBay offer's price to what Stephanie says. A live listing updates right away.

    offer_id: from ebay_get_active_offers. price: e.g. "24.99".
    """
    try:
        return {"ok": True, **ebay_listings.update_price(_cfg(), offer_id, price)}
    except Exception as e:
        return _fail("ebay_update_price", e)


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


@mcp.tool()
def ebay_listing_report(offer_ids: Optional[list] = None, skus: Optional[list] = None) -> dict:
    """THE listing sheet: live data from eBay + Shopify, saved as a CSV for Excel.

    offer_ids / skus: which listings (both empty = every offer). Show the rows exactly as
    returned. This is the only allowed way to summarize listings for Stephanie.
    """
    try:
        rows, path = ebay_listings.listing_report(_cfg(), offer_ids, skus, out_dir=REPORTS)
        return {"ok": True, "count": len(rows), "csv": path, "rows": rows}
    except Exception as e:
        return _fail("ebay_listing_report", e)


@mcp.tool()
def ebay_store_categories() -> dict:
    """Stephanie's eBay store categories (her store's shelves). Use a path for store_category."""
    try:
        cats = ebay_store.store_categories(_cfg())
        return {"ok": True, "categories": [c for c in cats if c["leaf"]]}
    except Exception as e:
        return _fail("ebay_store_categories", e)


@mcp.tool()
def ebay_category_specifics(category_id: str) -> dict:
    """Item specifics eBay requires/recommends for an eBay category, with allowed values."""
    try:
        return {"ok": True, "specifics": ebay_inventory.item_aspects(_cfg(), category_id)}
    except Exception as e:
        return _fail("ebay_category_specifics", e)


@mcp.tool()
def ebay_store_category_sheet(only_other: bool = True) -> dict:
    """Bulk fix step 1: CSV of live listings in the store's "Other" shelf, with a suggested
    shelf where the title makes it obvious. Stephanie reviews/fills new_category in Excel."""
    try:
        path, counts = ebay_store.build_sheet(_cfg(), REPORTS, only_other=only_other)
        return {"ok": True, "csv": path, **counts}
    except Exception as e:
        return _fail("ebay_store_category_sheet", e)


@mcp.tool()
def ebay_apply_store_categories(csv_path: str, confirm: bool = False) -> dict:
    """Bulk fix step 2: move listings to the new_category in a reviewed sheet.

    confirm=false (default) is a dry run that changes nothing. confirm=true only after
    Stephanie has seen the dry-run summary and said go.
    """
    try:
        return {"ok": True, **ebay_store.apply_sheet(_cfg(), csv_path, confirm=confirm)}
    except Exception as e:
        return _fail("ebay_apply_store_categories", e)


@mcp.tool()
def ebay_item_specifics_sheet(include_optional: bool = False) -> dict:
    """Bulk fix step 1: CSV of every live API listing's missing required/recommended item
    specifics, with eBay's allowed values and a title-based guess where obvious.
    Stephanie (or you, with her checking) fills new_value. Use | between multiple values."""
    try:
        path, stats = ebay_specifics.build_sheet(_cfg(), REPORTS, include_optional=include_optional)
        return {"ok": True, "csv": path, **stats}
    except Exception as e:
        return _fail("ebay_item_specifics_sheet", e)


@mcp.tool()
def ebay_apply_item_specifics(csv_path: str, confirm: bool = False) -> dict:
    """Bulk fix step 2: write the filled-in specifics to the live listings.

    Checks every value against eBay's rules first; a listing with any bad value is skipped
    whole. confirm=false (default) is a dry run. confirm=true only after Stephanie says go.
    """
    try:
        return {"ok": True, **ebay_specifics.apply_sheet(_cfg(), csv_path, confirm=confirm)}
    except Exception as e:
        return _fail("ebay_apply_item_specifics", e)


@mcp.tool()
def ebay_listings_to_fix(offset: int = 0, limit: int = 20) -> dict:
    """FAST FIX step 1: next batch of live listings missing item specifics or a store shelf.

    For each: title, eBay category, Shopify description, specifics it has, and the
    required/recommended specifics it's missing (with eBay's allowed values).
    Fill values ONLY from the title, the Shopify description, or what Stephanie told you.
    Leave out anything you'd be guessing. Then call ebay_fix_listings. Use next_offset for
    the next batch until it's null.
    """
    try:
        return {"ok": True, **ebay_specifics.listings_to_fix(_cfg(), offset=offset, limit=limit)}
    except Exception as e:
        return _fail("ebay_listings_to_fix", e)


@mcp.tool()
def ebay_fix_listings(fixes: list, confirm: bool = False) -> dict:
    """FAST FIX step 2: add item specifics and a store shelf to live listings, no relisting.

    fixes: [{"sku": "...", "specifics": {"Brand": "Funko", "Character": ["Snoopy", "Lucy"]},
             "store_category": "/Peanuts"}]
    Every value is checked against eBay's rules; a listing with a bad value is skipped and
    reported. confirm=false = dry run. Show Stephanie the dry run as a short table (sku,
    title, what's being added); on her go, call again with confirm=true. Stephanie has said
    she wants these done fast: after her first OK she may approve whole batches at once.
    """
    try:
        return {"ok": True, **ebay_specifics.fix_listings(_cfg(), fixes, confirm=confirm)}
    except Exception as e:
        return _fail("ebay_fix_listings", e)


DEFAULT_SHEET = Path(__file__).resolve().parent / "RRR_Listing_Sheet.xlsx"


def _publisher(path):
    if Publisher is None:
        raise RuntimeError("Run this once in a terminal: pip install openpyxl")
    return Publisher(_cfg(), path or str(DEFAULT_SHEET))


@mcp.tool()
def listing_sheet_rows(sheet_path: str = "", offset: int = 0, limit: int = 10) -> dict:
    """LISTING SHEET step 1: rows still needing titles/SEO/description/specifics.

    Call with offset=0, fill that batch, then call again with next_offset until next_offset
    is null. Do the whole sheet without stopping to ask Stephanie between batches.

    Each row has Stephanie's notes, prices and dropdown picks, plus the eBay specifics
    that row's eBay category needs (with allowed values). Write from her notes and the
    photos folder name only: never invent details (sizes, years, materials) she didn't give.
    sheet_path defaults to RRR_Listing_Sheet.xlsx next to this tool.
    """
    try:
        return {"ok": True, **_publisher(sheet_path).rows_to_fill(offset, limit)}
    except Exception as e:
        return _fail("listing_sheet_rows", e)


@mcp.tool()
def listing_sheet_fill(fills: list, sheet_path: str = "") -> dict:
    """LISTING SHEET step 2: write the blue columns for rows from listing_sheet_rows.

    fills: [{"row": 2, "Brand": "Funko", "Title": "...", "eBay title": "... (max 80)",
             "SEO title": "... (max 70)", "SEO description": "... (max 160)",
             "Description": "...", "Product type": "...", "Tags": "tag, tag",
             "eBay specifics": "Brand: Funko | Character: Snoopy; Woodstock | Type: Vinyl Figure"}]
    WRITING RULES (Google, AI shopping like ChatGPT/Gemini, and eBay Cassini):
    - eBay title: use as much of the 80 characters as the facts allow, keywords first:
      Brand, Franchise, Character, item type, key details (size, set count, edition), NEW.
      No filler (L@@K, WOW, rare, cute), no repeated words, no punctuation clutter.
    - Title (Shopify): Brand + Franchise + Character + item type + key detail, readable.
    - SEO title: 50-60 characters, Brand + Character + item type. No store name.
    - SEO description: 140-160 characters: what it is, who it's a gift for, then
      "Ships fast from Michigan. Free shipping $65+." if it fits.
    - Description: FIRST sentence says plainly what it is (brand, character, item type, size):
      AI tools quote it. Then a fact list, one per line starting with "- ": Brand, Franchise,
      Character, Size (only if given), What's included, Condition. Then one line on who it's a
      great gift for. Short. Match her voice: warm, kawaii, gift-focused. No made-up facts.
    - Bundle / gift set = Yes: say it's a curated gift set in the title and first sentence, and
      list every item under "Includes:" as "- " lines. Put every character and franchise in
      the eBay specifics (multiple values with ;). Never give a bundle a barcode/MPN.
    - Product type: short noun phrase like her existing ones ("Snoopy Plush Keychain").
    - Tags: franchise, characters, item type, gift terms. Her standard RRR tags are added
      automatically.
    Fill every REQUIRED eBay specific, and every one marked "Stephanie fills this" that her
    notes support (Franchise, TV Show, Movie, Character, Animation Studio, Type, Features,
    Unit Quantity, Unit Type...), using only allowed values where only_these is true.
    ebay_specifics_from_sheet are added automatically from her dropdowns: don't repeat them.
    "Other category fields": Shopify category fields for that row's Shopify category
    (shopify_category_fields), as "Field: value; value | Field: value". Prefer values_in_store.
    Only facts from her notes (e.g. Power source for a night light).
    Never fill dimensions, year, country or MPN unless she wrote them. Never set a Custom
    SKU: the tool always uses the Shopify product number. Tell her to close the sheet in
    Excel first.
    """
    try:
        return {"ok": True, **_publisher(sheet_path).fill(fills)}
    except Exception as e:
        return _fail("listing_sheet_fill", e)


@mcp.tool()
def listing_sheet_run(confirm: bool = False, limit: int = 0, rows: Optional[list] = None,
                      sheet_path: str = "") -> dict:
    """LISTING SHEET step 3: list rows on Shopify AND eBay.

    confirm=false (default): checks every row and marks it READY or NOT READY (with the
    reasons) in the sheet. Nothing is created. confirm=true: creates each READY row on
    Shopify (photos, SEO, metafields, all sales channels) then eBay, writing DONE / FAILED
    and links into the sheet. Reruns skip DONE rows. limit: max rows this run (0 = all).
    rows: specific sheet row numbers. Only confirm=true after Stephanie says go.
    confirm=true only lists rows the dry run marked READY (plus SHOPIFY ONLY retries).
    For a big sheet use limit=5 and repeat until ready_rows_left is 0: one "go" from her
    covers the whole sheet.
    """
    try:
        return {"ok": True, **_publisher(sheet_path).run(confirm=confirm, limit=limit or None, rows=rows)}
    except Exception as e:
        return _fail("listing_sheet_run", e)


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

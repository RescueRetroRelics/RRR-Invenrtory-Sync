"""eBay Sell Inventory API: inventory items, offers, locations, publishing.

Gotchas this module handles:
  * Every request carries Content-Language: en-US. Without it eBay answers
    PUT/POST calls with a 400 (errorId 25709 / "Invalid value for header").
  * There is no "list every offer" endpoint. GET /offer requires a sku, so
    all_offers() walks every inventory item and asks for its offers one SKU
    at a time. A SKU with no offers comes back as a 404, which means "none".
  * POST /offer/{id}/publish takes no body but still needs Content-Length: 0.
  * Listings made through this API can't be edited in Seller Hub. Changing the
    inventory item or the offer of a PUBLISHED offer revises the live listing,
    so every edit goes through here.
"""

import json
import urllib.parse

from . import http
from .ebay_auth import get_access_token

API = "https://api.ebay.com/sell/inventory/v1"
ACCOUNT_API = "https://api.ebay.com/sell/account/v1"
MARKETPLACE_ID = "EBAY_US"
OFFER_NOT_FOUND = 25713  # "This Offer is not available." (a SKU with no offers)

# Fields PUT /inventory_item/{sku} accepts. GET returns extras (sku, locale,
# groupIds, inventoryItemGroupKeys) that are read-only.
ITEM_WRITABLE = (
    "availability", "condition", "conditionDescription", "conditionDescriptors",
    "packageWeightAndSize", "product",
)

# Fields PUT /offer/{offerId} accepts. GET returns read-only extras
# (offerId, sku, marketplaceId, format, status, listing).
OFFER_WRITABLE = (
    "availableQuantity", "categoryId", "charity", "extendedProducerResponsibility",
    "hideBuyerDetails", "includeCatalogProductDetails", "listingDescription",
    "listingDuration", "listingPolicies", "listingStartDate", "lotSize",
    "merchantLocationKey", "pricingSummary", "quantityLimitPerBuyer", "regulatory",
    "secondaryCategoryId", "storeCategoryNames", "tax",
)


class EbayApiError(RuntimeError):
    def __init__(self, status, errors, url):
        self.status = status
        self.errors = errors
        msgs = []
        for e in errors:
            msg = f"[{e.get('errorId')}] {e.get('longMessage') or e.get('message')}"
            params = [f"{p.get('name')}={p.get('value')}" for p in e.get("parameters", [])]
            if params:
                msg += f" ({', '.join(params)})"
            msgs.append(msg)
        super().__init__(f"eBay HTTP {status}: " + (" | ".join(msgs) or url))

    def has_error_id(self, error_id):
        return any(e.get("errorId") == error_id for e in self.errors)


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
        "Accept-Language": "en-US",
        "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
    }


def call(cfg, method, path, params=None, payload=None, base=API):
    """One Sell API call (Inventory by default). Returns parsed JSON ({} for 204 No Content)."""
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(payload) if payload is not None else ("" if method in ("POST", "PUT") else None)
    try:
        _, text = http.request(method, url, headers=headers(get_access_token(cfg)), body=body)
    except http.HttpError as e:
        try:
            errors = json.loads(e.body).get("errors", [])
        except ValueError:
            errors = [{"message": e.body[:500]}]
        raise EbayApiError(e.status, errors, url) from None
    return json.loads(text) if text.strip() else {}


def _sku_path(sku):
    return urllib.parse.quote(str(sku), safe="")


# --- inventory items --------------------------------------------------------

def inventory_items(cfg):
    """Every inventory item on the account (paged, 100 at a time)."""
    items, offset = [], 0
    while True:
        resp = call(cfg, "GET", "/inventory_item", {"limit": 100, "offset": offset})
        batch = resp.get("inventoryItems", [])
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= resp.get("total", 0):
            return items


def get_inventory_item(cfg, sku):
    return call(cfg, "GET", f"/inventory_item/{_sku_path(sku)}")


def put_inventory_item(cfg, sku, item):
    """Create or fully replace an inventory item. Only writable fields are sent."""
    payload = {k: v for k, v in item.items() if k in ITEM_WRITABLE}
    return call(cfg, "PUT", f"/inventory_item/{_sku_path(sku)}", payload=payload)


# --- offers ------------------------------------------------------------------

def offers_for_sku(cfg, sku):
    try:
        resp = call(cfg, "GET", "/offer", {"sku": sku, "marketplace_id": MARKETPLACE_ID, "limit": 100})
    except EbayApiError as e:
        if e.status == 404 or e.has_error_id(OFFER_NOT_FOUND):
            return []
        raise
    return resp.get("offers", [])


def all_offers(cfg):
    """(offers, errors) for every SKU. One bad SKU doesn't sink the rest."""
    offers, errors = [], []
    for item in inventory_items(cfg):
        sku = item.get("sku")
        try:
            for offer in offers_for_sku(cfg, sku):
                offer["_title"] = item.get("product", {}).get("title", "")
                offers.append(offer)
        except EbayApiError as e:
            errors.append({"sku": sku, "error": str(e)})
    return offers, errors


def get_offer(cfg, offer_id):
    return call(cfg, "GET", f"/offer/{offer_id}")


def create_offer(cfg, payload):
    return call(cfg, "POST", "/offer", payload=payload)["offerId"]


def update_offer(cfg, offer_id, offer):
    """Fully replace an offer. Only writable fields are sent."""
    payload = {k: v for k, v in offer.items() if k in OFFER_WRITABLE}
    return call(cfg, "PUT", f"/offer/{offer_id}", payload=payload)


def publish_offer(cfg, offer_id):
    """Publish (or re-publish, which revises the live listing). Returns the response
    with listingId and any warnings."""
    return call(cfg, "POST", f"/offer/{offer_id}/publish")


def withdraw_offer(cfg, offer_id):
    """End the live listing. The offer goes back to UNPUBLISHED and can be republished."""
    return call(cfg, "POST", f"/offer/{offer_id}/withdraw")


def delete_offer(cfg, offer_id):
    return call(cfg, "DELETE", f"/offer/{offer_id}")


# --- business policies (Account API, read-only scope) -------------------------

def business_policies(cfg):
    """The seller's shipping, payment and return policies, summarized."""
    params = {"marketplace_id": MARKETPLACE_ID}
    shipping = []
    for p in call(cfg, "GET", "/fulfillment_policy", params, base=ACCOUNT_API).get("fulfillmentPolicies", []):
        services = []
        for opt in p.get("shippingOptions", []):
            for svc in sorted(opt.get("shippingServices", []), key=lambda s: s.get("sortOrder", 0)):
                services.append({
                    "type": opt.get("optionType"),           # DOMESTIC / INTERNATIONAL
                    "cost_type": opt.get("costType"),        # FLAT_RATE / CALCULATED
                    "service": svc.get("shippingServiceCode"),
                    "cost": svc.get("shippingCost", {}).get("value"),
                    "free": svc.get("freeShipping", False),
                    "priority": svc.get("sortOrder"),
                })
        shipping.append({
            "id": p.get("fulfillmentPolicyId"),
            "name": p.get("name"),
            "description": p.get("description", ""),
            "handling_days": p.get("handlingTime", {}).get("value"),
            "services": services,
        })
    payment = [
        {"id": p.get("paymentPolicyId"), "name": p.get("name"), "description": p.get("description", "")}
        for p in call(cfg, "GET", "/payment_policy", params, base=ACCOUNT_API).get("paymentPolicies", [])
    ]
    returns = [
        {"id": p.get("returnPolicyId"), "name": p.get("name"), "returns_accepted": p.get("returnsAccepted"),
         "description": p.get("description", "")}
        for p in call(cfg, "GET", "/return_policy", params, base=ACCOUNT_API).get("returnPolicies", [])
    ]
    return {"shipping": shipping, "payment": payment, "returns": returns}


# --- locations ---------------------------------------------------------------

def locations(cfg):
    return call(cfg, "GET", "/location", {"limit": 100}).get("locations", [])


# --- categories ----------------------------------------------------------------

TAXONOMY_API = "https://api.ebay.com/commerce/taxonomy/v1/category_tree/0"  # 0 = eBay US


def category_suggestions(cfg, query, limit=5):
    """Leaf categories eBay suggests for a product description, best match first."""
    url = TAXONOMY_API + "/get_category_suggestions?" + urllib.parse.urlencode({"q": query})
    try:
        _, text = http.request("GET", url, headers=headers(get_access_token(cfg)))
    except http.HttpError as e:
        raise EbayApiError(e.status, [{"message": e.body[:500]}], url) from None
    out = []
    for s in json.loads(text).get("categorySuggestions", [])[:limit]:
        ancestors = [a["categoryName"] for a in reversed(s.get("categoryTreeNodeAncestors", []))]
        out.append({
            "category_id": s["category"]["categoryId"],
            "path": " > ".join(ancestors + [s["category"]["categoryName"]]),
        })
    return out

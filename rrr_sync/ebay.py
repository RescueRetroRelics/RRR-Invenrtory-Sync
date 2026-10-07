"""eBay calls: active listings and quantity updates (Trading API), orders (Fulfillment API).

The Trading API is used for listings because it sees every active listing,
including ones created in Seller Hub. The newer Inventory API only sees
listings that were created through the Inventory API itself.
"""

import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import http
from .ebay_auth import get_access_token

TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
FULFILLMENT_ENDPOINT = "https://api.ebay.com/sell/fulfillment/v1/order"
COMPAT_LEVEL = "1349"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}


@dataclass
class EbayListing:
    item_id: str
    sku: str
    title: str        # listing title, plus variation specifics for variations
    available: int    # quantity a buyer can still purchase
    variation: bool = False

    @property
    def key(self):
        return f"{self.item_id}:{self.sku}" if self.variation else self.item_id


def _text(el, path, default=""):
    found = el.find(path, NS)
    return found.text.strip() if found is not None and found.text else default


def _int(el, path):
    try:
        return int(_text(el, path, "0"))
    except ValueError:
        return 0


def _trading_call(cfg, call_name, body_xml):
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<{call_name}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"{body_xml}<ErrorLanguage>en_US</ErrorLanguage><WarningLevel>High</WarningLevel>"
        f"</{call_name}Request>"
    )
    _, text = http.request(
        "POST",
        TRADING_ENDPOINT,
        headers={
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT_LEVEL,
            "X-EBAY-API-IAF-TOKEN": get_access_token(cfg),
            "Content-Type": "text/xml",
        },
        body=xml,
    )
    root = ET.fromstring(text)
    if _text(root, "e:Ack") == "Failure":
        errors = [_text(e, "e:LongMessage") for e in root.findall("e:Errors", NS)]
        raise RuntimeError(f"eBay {call_name} failed: " + " | ".join(errors))
    return root


def parse_active_list(root):
    """Turn a GetMyeBaySelling response into listings (one per variation)."""
    listings = []
    for item in root.findall("e:ActiveList/e:ItemArray/e:Item", NS):
        item_id = _text(item, "e:ItemID")
        title = _text(item, "e:Title")
        variations = item.findall("e:Variations/e:Variation", NS)
        if variations:
            for var in variations:
                specifics = [
                    _text(nv, "e:Value")
                    for nv in var.findall("e:VariationSpecifics/e:NameValueList", NS)
                ]
                qty = _int(var, "e:Quantity") - _int(var, "e:SellingStatus/e:QuantitySold")
                listings.append(EbayListing(
                    item_id=item_id,
                    sku=_text(var, "e:SKU"),
                    title=" ".join([title, *specifics]).strip(),
                    available=max(qty, 0),
                    variation=True,
                ))
        else:
            if item.find("e:QuantityAvailable", NS) is not None:
                qty = _int(item, "e:QuantityAvailable")
            else:
                qty = _int(item, "e:Quantity") - _int(item, "e:SellingStatus/e:QuantitySold")
            listings.append(EbayListing(item_id, _text(item, "e:SKU"), title, max(qty, 0)))
    pages = _int(root, "e:ActiveList/e:PaginationResult/e:TotalNumberOfPages")
    return listings, pages


def active_listings(cfg):
    listings, page, pages = [], 1, 1
    while page <= pages:
        root = _trading_call(cfg, "GetMyeBaySelling", (
            "<ActiveList><Include>true</Include>"
            f"<Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination>"
            "</ActiveList><DetailLevel>ReturnAll</DetailLevel>"
        ))
        batch, pages = parse_active_list(root)
        listings.extend(batch)
        page += 1
    return listings


def set_quantity(cfg, listing, qty):
    """Set the quantity a buyer can purchase on one listing (or one variation)."""
    sku = f"<SKU>{_xml_escape(listing.sku)}</SKU>" if listing.variation else ""
    _trading_call(cfg, "ReviseInventoryStatus", (
        f"<InventoryStatus><ItemID>{listing.item_id}</ItemID>{sku}"
        f"<Quantity>{int(qty)}</Quantity></InventoryStatus>"
    ))


def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def orders_since(cfg, since_iso):
    """All orders created on or after since_iso (e.g. 2026-01-01T00:00:00.000Z).

    The Fulfillment API returns orders up to two years old.
    """
    token = get_access_token(cfg)
    params = {"filter": f"creationdate:[{since_iso}..]", "limit": "200", "offset": "0"}
    url = FULFILLMENT_ENDPOINT + "?" + urllib.parse.urlencode(params)
    orders = []
    while url:
        resp = http.request_json("GET", url, headers={"Authorization": f"Bearer {token}"})
        orders.extend(resp.get("orders", []))
        url = resp.get("next")
    return orders

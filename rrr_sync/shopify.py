"""Shopify Admin GraphQL: read variant quantities and set them at one location."""

import json
import os
import time
import urllib.parse
import uuid
from dataclasses import dataclass

from . import http
from .config import SECRETS_DIR

TOKEN_FILE = SECRETS_DIR / "shopify_token.json"

VARIANTS_QUERY = """
query Variants($cursor: String, $loc: ID!) {
  productVariants(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id sku title barcode
      product { id title status }
      inventoryItem {
        id tracked
        inventoryLevel(locationId: $loc) { quantities(names: ["available"]) { name quantity } }
      }
    }
  }
}
"""

SET_QTY_MUTATION = """
mutation SetQty($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) {
    inventoryAdjustmentGroup { id }
    userErrors { field message code }
  }
}
"""


@dataclass
class ShopifyVariant:
    variant_id: str
    inventory_item_id: str
    sku: str
    title: str          # product title, plus variant title when not "Default Title"
    available: int | None  # None when not stocked at the sync location
    tracked: bool
    status: str


class Shopify:
    def __init__(self, cfg):
        cfg.require("shopify_domain", "shopify_location_id")
        if not (cfg.shopify_admin_token or (cfg.shopify_client_id and cfg.shopify_client_secret)):
            raise RuntimeError(
                "Set SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (Dev Dashboard app) "
                "or SHOPIFY_ADMIN_TOKEN in .env"
            )
        self.cfg = cfg
        self.url = f"https://{cfg.shopify_domain}/admin/api/{cfg.shopify_api_version}/graphql.json"

    def _token(self):
        if self.cfg.shopify_admin_token:
            return self.cfg.shopify_admin_token
        if TOKEN_FILE.exists():
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("expires_at", 0) > time.time() + 300:
                return cached["access_token"]
        _, text = http.request(
            "POST",
            f"https://{self.cfg.shopify_domain}/admin/oauth/access_token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.cfg.shopify_client_id,
                "client_secret": self.cfg.shopify_client_secret,
            }),
        )
        resp = json.loads(text)
        SECRETS_DIR.mkdir(exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({
            "access_token": resp["access_token"],
            "expires_at": time.time() + resp.get("expires_in", 86399),
        }))
        os.chmod(TOKEN_FILE, 0o600)
        return resp["access_token"]

    def graphql(self, query, variables=None):
        for attempt in range(5):
            resp = http.request_json(
                "POST", self.url,
                headers={"X-Shopify-Access-Token": self._token()},
                payload={"query": query, "variables": variables or {}},
            )
            errors = resp.get("errors") or []
            if any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors):
                time.sleep(2 ** attempt)
                continue
            if errors:
                raise RuntimeError(f"Shopify GraphQL error: {errors}")
            return resp["data"]
        raise RuntimeError("Shopify kept throttling the request")

    def shop_name(self):
        return self.graphql("{ shop { name myshopifyDomain } }")["shop"]

    def variants(self):
        out, cursor = [], None
        while True:
            data = self.graphql(VARIANTS_QUERY, {"cursor": cursor, "loc": self.cfg.shopify_location_id})
            conn = data["productVariants"]
            for n in conn["nodes"]:
                if n["product"]["status"] == "ARCHIVED":
                    continue
                title = n["product"]["title"]
                if n["title"] and n["title"] != "Default Title":
                    title = f"{title} {n['title']}"
                level = n["inventoryItem"]["inventoryLevel"]
                qty = None
                if level:
                    qty = next((q["quantity"] for q in level["quantities"] if q["name"] == "available"), None)
                out.append(ShopifyVariant(
                    variant_id=n["id"],
                    inventory_item_id=n["inventoryItem"]["id"],
                    sku=(n["sku"] or "").strip(),
                    title=title,
                    available=qty,
                    tracked=n["inventoryItem"]["tracked"],
                    status=n["product"]["status"],
                ))
            if not conn["pageInfo"]["hasNextPage"]:
                return out
            cursor = conn["pageInfo"]["endCursor"]

    def set_available(self, variant, qty, reference):
        """Set one variant's available quantity, guarded by compare-and-swap on the
        value we read. Returns a list of error strings (empty on success)."""
        data = self.graphql(SET_QTY_MUTATION, {
            "key": str(uuid.uuid4()),
            "input": {
                "name": "available",
                "reason": "correction",
                "referenceDocumentUri": reference,
                "quantities": [{
                    "inventoryItemId": variant.inventory_item_id,
                    "locationId": self.cfg.shopify_location_id,
                    "quantity": int(qty),
                    "changeFromQuantity": variant.available,
                }],
            },
        })
        return [e["message"] for e in data["inventorySetQuantities"]["userErrors"]]

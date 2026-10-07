"""Export eBay orders to CSV (one row per line item) plus the raw JSON."""

import csv
import json

from .config import DATA_DIR

COLUMNS = [
    "order_id", "created", "payment_status", "fulfillment_status", "buyer",
    "order_total", "currency", "line_item_id", "item_id", "sku", "title",
    "quantity", "line_total",
]


def order_rows(orders):
    for o in orders:
        total = o.get("pricingSummary", {}).get("total", {})
        base = {
            "order_id": o.get("orderId", ""),
            "created": o.get("creationDate", ""),
            "payment_status": o.get("orderPaymentStatus", ""),
            "fulfillment_status": o.get("orderFulfillmentStatus", ""),
            "buyer": o.get("buyer", {}).get("username", ""),
            "order_total": total.get("value", ""),
            "currency": total.get("currency", ""),
        }
        for li in o.get("lineItems", []) or [{}]:
            yield {
                **base,
                "line_item_id": li.get("lineItemId", ""),
                "item_id": li.get("legacyItemId", ""),
                "sku": li.get("sku", ""),
                "title": li.get("title", ""),
                "quantity": li.get("quantity", ""),
                "line_total": li.get("total", {}).get("value", ""),
            }


def export(orders, since_date):
    DATA_DIR.mkdir(exist_ok=True)
    csv_path = DATA_DIR / f"ebay_orders_since_{since_date}.csv"
    json_path = DATA_DIR / f"ebay_orders_since_{since_date}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(order_rows(orders))
    json_path.write_text(json.dumps(orders, indent=2))
    return csv_path, json_path

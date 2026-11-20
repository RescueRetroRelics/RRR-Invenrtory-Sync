"""Create items on Shopify AND eBay from the listing sheet. No Shopify CSV import.

Per row:
  1. Pre-flight (nothing is created if any check fails): prices, qty, photos found,
     dropdown values valid, eBay category resolved, eBay specifics valid + required
     ones present, store shelf valid, titles within limits.
  2. Shopify: upload photos (staged uploads), productSet (title, description, SEO,
     category, metafields, tags, price, qty at the store location), publish to every
     sales channel.
  3. eBay: create the inventory item + offer from that Shopify product (same SKU rule
     as the rest of the tools) and publish it.
The sheet's gray columns record DONE / SHOPIFY ONLY / FAILED. A rerun skips DONE rows and
retries only the eBay half of SHOPIFY ONLY rows.
"""

import json
import mimetypes
import re
import time
import urllib.parse
import uuid
from pathlib import Path

from rrr_sync import ebay_inventory as inv
from rrr_sync import ebay_listings as el
from rrr_sync import ebay_specifics as spec
from rrr_sync import ebay_store, http
from rrr_sync.shopify import Shopify

from . import sheet as sheet_io

OPTIONS = Path(__file__).with_name("options.json")
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}
EBAY_CONDITIONS = {"new": "NEW", "new other (open box)": "NEW_OTHER", "used": "USED_EXCELLENT"}
LIMITS = {"eBay title": 80, "SEO title": 70, "SEO description": 160, "Title": 255}

STAGED = """mutation($input: [StagedUploadInput!]!) { stagedUploadsCreate(input: $input) {
  stagedTargets { url resourceUrl parameters { name value } } userErrors { field message } } }"""
PRODUCT_SET = """mutation($input: ProductSetInput!) { productSet(input: $input, synchronous: true) {
  product { id handle onlineStoreUrl } userErrors { field message } } }"""
PUBLICATIONS = "{ publications(first: 50) { nodes { id } } }"
PUBLISH = """mutation($id: ID!, $input: [PublicationInput!]!) { publishablePublish(id: $id, input: $input) {
  userErrors { field message } } }"""
CATEGORY_ATTRS = """query($id: ID!) { node(id: $id) { ... on TaxonomyCategory { attributes(first: 100) { nodes {
  ... on TaxonomyChoiceListAttribute { name } ... on TaxonomyMeasurementAttribute { name } } } } } }"""
METAFIELD_DEFS = """{ metafieldDefinitions(first: 100, ownerType: PRODUCT, namespace: "shopify") {
  nodes { name key validations { name value } } } }"""
DEF_VALUES = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on MetaobjectDefinition { id
  metaobjects(first: 100) { nodes { id displayName } } } } }"""
GOOGLE_AGE = {"Adults": "adult", "Teens": "teen", "All ages": "adult", "Universal": "adult"}
GOOGLE_CONDITION = {"new": "new", "new other (open box)": "new", "used": "used"}
GOOGLE_GENDER = {"Unisex": "unisex", "Female": "female"}
MEDIA_READY = """query($id: ID!) { product(id: $id) { media(first: 30) { nodes { status } } } }"""


def load_options():
    return json.loads(OPTIONS.read_text(encoding="utf-8"))


def photos_for(sheet_path, folder):
    d = Path(sheet_path).resolve().parent / "listing_photos" / str(folder).strip()
    if not folder or not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in PHOTO_EXT)


def parse_specifics(text):
    """'Brand: Funko | Character: Snoopy; Woodstock' -> {"Brand": "Funko", "Character": ["Snoopy", "Woodstock"]}"""
    out = {}
    for part in re.split(r"\s*\|\s*", text or ""):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        vals = [x.strip() for x in v.split(";") if x.strip()]
        if k.strip() and vals:
            out[k.strip()] = vals if len(vals) > 1 else vals[0]
    return out


def clean_barcode(v):
    """'0 12345 67890 5' -> '012345678905'. None if blank; ValueError if it can't be a barcode."""
    digits = re.sub(r"[\s-]", "", str(v or ""))
    if not digits:
        return None
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        raise ValueError(f'UPC/barcode "{v}" should be 8, 12, 13 or 14 digits')
    return digits


def description_html(text):
    """Plain text -> HTML. Lines starting with '-' or '•' become a bullet list."""
    if "<" in text:
        return text
    out, bullets = [], []
    for line in [l.strip() for l in text.split("\n") if l.strip()]:
        if line[0] in "-•*":
            bullets.append(f"<li>{line.lstrip('-•* ').strip()}</li>")
            continue
        if bullets:
            out.append("<ul>" + "".join(bullets) + "</ul>")
            bullets = []
        out.append(f"<p>{line}</p>")
    if bullets:
        out.append("<ul>" + "".join(bullets) + "</ul>")
    return "".join(out)


def _money(v):
    try:
        return el.ebay_price(v)
    except ValueError:
        return None


class Publisher:
    def __init__(self, cfg, sheet_path):
        self.cfg = cfg
        self.path = sheet_path
        self.opts = load_options()
        self._ebay_cat_ids = {}
        self._aspects = {}
        self._store_cats = None
        self._shop = None

    # --- lookups ---------------------------------------------------------------

    @property
    def shop(self):
        if self._shop is None:
            self._shop = Shopify(self.cfg)
        return self._shop

    def ebay_category_id(self, row):
        """(category_id, confirmed). From the dropdown = Stephanie's pick (confirmed).
        Blank = eBay's top suggestion for the eBay title (checked by the golf-club guard)."""
        choice = row.get("eBay category", "")
        if choice and not choice.startswith("("):
            path = self.opts["ebay_categories"].get(choice, choice)
            if path not in self._ebay_cat_ids:
                leaf = path.split(">")[-1].strip()
                hits = inv.category_suggestions(
                    self.cfg, f"{leaf} {row.get('eBay title') or row.get('Title') or row.get('What it is')}", limit=10)
                hits += inv.category_suggestions(self.cfg, path.replace(">", " "), limit=10)
                want = re.sub(r"\s+", " ", path).lower()
                match = next((h for h in hits if re.sub(r"\s+", " ", h["path"]).lower() == want), None)
                if not match:
                    raise ValueError(f'eBay category "{path}" could not be found on eBay')
                self._ebay_cat_ids[path] = match["category_id"]
            return self._ebay_cat_ids[path], True
        hits = inv.category_suggestions(
            self.cfg, row.get("eBay title") or row.get("Title") or row.get("What it is"), limit=1)
        if not hits:
            raise ValueError("eBay had no category suggestion; pick one in the eBay category column")
        return hits[0]["category_id"], False

    def store_fields(self):
        """{lowercase field name: {"key", "values": {display: gid}}} for every category field
        already set up in the store (values Stephanie has used before)."""
        if getattr(self, "_store_fields", None) is None:
            defs = self.shop.graphql(METAFIELD_DEFS)["metafieldDefinitions"]["nodes"]
            by_def = {}
            for d in defs:
                v = {x["name"]: x["value"] for x in d.get("validations", [])}.get("metaobject_definition_id")
                if v:
                    by_def[v] = d
            fields = {}
            ids = list(by_def)
            for i in range(0, len(ids), 5):   # keeps each request under Shopify's cost limit
                for n in self.shop.graphql(DEF_VALUES, {"ids": ids[i:i + 5]})["nodes"]:
                    if not n:
                        continue
                    d = by_def[n["id"]]
                    fields[d["name"].lower()] = {"name": d["name"], "key": d["key"], "values": {
                        m["displayName"]: m["id"] for m in n["metaobjects"]["nodes"]}}
            self._store_fields = fields
        return self._store_fields

    def category_fields(self, category_gid):
        if not category_gid:
            return []
        cache = self.__dict__.setdefault("_cat_fields", {})
        if category_gid not in cache:
            node = self.shop.graphql(CATEGORY_ATTRS, {"id": category_gid})["node"] or {}
            cache[category_gid] = [a["name"] for a in node.get("attributes", {}).get("nodes", []) if a.get("name")]
        return cache[category_gid]

    def aspects(self, category_id):
        if category_id not in self._aspects:
            self._aspects[category_id] = {a["name"].lower(): a for a in inv.item_aspects(self.cfg, category_id)}
        return self._aspects[category_id]

    def store_shelf(self, name):
        if self._store_cats is None:
            self._store_cats = ebay_store.store_categories(self.cfg)
        return ebay_store.resolve_store_category(self.cfg, name, self._store_cats)["path"]

    SHEET_TO_EBAY = {   # sheet column(s) -> eBay specific, used when the category has it
        "Brand": ("Brand",), "Color": ("Color", "Color 2"), "Material": ("Material", "Material 2"),
        "Theme": ("Theme",), "Item Height": ("Height (in)",), "Item Length": ("Length (in)",),
        "Item Width": ("Width (in)",), "Country of Origin": ("Country of origin",),
        "Year Manufactured": ("Year made",), "MPN": ("MPN",),
    }

    def sheet_specifics(self, row, rules):
        out = {}
        for name, cols in self.SHEET_TO_EBAY.items():
            a = rules.get(name.lower())
            vals = [row[c] for c in cols if row.get(c)]
            if not a or not vals:
                continue
            if name.startswith("Item ") and not any(u in vals[0].lower() for u in ("in", "cm")):
                vals = [f"{vals[0]} in"]
            if a["mode"] == "SELECTION_ONLY":
                allowed = {v.lower() for v in a["values"]}
                vals = [v for v in vals if v.lower() in allowed]
            if vals:
                out[a["name"]] = vals if (a["multi"] and len(vals) > 1) else vals[0]
        return out

    # --- pre-flight ------------------------------------------------------------

    def preflight(self, row, need_shopify=True):
        """Returns (plan, problems). Makes no changes anywhere."""
        problems, plan = [], {"row": row["_row"]}
        for col in ("Title", "eBay title", "SEO title", "SEO description", "Description"):
            if not row.get(col):
                problems.append(f"{col} is empty (ask Claude to fill the sheet)")
        for col, n in LIMITS.items():
            if len(row.get(col, "")) > n:
                problems.append(f"{col} is {len(row[col])} characters (max {n})")
        plan["shopify_price"] = _money(row.get("Shopify price"))
        plan["ebay_price"] = _money(row.get("eBay price"))
        if need_shopify and not plan["shopify_price"]:
            problems.append("Shopify price missing")
        if not plan["ebay_price"]:
            problems.append("eBay price missing")
        try:
            plan["qty"] = int(float(row.get("Qty") or 0))
        except ValueError:
            plan["qty"] = 0
        if plan["qty"] < 1:
            problems.append("Qty must be 1 or more")
        if need_shopify:
            plan["photos"] = photos_for(self.path, row.get("Photos folder"))
            if not plan["photos"]:
                problems.append(f'No photos in listing_photos/{row.get("Photos folder") or "?"}')
            cat = row.get("Shopify category")
            if cat and cat not in self.opts["shopify_categories"]:
                problems.append(f'Shopify category "{cat}" isn\'t in the list')
            plan["category"] = self.opts["shopify_categories"].get(cat)
            plan["metafields"] = []
            for label, m in self.opts["metafields"].items():
                cols = m.get("multi_columns") or [label]
                gids = []
                for c in cols:
                    v = row.get(c)
                    if v:
                        if v not in m["values"]:
                            problems.append(f'{c}: "{v}" isn\'t an allowed value')
                        else:
                            gids.append(m["values"][v])
                if gids:
                    ns, key = m["key"].split(".", 1)
                    plan["metafields"].append({"namespace": ns, "key": key, "type": "list.metaobject_reference",
                                               "value": json.dumps(list(dict.fromkeys(gids)))})
            plan["warnings"] = []
            if row.get("Other category fields"):
                taken = {m["key"] for m in plan["metafields"]}
                try:
                    fields = self.store_fields()
                except Exception as e:
                    fields = {}
                    plan["warnings"].append(f"Couldn't read Shopify category fields: {e}"[:200])
                for name, vals in parse_specifics(row["Other category fields"]).items():
                    f = fields.get(name.lower())
                    vals = vals if isinstance(vals, list) else [vals]
                    if not f:
                        plan["warnings"].append(f'Shopify field "{name}" isn\'t set up in your store yet (set it once by hand)')
                        continue
                    if f["key"] in taken:
                        continue
                    lower = {k.lower(): g for k, g in f["values"].items()}
                    gids = [lower[v.lower()] for v in vals if v.lower() in lower]
                    unknown = [v for v in vals if v.lower() not in lower]
                    if unknown:
                        plan["warnings"].append(f'{f["name"]}: {", ".join(unknown)} not in your store yet (add it once by hand)')
                    if gids:
                        plan["metafields"].append({"namespace": "shopify", "key": f["key"],
                                                   "type": "list.metaobject_reference", "value": json.dumps(gids)})
            google = {"custom_product": ("boolean", "false"),
                      "condition": ("single_line_text_field",
                                    GOOGLE_CONDITION.get((row.get("Condition") or "New").lower(), "new"))}
            if GOOGLE_AGE.get(row.get("Age group")):
                google["age_group"] = ("single_line_text_field", GOOGLE_AGE[row["Age group"]])
            if GOOGLE_GENDER.get(row.get("Target gender")):
                google["gender"] = ("single_line_text_field", GOOGLE_GENDER[row["Target gender"]])
            if row.get("MPN"):
                google["mpn"] = ("single_line_text_field", row["MPN"])
            plan["metafields"] += [{"namespace": "mm-google-shopping", "key": k, "type": t, "value": v}
                                   for k, (t, v) in google.items()]
            try:
                plan["weight"] = float(row["Weight (lb)"]) if row.get("Weight (lb)") else None
            except ValueError:
                problems.append(f'Weight "{row["Weight (lb)"]}" isn\'t a number')
        try:
            plan["barcode"] = clean_barcode(row.get("UPC / barcode"))
        except ValueError as e:
            problems.append(str(e))
        if need_shopify and plan.get("barcode"):
            plan["metafields"].append({"namespace": "mm-google-shopping", "key": "gtin",
                                       "type": "single_line_text_field", "value": plan["barcode"]})
        cond = (row.get("Condition") or "New").lower()
        plan["condition"] = EBAY_CONDITIONS.get(cond)
        if not plan["condition"]:
            problems.append(f'Condition "{row.get("Condition")}" isn\'t in the list')
        if not row.get("eBay store category") or row["eBay store category"].startswith("("):
            problems.append("Pick an eBay store category")
        else:
            try:
                plan["shelf"] = self.store_shelf(row["eBay store category"])
            except ValueError as e:
                problems.append(str(e)[:200])
        try:
            plan["ebay_category"], plan["category_confirmed"] = self.ebay_category_id(row)
            rules = self.aspects(plan["ebay_category"])
            specifics = parse_specifics(row.get("eBay specifics"))
            specifics = {**self.sheet_specifics(row, rules), **specifics}
            clean, bad = spec.validate(rules, specifics)
            problems += bad
            have = {k.lower() for k in clean}
            missing = [a["name"] for a in rules.values() if a["required"] and a["name"].lower() not in have]
            if missing:
                problems.append(f"eBay requires: {', '.join(missing)}")
            plan["specifics"] = clean
        except Exception as e:
            problems.append(f"eBay category: {e}"[:250])
        return plan, problems

    # --- Shopify ---------------------------------------------------------------

    def upload_photos(self, files, alt):
        inputs = [{"filename": f.name, "mimeType": mimetypes.guess_type(f.name)[0] or "image/jpeg",
                   "resource": "IMAGE", "httpMethod": "POST", "fileSize": str(f.stat().st_size)} for f in files]
        data = self.shop.graphql(STAGED, {"input": inputs})["stagedUploadsCreate"]
        if data["userErrors"]:
            raise RuntimeError(f"Shopify photo upload: {data['userErrors']}")
        out = []
        for f, t in zip(files, data["stagedTargets"]):
            fields = [(p["name"], p["value"]) for p in t["parameters"]]
            body, ctype = _multipart(fields, f)
            http.request("POST", t["url"], headers={"Content-Type": ctype}, body=body, timeout=120)
            n = len(out) + 1
            out.append({"originalSource": t["resourceUrl"], "contentType": "IMAGE",
                        "alt": (alt if n == 1 else f"{alt} - photo {n}")[:120]})
        return out

    def create_shopify(self, row, plan):
        files = self.upload_photos(plan["photos"], row["Title"])
        tags = [t.strip() for t in re.split(r"[,|]", row.get("Tags", "")) if t.strip()]
        tags = list(dict.fromkeys(tags + self.opts["standard_tags"]))
        product = {
            "title": row["Title"],
            "descriptionHtml": description_html(row["Description"]),
            "vendor": row.get("Brand") or None,
            "productType": row.get("Product type") or None,
            "tags": tags,
            "status": "ACTIVE",
            "seo": {"title": row["SEO title"], "description": row["SEO description"]},
            "metafields": plan["metafields"],
            "files": files,
            "productOptions": [{"name": "Title", "values": [{"name": "Default Title"}]}],
            "variants": [{
                "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                "price": plan["shopify_price"],
                **({"barcodes": [{"type": {12: "UPC", 13: "EAN"}.get(len(plan["barcode"]), "GTIN"),
                                  "value": plan["barcode"]}]} if plan.get("barcode") else {}),
                "inventoryItem": {"tracked": True, **({"measurement": {"weight": {
                    "value": plan["weight"], "unit": "POUNDS"}}} if plan.get("weight") else {})},
                "inventoryQuantities": [{"locationId": self.cfg.shopify_location_id, "name": "available",
                                         "quantity": plan["qty"]}],
            }],
        }
        if plan.get("category"):
            product["category"] = plan["category"]
        product = {k: v for k, v in product.items() if v is not None}
        res = self.shop.graphql(PRODUCT_SET, {"input": product})["productSet"]
        if res["userErrors"]:
            raise RuntimeError(f"Shopify said: {res['userErrors']}")
        pid = res["product"]["id"]
        pubs = [{"publicationId": p["id"]} for p in self.shop.graphql(PUBLICATIONS)["publications"]["nodes"]]
        if pubs:
            errs = self.shop.graphql(PUBLISH, {"id": pid, "input": pubs})["publishablePublish"]["userErrors"]
            if errs:
                raise RuntimeError(f"Created, but publishing to sales channels failed: {errs}")
        domain = self.cfg.shopify_domain
        return pid, f"https://{domain}/admin/products/{el.sku_from_shopify_id(pid)}"

    def wait_for_photos(self, pid, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            nodes = self.shop.graphql(MEDIA_READY, {"id": pid})["product"]["media"]["nodes"]
            if nodes and all(n["status"] == "READY" for n in nodes):
                return True
            if any(n["status"] == "FAILED" for n in nodes):
                raise RuntimeError("Shopify couldn't process a photo")
            time.sleep(3)
        return False

    # --- eBay ------------------------------------------------------------------

    def create_ebay(self, row, plan, pid):
        draft = el.create_draft(
            self.cfg, pid, plan["ebay_price"], category_id=plan["ebay_category"], quantity=1,
            condition=plan["condition"], title=row["eBay title"], aspects=plan["specifics"],
            category_confirmed=plan["category_confirmed"], store_category=plan["shelf"],
            barcode=plan.get("barcode"),
        )
        pub = el.publish(self.cfg, draft["offer_id"], category_confirmed=plan["category_confirmed"])
        return pub["url"]

    # --- Claude fills the blue columns -----------------------------------------

    FILL_COLUMNS = ("Brand", "Title", "eBay title", "SEO title", "SEO description", "Description",
                    "Product type", "Tags", "eBay specifics", "Other category fields")

    def rows_to_fill(self, offset=0, limit=10):
        """Rows whose blue columns aren't filled yet, with what eBay needs for each."""
        rows = [r for r in sheet_io.read(self.path) if r.get("Status", "").upper() != "DONE"]
        needs = lambda r: not all(r.get(c) for c in ("Title", "eBay title", "SEO title", "SEO description", "Description"))
        batch = []
        for r in rows[offset:offset + limit]:
            if not needs(r):
                continue
            entry = {k: r.get(k, "") for k in sheet_io.YOURS + ("Condition", "eBay store category", "eBay category",
                     "Shopify category", "Toy figure theme", "Theme", "Color", "Color 2", "Material", "Brand")}
            entry["row"] = r["_row"]
            try:
                cat_id, _ = self.ebay_category_id(r)
                rules = self.aspects(cat_id)
                entry["ebay_category_id"] = cat_id
                wanted = {w.lower() for w in self.opts.get("ebay_specifics_wanted", [])}
                auto = self.sheet_specifics(r, rules)
                entry["ebay_specifics_from_sheet"] = auto
                entry["ebay_specifics_needed"] = [
                    {"name": a["name"],
                     "need": "REQUIRED" if a["required"] else ("Stephanie fills this" if a["name"].lower() in wanted
                                                              else "recommended"),
                     "only_these": a["mode"] == "SELECTION_ONLY", "values": a["values"][:20]}
                    for a in rules.values()
                    if (a["required"] or a["usage"] == "RECOMMENDED" or a["name"].lower() in wanted)
                    and a["name"] not in auto]
            except Exception as e:
                entry["ebay_problem"] = str(e)[:200]
            try:
                cat_gid = self.opts["shopify_categories"].get(r.get("Shopify category"))
                in_sheet = {k.lower() for k in self.opts["metafields"]} | {"color", "material", "recommended age group",
                                                                          "toy/game material"}
                fields = self.store_fields()
                entry["shopify_category_fields"] = [
                    {"name": n, "values_in_store": list(fields[n.lower()]["values"])[:40] if n.lower() in fields else []}
                    for n in self.category_fields(cat_gid) if n.lower() not in in_sheet]
            except Exception as e:
                entry["shopify_fields_problem"] = str(e)[:200]
            batch.append(entry)
        nxt = offset + limit
        return {"rows": batch, "still_to_fill_after_this": sum(1 for r in rows[nxt:] if needs(r)),
                "next_offset": nxt if nxt < len(rows) else None}

    def fill(self, fills):
        """fills: [{"row": 2, "Title": ..., "eBay title": ..., ...}]. Checks length limits."""
        updates, problems = {}, []
        for f in fills:
            r = int(f["row"])
            vals = {k: str(f[k]).strip() for k in self.FILL_COLUMNS if f.get(k) not in (None, "")}
            too_long = [f"{k} {len(vals[k])}/{n}" for k, n in LIMITS.items() if len(vals.get(k, "")) > n]
            if too_long:
                problems.append({"row": r, "too_long": too_long})
                continue
            updates[r] = vals
        if updates:
            sheet_io.write(self.path, updates)
        return {"written": sorted(updates), "problems": problems}

    # --- the run ---------------------------------------------------------------

    def run(self, confirm=False, limit=None, rows=None):
        all_rows = sheet_io.read(self.path)
        if confirm:   # only rows that passed the dry run (or need their eBay half retried)
            todo = [r for r in all_rows if r.get("Status", "").upper() in ("READY", "SHOPIFY ONLY")]
        else:
            todo = [r for r in all_rows if r.get("Status", "").upper() != "DONE"]
        if rows:
            want = {int(x) for x in rows}
            todo = [r for r in todo if r["_row"] in want]
        if limit:
            todo = todo[:int(limit)]
        results, updates = [], {}
        for row in todo:
            shopify_done = row.get("Status", "").upper() == "SHOPIFY ONLY" and row.get("Shopify link")
            plan, problems = self.preflight(row, need_shopify=not shopify_done)
            base = {"row": row["_row"], "title": (row.get("Title") or row.get("What it is"))[:60]}
            if problems:
                results.append({**base, "result": "NOT READY", "problems": problems})
                updates[row["_row"]] = {"Status": "NOT READY", "Problem": "; ".join(problems)[:500]}
                continue
            if not confirm:
                results.append({**base, "result": "READY", "notes": plan.get("warnings") or [],
                                "ebay_category_id": plan["ebay_category"],
                                "shelf": plan["shelf"], "photos": len(plan.get("photos") or [])})
                updates[row["_row"]] = {"Status": "READY", "Problem": ""}
                continue
            try:
                if shopify_done:
                    pid = "gid://shopify/Product/" + row["Shopify link"].rstrip("/").split("/")[-1]
                    shop_link = row["Shopify link"]
                else:
                    pid, shop_link = self.create_shopify(row, plan)
                    updates[row["_row"]] = {"Status": "SHOPIFY ONLY", "Shopify link": shop_link}
                    sheet_io.write(self.path, {row["_row"]: updates[row["_row"]]})
                    self.wait_for_photos(pid)
                ebay_link = self.create_ebay(row, plan, pid)
                updates[row["_row"]] = {"Status": "DONE", "Shopify link": shop_link, "eBay link": ebay_link,
                                        "Problem": ("Note: " + "; ".join(plan.get("warnings") or []))[:500]
                                        if plan.get("warnings") else ""}
                results.append({**base, "result": "DONE", "shopify": shop_link, "ebay": ebay_link})
            except sheet_io.SheetOpenError:
                raise
            except Exception as e:
                state = "SHOPIFY ONLY" if (shopify_done or row["_row"] in updates) else "FAILED"
                updates.setdefault(row["_row"], {})
                updates[row["_row"]].update({"Status": state, "Problem": str(e)[:500]})
                results.append({**base, "result": state, "error": str(e)[:300]})
            sheet_io.write(self.path, {row["_row"]: updates[row["_row"]]})
        if updates:
            sheet_io.write(self.path, updates)
        summary = {}
        for r in results:
            summary[r["result"]] = summary.get(r["result"], 0) + 1
        left = sum(1 for r in sheet_io.read(self.path) if r.get("Status", "").upper() in ("READY", "SHOPIFY ONLY"))
        return {"dry_run": not confirm, "summary": summary, "rows": results,
                "ready_rows_left": left if confirm else None}


def _multipart(fields, path):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    ctype = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n").encode()
    out += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"

"""One-time fix: Casey, Savannah and Bailee are girls, so listings that call them "he/him/his" get corrected.

The fix list (rrr_sync/data/dog_pronoun_fixes.json) was built by reading every listing on the store and
checking each pronoun by hand. A pronoun about a character (Garfield, Woody, Snoopy...) is NOT on the
list and is never touched. Each entry is an exact phrase that appears once in one product's description.

Safety:
- Dry run unless --apply.
- Before changing a product, the current description is fetched and the exact phrase must still be
  there exactly once; otherwise that product is skipped and reported.
- A backup of every original description is written to data/pronoun_fix_backup.json before any change.
- Only the description changes. Title, tags, price, inventory and everything else are left alone.
"""

import json
from pathlib import Path

from .config import ROOT

FIXES_FILE = Path(__file__).parent / "data" / "dog_pronoun_fixes.json"
BACKUP_FILE = ROOT / "data" / "pronoun_fix_backup.json"

GET = """
query Get($id: ID!) { product(id: $id) { id title descriptionHtml } }
"""

UPDATE = """
mutation Update($input: ProductInput!) {
  productUpdate(input: $input) { product { id } userErrors { field message } }
}
"""


def load_fixes(path=FIXES_FILE):
    fixes = json.loads(Path(path).read_text(encoding="utf-8"))
    by_product = {}
    for f in fixes:
        by_product.setdefault(str(f["product_id"]), []).append(f)
    return by_product


def plan_product(html, fixes):
    """Apply this product's fixes to its description. Returns (new_html, applied, skipped)."""
    applied, skipped = [], []
    for f in fixes:
        if html.count(f["old"]) == 1:
            html = html.replace(f["old"], f["new"])
            applied.append(f)
        elif f["new"] in html and f["old"] not in html:
            skipped.append((f, "already fixed"))
        else:
            skipped.append((f, "phrase not found exactly once (listing was edited?)"))
    return html, applied, skipped


def run(shopify, do_apply=False, log=print, fixes_path=FIXES_FILE, backup_path=BACKUP_FILE):
    by_product = load_fixes(fixes_path)
    mode = "APPLYING" if do_apply else "DRY RUN (add --apply to make changes)"
    log(f"Dog pronoun fix: {sum(map(len, by_product.values()))} phrases in {len(by_product)} listings. {mode}")
    backup, changed, errors = {}, 0, []
    for pid, fixes in by_product.items():
        gid = f"gid://shopify/Product/{pid}"
        product = shopify.graphql(GET, {"id": gid})["product"]
        if not product:
            errors.append(f"product {pid} ({fixes[0]['handle']}) no longer exists, skipped")
            continue
        new_html, applied, skipped = plan_product(product["descriptionHtml"], fixes)
        for f, why in skipped:
            log(f"  SKIP  {product['title']}: {why}: \"{f['old']}\"")
        if not applied:
            continue
        for f in applied:
            log(f"  FIX   {product['title']}: \"{f['old']}\"  ->  \"{f['new']}\"")
        if do_apply:
            backup[pid] = {"title": product["title"], "descriptionHtml": product["descriptionHtml"]}
            Path(backup_path).parent.mkdir(exist_ok=True)
            Path(backup_path).write_text(json.dumps(backup, indent=1, ensure_ascii=False), encoding="utf-8")
            data = shopify.graphql(UPDATE, {"input": {"id": gid, "descriptionHtml": new_html}})
            errs = data["productUpdate"]["userErrors"]
            if errs:
                errors.extend(f"{product['title']}: {e['message']}" for e in errs)
                continue
        changed += 1
    log(f"{'Changed' if do_apply else 'Would change'} {changed} listings.")
    for e in errors:
        log("  ERROR " + e)
    return changed, errors

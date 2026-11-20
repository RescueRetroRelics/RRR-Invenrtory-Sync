"""Build the RRR listing sheet (Excel, with dropdowns) from listing/options.json.

    python -m listing.make_sheet                 -> RRR_Listing_Sheet.xlsx
    python -m listing.make_sheet out.xlsx 500    -> 500 rows

Yellow columns: Stephanie types / picks. Blue columns: Claude fills. Gray: the tool fills.
Every dropdown is limited to values Shopify/eBay actually accept.
"""

import json
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

OPTIONS = Path(__file__).with_name("options.json")

YOU, CLAUDE, TOOL = "FFF2CC", "DDEBF7", "E7E6E6"

# (header, who, width, dropdown list name or None, note)
COLUMNS = [
    ("Photos folder", YOU, 13, None, "Folder name inside listing_photos, e.g. 001"),
    ("What it is", YOU, 40, None, "Your notes. Messy is fine: brand, character, size, anything special."),
    ("Shopify price", YOU, 11, None, "e.g. 15.99"),
    ("eBay price", YOU, 11, None, "e.g. 19.99"),
    ("Qty", YOU, 6, None, "How many you have"),
    ("Condition", YOU, 14, "conditions", None),
    ("eBay store category", YOU, 24, "ebay_store_categories", "Your eBay store shelf"),
    ("eBay category", YOU, 30, "ebay_categories", "Leave blank and Claude picks one eBay suggests"),
    ("Shopify category", YOU, 28, "shopify_categories", None),
    ("Age group", YOU, 12, "Age group", "Adults / Teens / All ages. NOT '10+'. That goes in Recommended age."),
    ("Recommended age", YOU, 16, "Recommended age", "This is where '10 years or older' goes"),
    ("Target gender", YOU, 12, "Target gender", None),
    ("Theme", YOU, 13, "Theme", None),
    ("Toy figure theme", YOU, 14, "Toy figure theme", "Only for figures/toys"),
    ("Color", YOU, 12, "Color", None),
    ("Color 2", YOU, 12, "Color", None),
    ("Material", YOU, 12, "Material", None),
    ("Material 2", YOU, 12, "Material", None),
    ("Toy material", YOU, 12, "Toy material", "Only for toys/games"),
    ("Brand", CLAUDE, 16, None, "Claude fills from your notes (you can too)"),
    ("Title", CLAUDE, 45, None, "Shopify title"),
    ("eBay title", CLAUDE, 45, None, "80 characters max, eBay-optimized"),
    ("SEO title", CLAUDE, 40, None, "Meta title, 70 characters max"),
    ("SEO description", CLAUDE, 50, None, "Meta description, 160 characters max"),
    ("Description", CLAUDE, 50, None, None),
    ("Product type", CLAUDE, 25, None, None),
    ("Tags", CLAUDE, 40, None, "Your standard RRR tags are added automatically"),
    ("eBay specifics", CLAUDE, 40, None, "Brand: Funko | Character: Chucky | ..."),
    ("Status", TOOL, 14, None, "READY / DONE / FAILED (the tool writes this)"),
    ("Shopify link", TOOL, 30, None, None),
    ("eBay link", TOOL, 30, None, None),
    ("Problem", TOOL, 40, None, None),
]


def lists_from_options(opts):
    lists = {
        "conditions": opts["conditions"],
        "shopify_categories": list(opts["shopify_categories"]),
        "ebay_store_categories": opts.get("ebay_store_categories") or ["(send Claude your store categories)"],
        "ebay_categories": list(opts.get("ebay_categories") or {}) or ["(Claude picks)"],
    }
    for name, m in opts["metafields"].items():
        lists[name] = list(m["values"])
    return lists


def build(path, rows=500):
    opts = json.loads(OPTIONS.read_text(encoding="utf-8"))
    lists = lists_from_options(opts)
    wb = Workbook()
    ws = wb.active
    ws.title = "Listings"

    hidden = wb.create_sheet("Lists")
    ranges = {}
    for col, (name, values) in enumerate(lists.items(), start=1):
        letter = get_column_letter(col)
        hidden.cell(row=1, column=col, value=name)
        for r, v in enumerate(values, start=2):
            hidden.cell(row=r, column=col, value=v)
        ranges[name] = f"Lists!${letter}$2:${letter}${len(values) + 1}"
    hidden.sheet_state = "hidden"

    for c, (header, who, width, dd, note) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=c, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=who)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        if note:
            cell.comment = Comment(note, "RRR")
        letter = get_column_letter(c)
        ws.column_dimensions[letter].width = width
        if dd:
            dv = DataValidation(type="list", formula1=f"={ranges[dd]}", allow_blank=True,
                                showErrorMessage=True, errorTitle="Pick from the list",
                                error="Only the dropdown values work on Shopify/eBay.")
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{rows + 1}")
        if who == YOU:
            for r in range(2, rows + 2):
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor="FFFBEA")
    ws.freeze_panes = "C2"
    ws.row_dimensions[1].height = 32

    help_ws = wb.create_sheet("How to", 0)
    lines = [
        "RRR LISTING SHEET",
        "",
        "1. One row per item. Fill the YELLOW columns. Dropdowns only offer values Shopify and eBay accept.",
        "2. Put each item's photos in its own folder inside 'listing_photos' (001, 002, ...). Type that folder name in 'Photos folder'.",
        "3. Save. Tell Claude Desktop: 'Fill in my listing sheet.' Claude fills the BLUE columns (titles, SEO, description, tags, eBay specifics).",
        "4. Skim the blue columns. Fix anything dumb.",
        "5. Tell Claude: 'Dry run my listing sheet', then 'List them'. It creates each item on Shopify AND eBay, one at a time,",
        "   and writes DONE or FAILED (with the reason) in the gray columns. Running it again skips the DONE rows.",
        "",
        "No Shopify CSV import is used. Ever.",
    ]
    for i, t in enumerate(lines, start=1):
        help_ws.cell(row=i, column=1, value=t).font = Font(bold=(i == 1), size=14 if i == 1 else 12)
    help_ws.column_dimensions["A"].width = 130
    wb.active = 1
    wb.save(path)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "RRR_Listing_Sheet.xlsx"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    print(build(out, n))

"""Read and write the RRR listing sheet (.xlsx). Rows are dicts keyed by header text."""

from pathlib import Path

from openpyxl import load_workbook

SHEET = "Listings"
YOURS = ("Photos folder", "What it is", "Shopify price", "eBay price", "Qty")


class SheetOpenError(RuntimeError):
    pass


def read(path):
    wb = load_workbook(path)
    ws = wb[SHEET]
    headers = [c.value for c in ws[1]]
    rows = []
    for r in range(2, ws.max_row + 1):
        values = {h: ws.cell(row=r, column=i + 1).value for i, h in enumerate(headers) if h}
        if not any(values.get(k) not in (None, "") for k in YOURS):
            continue
        clean = {k: ("" if v is None else str(v).strip()) for k, v in values.items()}
        clean["_row"] = r
        rows.append(clean)
    return rows


def write(path, updates):
    """updates: {row_number: {header: value}}. Fails clearly if Excel has the file open."""
    wb = load_workbook(path)
    ws = wb[SHEET]
    col = {c.value: c.column for c in ws[1] if c.value}
    for r, fields in updates.items():
        for h, v in fields.items():
            if h in col:
                ws.cell(row=int(r), column=col[h], value=v)
    try:
        wb.save(path)
    except PermissionError:
        raise SheetOpenError(f"Close {Path(path).name} in Excel first, then try again.") from None

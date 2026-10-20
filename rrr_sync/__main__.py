"""Command line: python -m rrr_sync <command>"""

import argparse
import sys

from . import ebay, ebay_auth, matching, notify, orders, sync
from .config import Config
from .shopify import Shopify


def cmd_ebay_auth_url(cfg, args):
    print("Open this link, sign in to eBay and click 'Agree':\n")
    print(ebay_auth.auth_url(cfg))
    print(
        "\neBay then sends you to a page whose address contains `code=`. Copy that whole"
        "\naddress and run (within 5 minutes - the code expires fast):\n"
        "\n  python -m rrr_sync ebay-auth-exchange '<paste the address here>'"
    )


def cmd_ebay_auth_exchange(cfg, args):
    ebay_auth.exchange_code(cfg, args.redirect)
    print(f"eBay connected. Refresh token saved to {ebay_auth.TOKEN_FILE} "
          f"(good until about {ebay_auth.refresh_token_expiry()}).")


def cmd_check(cfg, args):
    ok = True
    try:
        listings = ebay.active_listings(cfg)
        print(f"eBay:    OK - {len(listings)} active listings/variations")
    except Exception as e:
        ok = False
        print(f"eBay:    FAILED - {e}")
    try:
        shop = Shopify(cfg)
        info = shop.shop_name()
        print(f"Shopify: OK - {info['name']} ({info['myshopifyDomain']})")
    except Exception as e:
        ok = False
        print(f"Shopify: FAILED - {e}")
    return 0 if ok else 1


def cmd_match(cfg, args):
    listings = ebay.active_listings(cfg)
    variants = Shopify(cfg).variants()
    rows = matching.find_matches(listings, variants, matching.load_matches())
    matching.save_matches(rows)
    count = lambda pred: sum(1 for r in rows if pred(r))
    print(f"eBay listings:      {len(rows)}")
    print(f"  approved (Y):     {count(lambda r: r['approved'] == 'Y')}")
    print(f"  need your review: {count(lambda r: r['approved'] == '' and r['method'] == 'fuzzy')}")
    print(f"  rejected (N):     {count(lambda r: r['approved'] == 'N')}")
    print(f"  no match found:   {count(lambda r: r['method'] == 'unmatched')}")
    print(f"\nReview {matching.MATCHES_FILE}: put Y (sync) or N (don't) in the 'approved' column.")


def cmd_sync(cfg, args):
    report = sync.run(cfg, Shopify(cfg), apply=args.apply)
    for line in report.events + report.changes:
        print(line)
    if args.verbose:
        for what, why in report.skipped:
            print(f"Skipped: {what}  ({why})")
    if not report.events and not report.changes:
        print("Nothing to do: eBay and Shopify already agree.")
    if args.apply:
        push_error = notify.send(cfg, report.events)
        if push_error:
            print(f"(Phone notification failed: {push_error})")
    elif report.events or report.changes:
        print("\nDry run only. Re-run with --apply to make these changes.")
    return 1 if report.errors else 0


def cmd_notify_test(cfg, args):
    if not cfg.ntfy_topic:
        print("Set NTFY_TOPIC in .env first.")
        return 1
    err = notify.send(cfg, ["Test from the RRR inventory sync. If you see this, notifications work! 🐶"])
    print(f"Failed: {err}" if err else "Sent. Check your phone.")
    return 1 if err else 0


def cmd_orders(cfg, args):
    since_iso = f"{args.since}T00:00:00.000Z"
    found = ebay.orders_since(cfg, since_iso)
    csv_path, json_path = orders.export(found, args.since)
    print(f"{len(found)} eBay orders since {args.since}")
    print(f"  {csv_path}\n  {json_path}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="rrr_sync", description="RRR eBay <-> Shopify inventory sync")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("ebay-auth-url", help="print the eBay consent link")
    ex = sub.add_parser("ebay-auth-exchange", help="trade the redirect URL/code for tokens")
    ex.add_argument("redirect", help="the full address eBay sent you to, or just the code")
    sub.add_parser("check", help="test the eBay and Shopify connections")
    sub.add_parser("match", help="match eBay listings to Shopify products -> data/matches.csv")
    sy = sub.add_parser("sync", help="count eBay sales on Shopify, keep eBay at 1 (dry run by default)")
    sy.add_argument("--apply", action="store_true", help="actually change quantities")
    sy.add_argument("--verbose", action="store_true", help="also list skipped pairs")
    sub.add_parser("notify-test", help="send a test phone notification")
    od = sub.add_parser("orders", help="export eBay orders to data/")
    od.add_argument("--since", default="2026-01-01", help="YYYY-MM-DD (default 2026-01-01)")

    args = p.parse_args(argv)
    cfg = Config.load()
    handler = globals()["cmd_" + args.command.replace("-", "_")]
    return handler(cfg, args) or 0


if __name__ == "__main__":
    sys.exit(main())

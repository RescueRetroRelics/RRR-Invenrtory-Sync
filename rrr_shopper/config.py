"""Settings for the simulated shopper. Edit this file to change who shops and what they look for."""

BASE_URL = "https://rescueretrorelics.com"

# Each persona is a pretend customer. "searches" are typed into site search, in order.
# The first search that returns products is the one the persona clicks into.
PERSONAS = [
    {
        "key": "collector",
        "name": "The Collector",
        "goal": "Hunting a specific Sonny Angel / Re-Ment find",
        "devices": ["desktop", "mobile"],
        "searches": ["sonny angel", "re-ment snoopy", "snoopy hippers"],
        "browse": "/collections/all",
    },
    {
        "key": "gift",
        "name": "The Gift Buyer",
        "goal": "Shopping for a Snoopy fan, wants something under $25",
        "devices": ["desktop", "mobile"],
        "searches": ["snoopy", "snoopy gift", "peanuts keychain"],
        "browse": "/collections/all?sort_by=price-ascending",
    },
    {
        "key": "mobile",
        "name": "The Mobile-Only Shopper",
        "goal": "Browsing on a phone between other things",
        "devices": ["mobile"],
        "searches": ["hello kitty", "funko pop"],
        "browse": "/collections/all",
    },
    {
        "key": "skeptic",
        "name": "The Skeptic",
        "goal": "Checking authenticity, shipping and returns before buying",
        "devices": ["desktop", "mobile"],
        "searches": ["betty boop", "care bears"],
        "browse": "/collections/all",
        "trust_pages": True,
    },
]

# Pages a careful buyer expects to find. Standard Shopify addresses.
TRUST_PAGES = {
    "Shipping policy": "/policies/shipping-policy",
    "Refund / returns policy": "/policies/refund-policy",
    "Contact page": "/pages/contact",
    "Terms of service": "/policies/terms-of-service",
    "Privacy policy": "/policies/privacy-policy",
}

# Products known to be sold out, to see how the site handles them.
# The shopper also finds more on its own from the store's public product list.
OUT_OF_STOCK_HANDLES = [
    "celavi-betty-boop-pucker-pout-moisturizing-tinted-lip-oil-set-3-piece",
]

SLOW_PAGE_MS = 3000        # a page taking longer than this to load gets flagged
MAX_LINKS_TO_CHECK = 80    # how many of the site's own links to test for 404s
MIN_TAP_TARGET_PX = 24     # WCAG 2.2 minimum size for tappable things on mobile

# SAFETY: the shopper refuses to click anything whose text matches this.
# It never fills in checkout fields and stops the moment checkout loads.
FORBIDDEN_CLICK_WORDS = [
    "pay now", "complete order", "place order", "submit order", "buy now", "buy it now",
    "pay with", "shop pay", "paypal", "apple pay", "google pay", "venmo", "confirm",
]

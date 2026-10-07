"""Settings loaded from environment variables and an optional .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SECRETS_DIR = ROOT / ".secrets"

DEFAULT_EBAY_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
])


def load_dotenv(path=ROOT / ".env"):
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    ebay_client_id: str
    ebay_client_secret: str
    ebay_dev_id: str
    ebay_runame: str
    ebay_scopes: str
    ebay_refresh_token_env: str
    shopify_domain: str
    shopify_client_id: str
    shopify_client_secret: str
    shopify_admin_token: str
    shopify_location_id: str
    shopify_api_version: str

    @classmethod
    def load(cls):
        load_dotenv()
        env = os.environ.get
        return cls(
            ebay_client_id=env("EBAY_CLIENT_ID", ""),
            ebay_client_secret=env("EBAY_CLIENT_SECRET", ""),
            ebay_dev_id=env("EBAY_DEV_ID", ""),
            ebay_runame=env("EBAY_RUNAME", ""),
            ebay_scopes=env("EBAY_SCOPES", DEFAULT_EBAY_SCOPES),
            ebay_refresh_token_env=env("EBAY_REFRESH_TOKEN", ""),
            shopify_domain=env("SHOPIFY_STORE_DOMAIN", ""),
            shopify_client_id=env("SHOPIFY_CLIENT_ID", ""),
            shopify_client_secret=env("SHOPIFY_CLIENT_SECRET", ""),
            shopify_admin_token=env("SHOPIFY_ADMIN_TOKEN", ""),
            shopify_location_id=env("SHOPIFY_LOCATION_ID", ""),
            shopify_api_version=env("SHOPIFY_API_VERSION", "2026-07"),
        )

    def require(self, *names):
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise ConfigError(
                "Missing settings in .env: " + ", ".join(n.upper() for n in missing)
            )

"""eBay OAuth (authorization code grant) with a stored, auto-renewing refresh token.

Flow:
  1. auth_url() builds the consent link. The seller opens it and signs in.
  2. eBay redirects to the RuName's "accept URL" with ?code=... in the address bar.
  3. exchange_code() takes that URL (or just the code) and stores the tokens.
  4. get_access_token() returns a valid access token, refreshing it when needed.

Access tokens last ~2 hours; refresh tokens last ~18 months.
"""

import base64
import json
import os
import secrets
import time
import urllib.parse

from . import http
from .config import SECRETS_DIR

AUTH_ENDPOINT = "https://auth.ebay.com/oauth2/authorize"
TOKEN_ENDPOINT = "https://api.ebay.com/identity/v1/oauth2/token"
TOKEN_FILE = SECRETS_DIR / "ebay_tokens.json"


def auth_url(cfg, state=None):
    cfg.require("ebay_client_id", "ebay_runame")
    params = {
        "client_id": cfg.ebay_client_id,
        "response_type": "code",
        "redirect_uri": cfg.ebay_runame,
        "scope": cfg.ebay_scopes,
        "state": state or secrets.token_urlsafe(12),
        "prompt": "login",
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def extract_code(pasted):
    """Accept the full redirect URL or a bare code (URL-encoded or not)."""
    pasted = pasted.strip()
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        values = urllib.parse.parse_qs(query).get("code")
        if values:
            return values[0]
    return urllib.parse.unquote(pasted)


def _basic_auth(cfg):
    raw = f"{cfg.ebay_client_id}:{cfg.ebay_client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _post_token(cfg, form):
    _, text = http.request(
        "POST",
        TOKEN_ENDPOINT,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth(cfg),
        },
        body=urllib.parse.urlencode(form),
        retries=1,
    )
    return json.loads(text)


def _save(tokens):
    SECRETS_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    os.chmod(TOKEN_FILE, 0o600)


def _load():
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return {}


def exchange_code(cfg, pasted):
    cfg.require("ebay_client_id", "ebay_client_secret", "ebay_runame")
    resp = _post_token(cfg, {
        "grant_type": "authorization_code",
        "code": extract_code(pasted),
        "redirect_uri": cfg.ebay_runame,
    })
    now = time.time()
    tokens = {
        "access_token": resp["access_token"],
        "access_expires_at": now + resp.get("expires_in", 7200),
        "refresh_token": resp["refresh_token"],
        "refresh_expires_at": now + resp.get("refresh_token_expires_in", 0),
    }
    _save(tokens)
    return tokens


def get_access_token(cfg):
    tokens = _load()
    if tokens.get("access_token") and tokens.get("access_expires_at", 0) > time.time() + 120:
        return tokens["access_token"]
    refresh = tokens.get("refresh_token") or cfg.ebay_refresh_token_env
    if not refresh:
        raise RuntimeError(
            "No eBay refresh token yet. Run `python -m rrr_sync ebay-auth-url`, "
            "approve access, then `python -m rrr_sync ebay-auth-exchange '<redirect URL>'`."
        )
    resp = _post_token(cfg, {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "scope": cfg.ebay_scopes,
    })
    tokens.update(
        access_token=resp["access_token"],
        access_expires_at=time.time() + resp.get("expires_in", 7200),
        refresh_token=refresh,
    )
    _save(tokens)
    return tokens["access_token"]


def refresh_token_expiry():
    expires = _load().get("refresh_expires_at")
    return time.strftime("%Y-%m-%d", time.localtime(expires)) if expires else None

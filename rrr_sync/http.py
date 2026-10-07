"""Tiny HTTP helper on top of urllib so the tool has no third-party dependencies."""

import json
import time
import urllib.error
import urllib.request


class HttpError(RuntimeError):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body


def request(method, url, headers=None, body=None, retries=3, timeout=60):
    """Send a request and return (status, text). Retries 429 and 5xx with backoff."""
    data = body.encode() if isinstance(body, str) else body
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise HttpError(e.code, text, url) from None
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise


def request_json(method, url, headers=None, payload=None, **kw):
    headers = {"Accept": "application/json", **(headers or {})}
    body = None
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")
        body = json.dumps(payload)
    _, text = request(method, url, headers=headers, body=body, **kw)
    return json.loads(text) if text else {}

"""Tell Stephanie what the sync did: an activity log, plus an optional phone push via ntfy."""

import time
import urllib.parse

from . import http
from .config import DATA_DIR

LOG_FILE = DATA_DIR / "activity.log"


def send(cfg, events):
    """events: list of short human-readable lines. Logged always; pushed if NTFY_TOPIC is set."""
    if not events:
        return None
    DATA_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(f"{stamp}  {e}\n")
    if not cfg.ntfy_topic:
        return None
    urgent = any(e.startswith(("OVERSOLD", "ERROR", "NOT MATCHED", "ENDED")) for e in events)
    try:
        http.request(
            "POST",
            "https://ntfy.sh/" + urllib.parse.quote(cfg.ntfy_topic, safe=""),
            headers={
                "Title": "RRR inventory sync",
                "Priority": "high" if urgent else "default",
                "Tags": "warning" if urgent else "package",
            },
            body="\n".join(events)[:3500],
            retries=1,
        )
    except Exception as e:  # a failed push must never break the sync itself
        return str(e)
    return None

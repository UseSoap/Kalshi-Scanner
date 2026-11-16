"""Phone notifications through ntfy.sh (free, no account). No topic set = notifications off."""

from __future__ import annotations

import requests


def send(topic: str, title: str, message: str, priority: str = "default", tags: str = "") -> bool:
    if not topic:
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode("ascii"), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        r = requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, timeout=15)
        return r.status_code == 200
    except requests.RequestException:
        return False

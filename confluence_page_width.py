"""
Set Confluence Cloud page display width (editor dropdown: Narrow / Wide / Max)
via content properties content-appearance-published and content-appearance-draft.

Default matches UI "Wide". Override with env CONFLUENCE_PAGE_WIDTH (e.g. wide, max,
full-width, fixed-width) if your tenant uses different values.
Set CONFLUENCE_PAGE_WIDTH= to empty or "0" to skip.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional, Tuple

import requests


def _desired_width() -> Optional[str]:
    raw = os.getenv("CONFLUENCE_PAGE_WIDTH", "wide")
    if raw is None:
        return "wide"
    s = str(raw).strip()
    if not s or s == "0":
        return None
    return s


def _upsert_string_property(
    base_url: str,
    auth: Tuple[str, str],
    page_id: str,
    key: str,
    value: str,
    timeout: int = 25,
) -> bool:
    prop_url = f"{base_url}/rest/api/content/{page_id}/property/{key}"
    r = requests.get(prop_url, auth=auth, timeout=timeout)
    version = 1
    if r.status_code == 200:
        payload = r.json()
        version = int(payload.get("version", {}).get("number", 0)) + 1
    elif r.status_code != 404:
        print(
            f"Warning: could not read property {key} for page {page_id}: {r.status_code}",
            file=sys.stderr,
        )
        return False

    body: dict[str, Any] = {"key": key, "version": {"number": version}, "value": value}
    put = requests.put(
        prop_url,
        auth=auth,
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if put.status_code not in (200, 201):
        print(
            f"Warning: could not set property {key} for page {page_id}: {put.status_code} {put.text}",
            file=sys.stderr,
        )
        return False
    return True


def apply_page_display_width(
    base_url: str,
    auth: Tuple[str, str],
    page_id: Optional[str],
    width: Optional[str] = None,
) -> None:
    if not page_id:
        return
    w = width if width is not None else _desired_width()
    if not w:
        return
    for key in ("content-appearance-published", "content-appearance-draft"):
        _upsert_string_property(base_url, auth, page_id, key, w)

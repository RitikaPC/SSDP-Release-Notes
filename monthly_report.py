#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()

import argparse
import calendar
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

import requests

CONFLUENCE_BASE_URL = "https://eng-stla.atlassian.net/wiki"
USERNAME = os.getenv("CONFLUENCE_USERNAME")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY")
PARENT_PAGE_ID = os.getenv("CONFLUENCE_PARENT_PAGE_ID")
# Optional override so monthly reports can be created under a different Confluence container
# without changing the weekly release location.
MONTHLY_PARENT_PAGE_ID = os.getenv("MONTHLY_PARENT_PAGE_ID", PARENT_PAGE_ID)
WEEKLY_STOPPER = os.getenv("WEEKLY_STOPPER", "weekly_stopper.json")

SUMMARY_SECTION_VARIANTS = {
    "➕ What we added:": ["What we added:", "➕ What we added:", ":plus: What we added:"],
    "🔧 What we changed:": ["What we changed:", "🔧 What we changed:", ":git-extension: What we changed:"],
    "🚮 What we Deprecated/ Removed:": [
        "What we Deprecated/ Removed:",
        "🚮 What we Deprecated/ Removed:",
        ":put_litter_in_its_place: What we Deprecated/ Removed:",
    ],
    "🔨 What we fixed:": [
        "What we fixed:",
        "🔨 What we fixed:",
        "🛠 What we fixed:",
        "🛠️ What we fixed:",
        ":hammer: What we fixed:",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate monthly SSDP report from weekly Confluence pages"
    )
    parser.add_argument(
        "--month",
        required=True,
        help="Target month in YYYY-MM format (example: 2026-01)",
    )
    parser.add_argument(
        "--output",
        default="monthly_report.html",
        help="Output HTML path (default: monthly_report.html)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish monthly report to Confluence under CONFLUENCE_PARENT_PAGE_ID",
    )
    parser.add_argument(
        "--skip-if-unchanged",
        action="store_true",
        help="When publishing, skip Confluence update if generated content is unchanged",
    )
    return parser.parse_args()


def validate_env(require_parent: bool = False) -> None:
    required = {
        "CONFLUENCE_USERNAME": USERNAME,
        "CONFLUENCE_API_TOKEN": API_TOKEN,
        "CONFLUENCE_SPACE_KEY": SPACE_KEY,
    }
    if require_parent:
        # Prefer the monthly override if provided.
        required["MONTHLY_PARENT_PAGE_ID"] = MONTHLY_PARENT_PAGE_ID

    missing = [
        name
        for name, value in required.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Confluence environment variables: {', '.join(missing)}")


def parse_month(month_text: str) -> Tuple[int, int]:
    m = re.match(r"^(\d{4})-(\d{2})$", (month_text or "").strip())
    if not m:
        raise ValueError("Invalid --month format. Use YYYY-MM.")
    year = int(m.group(1))
    month = int(m.group(2))
    if month < 1 or month > 12:
        raise ValueError("Month must be between 01 and 12.")
    return year, month


def iso_weeks_in_month(year: int, month: int) -> List[Tuple[int, int]]:
    """
    Monthly ownership rule (requested):
    Baseline: assign a week to the month that contains its ISO week start (Monday).
    Reassignment: if that week contains >=2 days in the *next* calendar month, move
    the week to the next month report instead.

    This ensures boundary weeks like “week starts in Feb but has only 1 day in Mar”
    stay in Feb (so March won’t incorrectly include them).
    """
    def month_bounds(y: int, m: int) -> Tuple[datetime.date, datetime.date]:
        _, last = calendar.monthrange(y, m)
        return datetime.date(y, m, 1), datetime.date(y, m, last)

    def count_days_in_month(week_monday: datetime.date, target_y: int, target_m: int) -> int:
        """Count how many days in [week_monday, week_monday+6] land in (target_y, target_m)."""
        week_end = week_monday + datetime.timedelta(days=6)
        cnt = 0
        cur = week_monday
        while cur <= week_end:
            if cur.year == target_y and cur.month == target_m:
                cnt += 1
            cur += datetime.timedelta(days=1)
        return cnt

    # Previous and next calendar months relative to (year, month)
    prev_year = year - 1 if month == 1 else year
    prev_month = 12 if month == 1 else month - 1

    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1

    result: set[Tuple[int, int]] = set()

    def add_week_if_reassigned(week_monday: datetime.date, baseline_month_is_current: bool) -> None:
        iso_year, iso_week = week_monday.isocalendar()[0], week_monday.isocalendar()[1]

        if baseline_month_is_current:
            # Week is baseline-owned by current month; reassign to next if it has >=2 days in next month.
            days_in_next = count_days_in_month(week_monday, next_year, next_month)
            if days_in_next >= 2:
                return
            result.add((iso_year, iso_week))
        else:
            # Week is baseline-owned by previous month; if it has >=2 days in current month, reassign here.
            days_in_current = count_days_in_month(week_monday, year, month)
            if days_in_current >= 2:
                result.add((iso_year, iso_week))

    # Enumerate ISO weeks by their Monday dates that fall in either the current month or previous month.
    cur_start, cur_end = month_bounds(year, month)
    prev_start, prev_end = month_bounds(prev_year, prev_month)

    day = prev_start
    while day <= prev_end:
        if day.weekday() == 0:  # Monday
            add_week_if_reassigned(day, baseline_month_is_current=False)
        day += datetime.timedelta(days=1)

    day = cur_start
    while day <= cur_end:
        if day.weekday() == 0:  # Monday
            add_week_if_reassigned(day, baseline_month_is_current=True)
        day += datetime.timedelta(days=1)

    return sorted(result)


def confluence_search_week_page(prefix_title: str) -> Optional[Dict]:
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content/search"
    cql = f'space="{SPACE_KEY}" AND type=page AND title ~ "{prefix_title}"'
    params = {
        "cql": cql,
        "limit": 20,
        "expand": "version",
    }
    r = requests.get(url, auth=(USERNAME, API_TOKEN), params=params, timeout=20)
    if r.status_code != 200:
        print(f"Warning: search failed for {prefix_title}: {r.status_code}", file=sys.stderr)
        return None

    results = r.json().get("results", [])
    if not results:
        return None

    candidates = [
        item
        for item in results
        if (item.get("title") or "").startswith(prefix_title)
    ]
    if not candidates:
        return results[0]

    if len(candidates) == 1:
        return candidates[0]

    # Same ISO week sometimes exists twice (e.g. old title vs updated template). Prefer
    # the page whose storage matches our Release Summary + subsection shape so monthly
    # aggregation is not empty.
    best: Optional[Dict] = None
    best_key: Tuple[int, int] = (-1, -1)
    for item in candidates:
        page_id = item.get("id")
        if not page_id:
            continue
        html, _ = confluence_get_page_storage(str(page_id))
        sections = extract_release_summary_sections(html)
        score = sum(
            1 for v in sections.values() if _normalize_text_check(v or "")
        )
        ver = int((item.get("version") or {}).get("number") or 0)
        key: Tuple[int, int] = (score, ver)
        if key > best_key:
            best_key = key
            best = item

    if best:
        if len(candidates) > 1:
            print(
                f"Note: {len(candidates)} pages match {prefix_title!r}; "
                f"using title {best.get('title')!r} (summary subsections score {best_key[0]}).",
                file=sys.stderr,
            )
        return best

    return candidates[0]


def confluence_get_page_storage(page_id: str) -> Tuple[str, str]:
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    r = requests.get(url, auth=(USERNAME, API_TOKEN), params=params, timeout=20)
    if r.status_code != 200:
        return "", ""

    payload = r.json()
    html = payload.get("body", {}).get("storage", {}).get("value", "")
    links = payload.get("_links", {})
    web_url = ""
    if links.get("base") and links.get("webui"):
        web_url = links["base"] + links["webui"]
    return html, web_url


def confluence_find_page_id_by_title(title: str) -> Optional[str]:
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    params = {"spaceKey": SPACE_KEY, "title": title, "limit": 1}
    r = requests.get(url, auth=(USERNAME, API_TOKEN), params=params, timeout=20)
    if r.status_code != 200:
        return None
    results = r.json().get("results", [])
    return results[0].get("id") if results else None


# Same behaviour as publish.py: IN PROGRESS on first create; preserve manual changes on update.
DEFAULT_STATUS_BLOCK = (
    "<p><strong>Status:</strong> "
    "<ac:structured-macro ac:name=\"status\">"
    "<ac:parameter ac:name=\"title\">IN PROGRESS</ac:parameter>"
    "<ac:parameter ac:name=\"colour\">Blue</ac:parameter>"
    "</ac:structured-macro>"
    "</p>"
)

def extract_existing_exec_summary_block(existing_html: str) -> str:
    """
    Preserve whatever humans wrote in the "Executive Release Summary" section.

    Capture from the Executive heading through (but not including) the next
    "Release Summary" heading.
    """
    if not existing_html:
        return ""
    pattern = re.compile(
        r"(?P<block>"
        r"<h[1-6][^>]*>\s*Executive\s+Release\s+Summary.*?</h[1-6]>\s*"
        r".*?)"
        r"(?=<h[1-6][^>]*>\s*Release\s+Summary\s*</h[1-6]>)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(existing_html)
    return (m.group("block") or "").strip() if m else ""

def _sanitize_exec_summary_block(block_html: str) -> str:
    """
    Normalize the Executive Release Summary heading text.

    We preserve the user's manually edited content, but we don't want legacy
    heading suffixes like "(planned)" to persist forever.
    """
    if not block_html:
        return block_html
    # Remove an optional " (planned)" suffix inside the Executive heading tag.
    return re.sub(
        r"(<h[1-6][^>]*>\s*Executive\s+Release\s+Summary)\s*\(planned\)(\s*.*?</h[1-6]>)",
        r"\1\2",
        block_html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def upsert_exec_summary_block(html: str, preserved_block: str) -> str:
    if not html or not preserved_block:
        return html
    preserved_block = _sanitize_exec_summary_block(preserved_block)
    target = re.compile(
        r"<h[1-6][^>]*>\s*Executive\s+Release\s+Summary.*?</h[1-6]>\s*.*?"
        r"(?=<h[1-6][^>]*>\s*Release\s+Summary\s*</h[1-6]>)",
        re.IGNORECASE | re.DOTALL,
    )
    if target.search(html):
        return target.sub(preserved_block, html, count=1)
    return html



def extract_existing_status_block(existing_html: str) -> str:
    if not existing_html:
        return ""

    rs_header = re.search(
        r"<h[1-6][^>]*id\s*=\s*['\"]release-summary['\"][^>]*>",
        existing_html,
        flags=re.IGNORECASE,
    )
    # Monthly pages may not set id="release-summary"; scan top of body.
    prefix = existing_html[: rs_header.start()] if rs_header else existing_html[:2000]

    status_pattern = re.compile(
        r"<p[^>]*>\s*(?:<strong>\s*)?Status:\s*(?:</strong>\s*)?.*?</p>",
        re.IGNORECASE | re.DOTALL,
    )
    m = status_pattern.search(prefix)
    return m.group(0).strip() if m else ""


def upsert_top_status_block(html: str, status_block: str) -> str:
    if not html:
        return html

    status_pattern = re.compile(
        r"(<div[^>]*>\s*)(<p[^>]*>\s*(?:<strong>\s*)?Status:\s*(?:</strong>\s*)?.*?</p>)",
        re.IGNORECASE | re.DOTALL,
    )
    if status_pattern.search(html):
        return status_pattern.sub(rf"\1{status_block}", html, count=1)

    return re.sub(r"(<div[^>]*>)", rf"\1\n{status_block}", html, count=1, flags=re.IGNORECASE)


def confluence_update_page(
    page_id: str, title: str, html: str, *, skip_if_unchanged: bool = False
) -> Optional[str]:
    get_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    info_resp = requests.get(
        get_url,
        auth=(USERNAME, API_TOKEN),
        params={"expand": "body.storage,version"},
        timeout=20,
    )
    if info_resp.status_code != 200:
        print(f"Failed to fetch page info for update: {info_resp.text}", file=sys.stderr)
        return None

    info = info_resp.json()
    existing_html = info.get("body", {}).get("storage", {}).get("value", "")
    preserved_exec = extract_existing_exec_summary_block(existing_html)
    existing_status = extract_existing_status_block(existing_html)
    if existing_status:
        # Same as weekly: never overwrite a manually changed status.
        html = upsert_top_status_block(html, existing_status)
    else:
        # Monthly pages created before this feature had no status; add default once.
        html = upsert_top_status_block(html, DEFAULT_STATUS_BLOCK)

    if preserved_exec:
        html = upsert_exec_summary_block(html, preserved_exec)

    if skip_if_unchanged:
        existing_norm = _normalize_text_check(existing_html)
        new_norm = _normalize_text_check(html)
        if existing_norm == new_norm:
            links = info.get("_links", {}) or {}
            current_url = (links.get("base") or "") + (links.get("webui") or "")
            print("No monthly content changes detected; skipping Confluence update.")
            return current_url or None

    new_ver = info.get("version", {}).get("number", 1) + 1
    data = {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "version": {"number": new_ver},
        "body": {"storage": {"value": html, "representation": "storage"}},
    }

    put_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    r = requests.put(
        put_url,
        auth=(USERNAME, API_TOKEN),
        headers={"Content-Type": "application/json"},
        json=data,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        print(f"Monthly report update failed: {r.status_code} {r.text}", file=sys.stderr)
        return None

    res = r.json()
    links = res.get("_links", {})
    return (links.get("base") or "") + (links.get("webui") or "")


def confluence_create_page(title: str, html: str) -> Tuple[Optional[str], Optional[str]]:
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    html = upsert_top_status_block(html, DEFAULT_STATUS_BLOCK)
    data = {
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "ancestors": [{"id": MONTHLY_PARENT_PAGE_ID}],
        "body": {"storage": {"value": html, "representation": "storage"}},
    }
    r = requests.post(
        url,
        auth=(USERNAME, API_TOKEN),
        headers={"Content-Type": "application/json"},
        json=data,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        print(f"Monthly report create failed: {r.status_code} {r.text}", file=sys.stderr)
        return None, None

    res = r.json()
    links = res.get("_links", {})
    page_url = (links.get("base") or "") + (links.get("webui") or "")
    return page_url, res.get("id")


def publish_monthly_page(
    title: str, html: str, *, skip_if_unchanged: bool = False
) -> Optional[str]:
    page_id = confluence_find_page_id_by_title(title)
    if page_id:
        url = confluence_update_page(
            page_id, title, html, skip_if_unchanged=skip_if_unchanged
        )
    else:
        url, new_id = confluence_create_page(title, html)
        page_id = new_id or page_id
    if url and page_id:
        try:
            from confluence_page_width import apply_page_display_width

            apply_page_display_width(
                CONFLUENCE_BASE_URL, (USERNAME, API_TOKEN), page_id
            )
        except Exception as exc:
            print(f"Warning: could not set page width: {exc}", file=sys.stderr)
    return url


def _normalize_text_check(content: str) -> str:
    text = re.sub(r"<[^>]+>", "", content or "")
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _cleanup_empty_paragraphs(html: str) -> str:
    """
    Remove empty paragraphs (typically <p>&nbsp;</p>, <p/> or whitespace-only) that
    Confluence often inserts between and after topics inside the Release Summary.

    This keeps the content inside each weekly card tight so we don't see large
    visual gaps between topics (e.g. between 'RCZ & Drive Alerts' and
    'Input Validation').
    """
    if not html:
        return html

    # Remove empty/nbsp paragraphs anywhere in the snippet
    any_empty = re.compile(
        r"\s*<p[^>]*>(?:&nbsp;|&#160;|\s)*</p>\s*",
        re.IGNORECASE,
    )
    html = re.sub(any_empty, " ", html)

    # Remove self-closing empty p tags (Confluence often outputs <p local-id="..." />)
    html = re.sub(r"\s*<p[^>]*/\s*>\s*", " ", html, flags=re.IGNORECASE)

    # As a safety net, strip any remaining empty paragraphs at the very end
    trailing = re.compile(
        r"(?:\s*<p[^>]*>(?:&nbsp;|&#160;|\s)*</p>\s*)+$",
        re.IGNORECASE,
    )
    return re.sub(trailing, "", html).strip()


def extract_release_summary_sections(storage_html: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    if not storage_html:
        return sections

    # Extract only the "Release Summary" block (up to TABLE of Contents).
    # Without this boundary, when heading levels change (H1/H2), we may
    # accidentally capture Confluence's TOC + following sections into one
    # subsection (e.g., "What we fixed").
    summary_boundary_pattern = re.compile(
        r"<h[1-2][^>]*>\s*Release Summary\s*</h[1-2]>\s*(?P<content>.*?)(?=<h[1-6][^>]*>\s*TABLE\s*of\s*Contents\s*</h[1-6]>|$)",
        re.IGNORECASE | re.DOTALL,
    )
    m = summary_boundary_pattern.search(storage_html)
    if not m:
        return sections

    content = (m.group("content") or "").strip()
    if not _normalize_text_check(content):
        return sections

    all_variants = []
    for variants in SUMMARY_SECTION_VARIANTS.values():
        all_variants.extend(variants)
    all_headers = "|".join(re.escape(v) for v in all_variants)

    for canonical, variants in SUMMARY_SECTION_VARIANTS.items():
        header_pattern = "(?:" + "|".join(re.escape(v) for v in variants) + ")"
        section_pattern = re.compile(
            rf"<h[2-3][^>]*>\s*{header_pattern}\s*</h[2-3]>(?P<section_content>.*?)(?=<h[2-3][^>]*>\s*(?:{all_headers})\s*</h[2-3]>|$)",
            re.IGNORECASE | re.DOTALL,
        )
        section_match = section_pattern.search(content)
        if not section_match:
            continue

        section_content = (section_match.group("section_content") or "").strip()
        section_content = re.sub(r"<hr[^>]*>", "", section_content, flags=re.IGNORECASE)
        if _normalize_text_check(section_content):
            sections[canonical] = section_content

    return sections


def month_label(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def _week_key_to_tuple(week_key: str) -> Optional[Tuple[int, int]]:
    m = re.match(r"^(\d{4})-W(\d{2})$", (week_key or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _version_sort_tuple(version_text: str) -> Tuple[int, ...]:
    numbers = re.findall(r"\d+", version_text or "")
    if not numbers:
        return tuple()
    return tuple(int(value) for value in numbers)


def _pick_latest_from_csv(raw_value: str) -> str:
    chunks = [part.strip() for part in (raw_value or "").split(",") if part.strip()]
    if not chunks:
        return ""
    return sorted(chunks, key=_version_sort_tuple)[-1]


def _is_missing_version(value: Optional[str]) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.lower() == "none"


def load_weekly_stopper() -> Dict[str, Dict[str, Optional[str]]]:
    if not os.path.exists(WEEKLY_STOPPER):
        return {}
    try:
        with open(WEEKLY_STOPPER, "r", encoding="utf-8") as f:
            payload = json.load(f)
            if isinstance(payload, dict):
                return payload
            return {}
    except Exception:
        return {}


def latest_component_versions_for_month(weeks: List[Tuple[int, int]]) -> Dict[str, str]:
    stopper = load_weekly_stopper()
    if not stopper or not weeks:
        return {}

    month_week_keys = {f"{year}-W{week:02d}" for year, week in weeks}
    anchor = sorted(month_week_keys, key=lambda k: _week_key_to_tuple(k) or (0, 0))[-1]
    anchor_tuple = _week_key_to_tuple(anchor)
    if not anchor_tuple:
        return {}

    all_weeks_sorted = sorted(
        [
            (key, _week_key_to_tuple(key))
            for key in stopper.keys()
            if _week_key_to_tuple(key) is not None
        ],
        key=lambda pair: pair[1],
    )
    candidate_weeks = [
        key
        for key, key_tuple in all_weeks_sorted
        if key_tuple and key_tuple <= anchor_tuple
    ]
    if not candidate_weeks:
        return {}

    # Preserve component order from the latest available week snapshot.
    latest_snapshot = stopper.get(candidate_weeks[-1], {}) or {}
    component_order = list(latest_snapshot.keys())

    # Normalize historical naming differences.
    aliases = {
        "PATRIC": ["PATRIC", "PATRIC-SSDP"],
        "PATRIC-SSDP": ["PATRIC-SSDP", "PATRIC"],
    }

    result: Dict[str, str] = {}
    for component in component_order:
        lookup_names = aliases.get(component, [component])
        selected = ""

        for week_key in reversed(candidate_weeks):
            week_data = stopper.get(week_key, {}) or {}
            raw_value = None
            for lookup in lookup_names:
                if lookup in week_data:
                    raw_value = week_data.get(lookup)
                    if not _is_missing_version(raw_value):
                        break

            if _is_missing_version(raw_value):
                continue

            selected = _pick_latest_from_csv(str(raw_value))
            if selected:
                break

        result[component] = selected or "-"

    return result


def generate_monthly_html(
    year: int,
    month: int,
    week_entries: List[Dict[str, str]],
    component_versions: Dict[str, str],
) -> str:
    def _render_section_heading(canonical: str) -> str:
        # Use the original headings (with unicode emoji).
        return canonical

    links = []
    section_buckets: Dict[str, List[str]] = {key: [] for key in SUMMARY_SECTION_VARIANTS.keys()}

    for entry in week_entries:
        week_key = entry["week"]
        title = entry["title"]
        url = entry.get("url", "")
        summary_sections = entry.get("summary_sections", {})

        if url:
            links.append(
                f"<li><a target='_blank' href='{url}' style='color:#0066CC;text-decoration:none;'>{title}</a> ({week_key})</li>"
            )
        else:
            links.append(f"<li>{title} ({week_key})</li>")

        for canonical in SUMMARY_SECTION_VARIANTS.keys():
            raw_section_html = summary_sections.get(canonical, "")
            section_html = _cleanup_empty_paragraphs(raw_section_html)
            if not section_html:
                continue
            section_buckets[canonical].append(
                f"""
                <div style="margin:4px 0;padding:10px 12px;border:1px solid #DFE1E6;border-radius:6px;background:#F8F9FA;">
                    {section_html}
                </div>
                """
            )

    summary_parts = []
    for canonical in SUMMARY_SECTION_VARIANTS.keys():
        grouped = section_buckets.get(canonical, [])
        if not grouped:
            continue
        # Space above each subsection header: 16px for first, 24px for rest (clear gap after previous section)
        is_first = len(summary_parts) == 0
        margin_top = "16px" if is_first else "24px"
        heading = _render_section_heading(canonical)
        summary_parts.append(
            f"""
            <h2 style="margin-top:{margin_top};margin-bottom:8px;">{heading}</h2>
            {''.join(grouped)}
            """
        )

    summary_section = "".join(summary_parts) if summary_parts else "<p>No manual Release Summary content found for the selected month.</p>"
    links_section = "\n".join(links) if links else "<li>No weekly pages found for this month.</li>"
    component_rows = "\n".join(
        f"<tr><td style='padding:8px 10px;border:1px solid #DFE1E6;'>{component}</td><td style='padding:8px 10px;border:1px solid #DFE1E6;'>{version}</td></tr>"
        for component, version in component_versions.items()
    )
    components_table = (
        f"""
    <table style="border-collapse:collapse;width:100%;margin-top:8px;">
        <thead>
            <tr>
                <th style="text-align:left;padding:8px 10px;border:1px solid #DFE1E6;background:#F4F5F7;">Component</th>
                <th style="text-align:left;padding:8px 10px;border:1px solid #DFE1E6;background:#F4F5F7;">Latest Version</th>
            </tr>
        </thead>
        <tbody>
            {component_rows}
        </tbody>
    </table>
        """.strip()
        if component_rows
        else "<p>No component version data found in weekly_stopper.json.</p>"
    )

    return f"""
<div>
    <h1 style="margin-top:24px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Executive Release Summary</h1>
    <div style="margin:8px 0 14px 0;padding:10px 12px;border:1px solid #DFE1E6;border-radius:6px;background:#F8F9FA;">
        <p style="margin:0;"><em>To be filled manually.</em></p>
    </div>

    <h1 style="margin-top:24px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Release Summary</h1>
    {summary_section}

    <h1 style="margin-top:24px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Component Latest Versions</h1>
    {components_table}

    <h1 style="margin-top:26px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Release Notes details by week</h1>
    <ul style="line-height:1.9;">
        {links_section}
    </ul>
</div>
""".strip()


def main() -> None:
    args = parse_args()
    validate_env(require_parent=args.publish)
    year, month = parse_month(args.month)

    weeks = iso_weeks_in_month(year, month)
    component_versions = latest_component_versions_for_month(weeks)
    entries: List[Dict[str, str]] = []

    for iso_year, iso_week in weeks:
        week_key = f"{iso_year}-W{iso_week:02d}"
        prefix = f"SSDP Release Notes Week {week_key}"
        page = confluence_search_week_page(prefix)
        if not page:
            continue

        page_id = page.get("id", "")
        title = page.get("title", prefix)
        html, page_url = confluence_get_page_storage(page_id)
        summary_sections = extract_release_summary_sections(html)

        entries.append(
            {
                "week": week_key,
                "title": title,
                "url": page_url,
                "summary_sections": summary_sections,
            }
        )

    final_html = generate_monthly_html(year, month, entries, component_versions)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"Monthly report generated: {args.output}")
    print(f"Weeks included: {len(entries)}")
    if entries:
        print("Weeks:", ", ".join(e.get("week", "") for e in entries if e.get("week")))

    if args.publish:
        title = f"SSDP Monthly Report - {month_label(year, month)}"
        url = publish_monthly_page(
            title,
            f"<div>{final_html}</div>",
            skip_if_unchanged=args.skip_if_unchanged,
        )
        if url:
            print(f"CONFLUENCE_PAGE_URL={url}")
        else:
            print("Failed to publish monthly report to Confluence.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()

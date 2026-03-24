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
    return parser.parse_args()


def validate_env(require_parent: bool = False) -> None:
    required = {
        "CONFLUENCE_USERNAME": USERNAME,
        "CONFLUENCE_API_TOKEN": API_TOKEN,
        "CONFLUENCE_SPACE_KEY": SPACE_KEY,
    }
    if require_parent:
        required["CONFLUENCE_PARENT_PAGE_ID"] = PARENT_PAGE_ID

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
    _, last_day = calendar.monthrange(year, month)
    weeks = set()
    for day in range(1, last_day + 1):
        d = datetime.date(year, month, day)
        iso = d.isocalendar()
        iso_year = iso[0]
        iso_week = iso[1]

        # Monthly ownership rule:
        # A week belongs to the month where its ISO week-start day (Monday) falls.
        # Example: week Feb 23 to Mar 1 belongs to February only.
        week_start = datetime.date.fromisocalendar(iso_year, iso_week, 1)
        if week_start.year == year and week_start.month == month:
            weeks.add((iso_year, iso_week))

    return sorted(weeks)


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

    # pick best title match: startswith weekly prefix
    for item in results:
        title = item.get("title", "")
        if title.startswith(prefix_title):
            return item

    # fallback first result
    return results[0]


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


def confluence_update_page(page_id: str, title: str, html: str) -> Optional[str]:
    get_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    info_resp = requests.get(
        get_url,
        auth=(USERNAME, API_TOKEN),
        params={"expand": "version"},
        timeout=20,
    )
    if info_resp.status_code != 200:
        print(f"Failed to fetch page info for update: {info_resp.text}", file=sys.stderr)
        return None

    info = info_resp.json()
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


def confluence_create_page(title: str, html: str) -> Optional[str]:
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    data = {
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "ancestors": [{"id": PARENT_PAGE_ID}],
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
        return None

    res = r.json()
    links = res.get("_links", {})
    return (links.get("base") or "") + (links.get("webui") or "")


def publish_monthly_page(title: str, html: str) -> Optional[str]:
    page_id = confluence_find_page_id_by_title(title)
    if page_id:
        return confluence_update_page(page_id, title, html)
    return confluence_create_page(title, html)


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

    pattern = re.compile(
        r"<h2[^>]*>\s*Release Summary\s*</h2>(?P<content>.*?)(?=<h2[^>]*>|$)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(storage_html)
    if not m:
        return sections

    content = (m.group("content") or "").strip()

    # Confluence pages may include TOC and following sections before the next <h2>.
    # Keep only the manually edited summary part.
    split_markers = [
        r"<h3[^>]*>\s*TABLE\s*of\s*Contents\s*</h3>",
        r":table-of-contents",
        r"<hr[^>]*>",
    ]
    for marker in split_markers:
        parts = re.split(marker, content, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            content = parts[0].strip()
            break

    if not _normalize_text_check(content):
        return sections

    all_variants = []
    for variants in SUMMARY_SECTION_VARIANTS.values():
        all_variants.extend(variants)
    all_headers = "|".join(re.escape(v) for v in all_variants)

    for canonical, variants in SUMMARY_SECTION_VARIANTS.items():
        header_pattern = "(?:" + "|".join(re.escape(v) for v in variants) + ")"
        section_pattern = re.compile(
            rf"<h3[^>]*>\s*{header_pattern}\s*</h3>(?P<section_content>.*?)(?=<h3[^>]*>\s*(?:{all_headers})\s*</h3>|$)",
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
        summary_parts.append(
            f"""
            <h3 style="margin-top:{margin_top};margin-bottom:8px;">{canonical}</h3>
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
    <h2 style="margin-top:24px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Executive Release Summary (planned)</h2>
    <div style="margin:8px 0 14px 0;padding:10px 12px;border:1px solid #DFE1E6;border-radius:6px;background:#F8F9FA;">
        <p style="margin:0;"><em>To be filled manually.</em></p>
    </div>

    <h2 style="margin-top:24px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Release Summary</h2>
    {summary_section}

    <h2 style="margin-top:24px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Component Latest Versions</h2>
    {components_table}

    <h2 style="margin-top:26px;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:8px;">Release Notes details by week</h2>
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

    if args.publish:
        title = f"SSDP Monthly Report - {month_label(year, month)}"
        url = publish_monthly_page(title, f"<div>{final_html}</div>")
        if url:
            print(f"CONFLUENCE_PAGE_URL={url}")
        else:
            print("Failed to publish monthly report to Confluence.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()

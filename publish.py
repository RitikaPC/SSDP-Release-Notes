#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()

import os
import json
import requests
import datetime
import sys
import re

CONFLUENCE_BASE_URL = "https://eng-stla.atlassian.net/wiki"

USERNAME = os.getenv("CONFLUENCE_USERNAME")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY")
PARENT_PAGE_ID = os.getenv("CONFLUENCE_PARENT_PAGE_ID")

# Hard fail if anything is missing (REQUIRED on Render)
missing = [
    name for name, value in {
        "CONFLUENCE_USERNAME": USERNAME,
        "CONFLUENCE_API_TOKEN": API_TOKEN,
        "CONFLUENCE_SPACE_KEY": SPACE_KEY,
        "CONFLUENCE_PARENT_PAGE_ID": PARENT_PAGE_ID,
    }.items()
    if not value
]

if missing:
    raise RuntimeError(
        f"Missing Confluence environment variables: {', '.join(missing)}"
    )

SUMMARY_HTML = os.getenv("SUMMARY_HTML", "summary_output.html")
WEEK_FILE = os.getenv("WEEK_FILE", "week_number.txt")

forced_week_raw = None
forced_week = None
forced_year = None
if "--week" in sys.argv:
    forced_week_raw = sys.argv[sys.argv.index("--week") + 1]

def parse_week_arg(raw: str):
    if not raw:
        return None, None
    raw = raw.strip()
    m = re.match(r"^(?P<year>\d{4})[-_ ]*W?(?P<week>\d{1,2})$", raw)
    if m:
        return int(m.group("year")), int(m.group("week"))
    m2 = re.match(r"^W?(?P<week>\d{1,2})$", raw)
    if m2:
        return None, int(m2.group("week"))
    return None, None

if forced_week_raw:
    forced_year, forced_week = parse_week_arg(forced_week_raw)


def read_week():
    # priority: forced CLI, then WEEK_FILE content, then today
    if forced_week is not None:
        if forced_year:
            return f"{forced_year}-W{forced_week:02d}"
        # Even without forced_year, use current year for consistency
        current_year = datetime.date.today().year
        return f"{current_year}-W{forced_week:02d}"

    if os.path.exists(WEEK_FILE):
        try:
            content = open(WEEK_FILE, "r").read().strip()
            # If content doesn't have year format, add current year
            if not re.match(r"^\d{4}-W\d{1,2}$", content):
                try:
                    week_num = int(content)
                    current_year = datetime.date.today().year
                    content = f"{current_year}-W{week_num:02d}"
                except ValueError:
                    pass
            return content
        except Exception:
            pass

    # fallback - always use year format
    today = datetime.date.today()
    wk = today.isocalendar()[1]
    return f"{today.year}-W{wk:02d}"


def confluence_search_page(title):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    params = {"spaceKey": SPACE_KEY, "title": title}
    r = requests.get(url, auth=(USERNAME, API_TOKEN), params=params)
    if r.status_code != 200:
        print("Page search failed:", r.text)
        return None

    results = r.json().get("results", [])
    return results[0]["id"] if results else None


def confluence_update_page(page_id, title, html):
    get_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    r_info = requests.get(get_url, auth=(USERNAME, API_TOKEN))
    if r_info.status_code != 200:
        print("Failed to fetch page info:", r_info.text)
        return None

    info = r_info.json()
    new_ver = info["version"]["number"] + 1

    data = {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "version": {"number": new_ver},
        "body": {"storage": {"value": html, "representation": "storage"}}
    }

    put_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    r = requests.put(
        put_url,
        auth=(USERNAME, API_TOKEN),
        headers={"Content-Type": "application/json"},
        data=json.dumps(data)
    )

    if r.status_code in (200, 201):
        res = r.json()
        return res["_links"]["base"] + res["_links"]["webui"]

    print("Update failed:", r.text)
    return None


def confluence_create_page(title, html):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"

    data = {
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "ancestors": [{"id": PARENT_PAGE_ID}],
        "body": {"storage": {"value": html, "representation": "storage"}}
    }

    r = requests.post(
        url,
        auth=(USERNAME, API_TOKEN),
        headers={"Content-Type": "application/json"},
        data=json.dumps(data)
    )

    if r.status_code in (200, 201):
        res = r.json()
        return res["_links"]["base"] + res["_links"]["webui"]

    print("Create failed:", r.text)
    return None


def main():
    week = read_week()
    
    # Since we now always use year format, title generation is simpler
    title = f"SSDP Release Notes Week {week}"

    if not os.path.exists(SUMMARY_HTML):
        print("summary_output.html missing")
        sys.exit(1)

    html = open(SUMMARY_HTML, "r", encoding="utf-8").read()
    html = f"<div>{html}</div>"

    page_id = confluence_search_page(title)
    if page_id:
        url = confluence_update_page(page_id, title, html)
    else:
        url = confluence_create_page(title, html)

    if url:
        print(f"CONFLUENCE_PAGE_URL={url}")
    else:
        print("Failed to publish page.")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import os
import json
import requests
import datetime
import sys

CONFLUENCE_BASE_URL = "https://eng-stla.atlassian.net/wiki"
USERNAME = "ritika.palchaudhuri@stellantis.com"
API_TOKEN = "ATATT3xFfGF0l3WsLauZ3IUi0ZngpkPh2-ES2Ti9BKg2GeX_4LC0lBvtPUQ4PR95LrxZDsjqlikz2DesIGYzJ_mVbIHpcylUOfhQMarL9QRJGnVlqtYfXby08jJylWWuTM0byBdw1XHX03X08Ikb-PcuhJh0bQJsTMElC6rHV0Q8oMGsF5yBWCo=FA51292A"
SPACE_KEY = "~7120205aff550fb14a4887972f52ed690ad96b"
PARENT_PAGE_ID = "2314764925"

SUMMARY_HTML = "summary_output.html"
WEEK_FILE = "week_number.txt"

forced_week = None
if "--week" in sys.argv:
    forced_week = int(sys.argv[sys.argv.index("--week") + 1])


def read_week():
    if forced_week is not None:
        return forced_week
    if os.path.exists(WEEK_FILE):
        try:
            return int(open(WEEK_FILE, "r").read().strip())
        except Exception:
            pass
    return datetime.date.today().isocalendar()[1]


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
        print("Page published:", url)
    else:
        print("Failed to publish page.")
        sys.exit(1)


if __name__ == "__main__":
    main()

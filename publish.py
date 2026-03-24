#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()

import os
import json
import requests
import datetime
import sys
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CONFLUENCE_BASE_URL = "https://eng-stla.atlassian.net/wiki"

USERNAME = os.getenv("CONFLUENCE_USERNAME")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY")
PARENT_PAGE_ID = os.getenv("CONFLUENCE_PARENT_PAGE_ID")

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_USERNAME = os.getenv("EMAIL_USERNAME")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
NOTIFICATION_RECIPIENTS = os.getenv("NOTIFICATION_RECIPIENTS", "").split(",") if os.getenv("NOTIFICATION_RECIPIENTS") else []

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


def _ordinal_day(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def week_date_range_text(week_display: str) -> str:
    m = re.match(r"^(?P<year>\d{4})-W(?P<week>\d{1,2})$", week_display)
    if not m:
        return ""

    year = int(m.group("year"))
    week = int(m.group("week"))
    start_date = datetime.date.fromisocalendar(year, week, 1)
    end_date = datetime.date.fromisocalendar(year, week, 7)
    start_text = f"{start_date.day} {start_date.strftime('%b %Y')}"
    end_text = f"{_ordinal_day(end_date.day)} {end_date.strftime('%B %Y')}"
    return f"{start_text} to {end_text}"


def check_page_exists(title):
    """Check if a Confluence page with the given title already exists."""
    search_url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    params = {
        "spaceKey": SPACE_KEY,
        "title": title,
        "limit": 1
    }
    
    try:
        r = requests.get(search_url, auth=(USERNAME, API_TOKEN), params=params, timeout=10)
        if r.status_code == 200:
            results = r.json().get("results", [])
            return len(results) > 0
    except Exception as e:
        print(f"Warning: Could not check if page exists '{title}': {e}", file=sys.stderr)
    return False


def send_notification_email(week_display, confluence_url):
    """Send email notification using Stellantis corporate email via Microsoft Graph API."""
    client_id = os.getenv('AZURE_CLIENT_ID')
    client_secret = os.getenv('AZURE_CLIENT_SECRET')
    tenant_id = os.getenv('AZURE_TENANT_ID')
    sender_email = os.getenv('STELLANTIS_EMAIL')
    recipients = os.getenv('NOTIFICATION_RECIPIENTS', '').strip()
    
    if not all([client_id, client_secret, tenant_id, sender_email]):
        print("⚠️  Microsoft Graph API not configured - missing Azure credentials")
        print(f"\n📋 Manual notification needed:")
        print(f"   Week: {week_display}")
        print(f"   Link: {confluence_url}")
        print(f"   Recipients: {recipients}")
        return False
        
    if not recipients:
        print("⚠️  No recipients configured")
        return False
    
    recipient_list = [email.strip() for email in recipients.split(',') if email.strip()]
    
    print("📧 Sending email via Stellantis corporate email...")
    
    try:
        # Get access token
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        token_data = {
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
            'scope': 'https://graph.microsoft.com/.default'
        }
        
        token_response = requests.post(token_url, data=token_data)
        if token_response.status_code != 200:
            print(f"❌ Failed to get access token: {token_response.text}")
            print(f"\n📋 Manual notification needed:")
            print(f"   Week: {week_display}")
            print(f"   Link: {confluence_url}")
            print(f"   Recipients: {', '.join(recipient_list)}")
            return False
            
        access_token = token_response.json().get('access_token')
        
        # Prepare email content
        subject = f"SSDP Release Notes Generated - Week {week_display}"
        body_content = f"""Dear Team,

The SSDP Release Notes for Week {week_display} have been generated and published on Confluence.

📋 **Release Notes Link**: {confluence_url}

📌 **Important To-Dos:**
• The page opens in a new tab - please ensure that pop-ups are enabled in your browser.
• After the Go/No-Go meeting, make sure to update the Enabler workflow status correctly when moving to "In Production" / "Deploying to Prod".
• Ensure the Deploy Date is updated accurately before generating the final release notes.
• Verify and enter the correct Enabler version to avoid inconsistencies in the published page.

This notification is sent only for newly generated release notes pages.

Best regards,
SSDRP Release Notes Automation
"""
        
        # Prepare recipients for Graph API
        to_recipients = [{'emailAddress': {'address': email}} for email in recipient_list]
        
        # Email payload
        email_data = {
            'message': {
                'subject': subject,
                'body': {
                    'contentType': 'Text',
                    'content': body_content
                },
                'toRecipients': to_recipients
            },
            'saveToSentItems': 'true'
        }
        
        # Send email via Graph API
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        send_url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/sendMail"
        send_response = requests.post(send_url, headers=headers, json=email_data)
        
        if send_response.status_code == 202:
            print(f"✅ Email sent via Stellantis email to: {', '.join(recipient_list)}")
            return True
        else:
            print(f"❌ Graph API failed: {send_response.status_code} - {send_response.text}")
            print(f"\n📋 Manual notification needed:")
            print(f"   Week: {week_display}")
            print(f"   Link: {confluence_url}")
            print(f"   Recipients: {', '.join(recipient_list)}")
            return False
            
    except Exception as e:
        print(f"❌ Microsoft Graph API error: {e}")
        print(f"\n📋 Manual notification needed:")
        print(f"   Week: {week_display}")
        print(f"   Link: {confluence_url}")
        print(f"   Recipients: {', '.join(recipient_list)}")
        return False


def confluence_search_page(title):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
    params = {"spaceKey": SPACE_KEY, "title": title}
    r = requests.get(url, auth=(USERNAME, API_TOKEN), params=params)
    if r.status_code != 200:
        print("Page search failed:", r.text)
        return None

    results = r.json().get("results", [])
    return results[0]["id"] if results else None


SUMMARY_SECTIONS = {
    "What we added:": [
        "What we added:",
        ":plus: What we added:",
        "➕ What we added:",
    ],
    "What we changed:": [
        "What we changed:",
        ":git-extension: What we changed:",
        "🔧 What we changed:",
    ],
    "What we Deprecated/ Removed:": [
        "What we Deprecated/ Removed:",
        ":put_litter_in_its_place: What we Deprecated/ Removed:",
        "🚮 What we Deprecated/ Removed:",
    ],
    "What we fixed:": [
        "What we fixed:",
        ":hammer: What we fixed:",
        "🔨 What we fixed:",
        "🛠 What we fixed:",
        "🛠️ What we fixed:",
    ],
}


def _header_pattern_for(label: str) -> str:
    variants = SUMMARY_SECTIONS.get(label, [label])
    return "(?:" + "|".join(re.escape(v) for v in variants) + ")"


def _normalize_content_for_check(content: str) -> str:
    text = re.sub(r"<[^>]+>", "", content or "")
    text = text.replace("&nbsp;", " ").strip()
    return text


def extract_manual_summary_sections(existing_html: str):
    sections = {}
    if not existing_html:
        return sections

    all_header_variants = []
    for variants in SUMMARY_SECTIONS.values():
        all_header_variants.extend(variants)
    all_headers = "|".join(re.escape(h) for h in all_header_variants)

    for header in SUMMARY_SECTIONS:
        header_pattern = _header_pattern_for(header)
        pattern = re.compile(
            rf"<h[2-3][^>]*>\s*{header_pattern}\s*</h[2-3]>(?P<content>.*?)(?=<h[1-6][^>]*>|<hr[^>]*>|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(existing_html)
        if not match:
            continue
        content = (match.group("content") or "").strip()
        if _normalize_content_for_check(content):
            sections[header] = content

    return sections


def merge_manual_summary_sections(generated_html: str, existing_html: str) -> str:
    preserved = extract_manual_summary_sections(existing_html)
    if not preserved:
        return generated_html

    merged = generated_html
    for header in SUMMARY_SECTIONS:
        content = preserved.get(header)
        if not content:
            continue

        header_pattern = _header_pattern_for(header)
        pattern = re.compile(
            rf"(<h[2-3][^>]*>\s*{header_pattern}\s*</h[2-3]>)\s*<p>\s*&nbsp;\s*</p>",
            re.IGNORECASE | re.DOTALL,
        )
        merged = pattern.sub(rf"\1\n{content}", merged, count=1)

    return merged


DEFAULT_STATUS_BLOCK = (
    "<p><strong>Status:</strong> "
    "<ac:structured-macro ac:name=\"status\">"
    "<ac:parameter ac:name=\"title\">IN PROGRESS</ac:parameter>"
    "<ac:parameter ac:name=\"colour\">Blue</ac:parameter>"
    "</ac:structured-macro>"
    "</p>"
)


def extract_existing_status_block(existing_html: str) -> str:
    """
    Preserve any manually changed top-level status line.
    We only scan before the Release Summary header to avoid matching table rows.
    """
    if not existing_html:
        return ""

    rs_header = re.search(
        r"<h[1-6][^>]*id\s*=\s*['\"]release-summary['\"][^>]*>",
        existing_html,
        flags=re.IGNORECASE,
    )
    prefix = existing_html[: rs_header.start()] if rs_header else existing_html[:2000]

    status_pattern = re.compile(
        r"<p[^>]*>\s*(?:<strong>\s*)?Status:\s*(?:</strong>\s*)?.*?</p>",
        re.IGNORECASE | re.DOTALL,
    )
    m = status_pattern.search(prefix)
    return m.group(0).strip() if m else ""


def upsert_top_status_block(html: str, status_block: str) -> str:
    """Insert or replace the top-level status block right after opening <div>."""
    if not html:
        return html

    # Replace existing top status line if present.
    status_pattern = re.compile(
        r"(<div[^>]*>\s*)(<p[^>]*>\s*(?:<strong>\s*)?Status:\s*(?:</strong>\s*)?.*?</p>)",
        re.IGNORECASE | re.DOTALL,
    )
    if status_pattern.search(html):
        return status_pattern.sub(rf"\1{status_block}", html, count=1)

    # Otherwise insert after first wrapper div.
    return re.sub(r"(<div[^>]*>)", rf"\1\n{status_block}", html, count=1, flags=re.IGNORECASE)


def confluence_update_page(page_id, title, html):
    get_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    r_info = requests.get(
        get_url,
        auth=(USERNAME, API_TOKEN),
        params={"expand": "body.storage,version"}
    )
    if r_info.status_code != 200:
        print("Failed to fetch page info:", r_info.text)
        return None

    info = r_info.json()
    existing_html = info.get("body", {}).get("storage", {}).get("value", "")
    html = merge_manual_summary_sections(html, existing_html)
    existing_status = extract_existing_status_block(existing_html)
    if existing_status:
        # Preserve manual status edits on every update.
        html = upsert_top_status_block(html, existing_status)
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
        print(f"Page updated successfully: {title}")
        return res["_links"]["base"] + res["_links"]["webui"]
    else:
        print(f"Update failed for {title}:", r.text)
        print(f"Status code: {r.status_code}")
        return None


def confluence_create_page(title, html):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content"

    html = upsert_top_status_block(html, DEFAULT_STATUS_BLOCK)

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

    week_dates = week_date_range_text(week)
    title = f"SSDP Release Notes Week {week} ({week_dates})" if week_dates else f"SSDP Release Notes Week {week}"

    if not os.path.exists(SUMMARY_HTML):
        print("summary_output.html missing")
        sys.exit(1)

    html = open(SUMMARY_HTML, "r", encoding="utf-8").read()
    html = f"<div>{html}</div>"

    # Check if this is a first-time generation (page doesn't exist yet)
    is_first_time_generation = not check_page_exists(title)

    page_id = confluence_search_page(title)
    if page_id:
        url = confluence_update_page(page_id, title, html)
    else:
        url = confluence_create_page(title, html)

    if url:
        print(f"CONFLUENCE_PAGE_URL={url}")
        
        # Send email notification only for first-time generation
        if is_first_time_generation:
            print(f"First-time generation detected for week {week} - sending notification email")
            send_notification_email(week, url)
        else:
            print(f"Page update for week {week} - no email notification sent")
    else:
        print("Failed to publish page.")
        sys.exit(1)


if __name__ == "__main__":
    main()

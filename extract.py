#!/usr/bin/env python3
"""
extract.py — Agile-board-based extractor with history-based selection for APIM/EAH

Behavior (high level)
 - APIM / EAH:
     * Discovered from agile board summaries using regex
     * Selected if their Jira changelog contains a first transition to "In production"
       whose ISO week/year equals the chosen week/current year
 - DOCG:
     * Discovered from agile board issues with summary starting "DOCG"
     * Selected if status == "In production" and first "In production" transition date is in chosen week/year
 - VDR:
     * Discovered from agile board issues with summary starting "VDR"
     * Selected if status == "Deploying to PROD" and deploy date (custom field or history) is in chosen week/year
 - PATRIC-SSDP:
     * Discovered from agile board issues with summary starting "PATRIC-SSDP-"
     * Selected if status == "In production" and first "In production" transition date is in chosen week/year
 - Writes Linked_Issues_Report.txt and updates weekly_stopper.json with latest versions discovered for the week
"""

import os
import sys
import re
import json
import datetime
import requests
from typing import List, Dict

# -----------------------
# Config
# -----------------------
JIRA_BASE = "https://stla-iotpf-jira.atlassian.net"

USERNAME = os.getenv("JIRA_USERNAME")
API_TOKEN = os.getenv("JIRA_API_TOKEN")

BOARD_ID = 35
QUICKFILTER_ID = 169

# Hard fail if env vars are missing (VERY IMPORTANT for Render)
if not USERNAME or not API_TOKEN:
    raise RuntimeError(
        "Missing JIRA credentials. "
        "Ensure JIRA_USERNAME and JIRA_API_TOKEN are set in Render Environment Variables."
    )

LINKED_FILE = "Linked_Issues_Report.txt"
WEEKLY_STOPPER = "weekly_stopper.json"

# kept for compatibility / fallback when scanning board
ALLOWED_STATUSES = {
    "Awaiting Go / No go PROD",
    "Deploying to PROD",
    "In production",
    "Done"
}

# -----------------------
# CLI args / week logic
# -----------------------
override_week = None
force_overwrite = False
if "--week" in sys.argv:
    try:
        override_week = int(sys.argv[sys.argv.index("--week") + 1])
    except Exception:
        override_week = None

if "--force" in sys.argv:
    force_overwrite = True

today = datetime.date.today()
current_year = today.isocalendar()[0]
week_num = override_week if override_week else today.isocalendar()[1]
week_str = str(week_num)

if not API_TOKEN:
    print("ERROR: Set env variable JIRA_API_TOKEN", file=sys.stderr)
    sys.exit(1)

# -----------------------
# Helpers
# -----------------------
def vtuple(v: str):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v))
    except Exception:
        return ()

def pick_latest(versions: List[str]):
    vs = [v for v in versions if v]
    if not vs:
        return None
    return sorted(vs, key=vtuple)[-1]

def load_stopper():
    if not os.path.exists(WEEKLY_STOPPER):
        return {}
    try:
        with open(WEEKLY_STOPPER, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_stopper(data):
    with open(WEEKLY_STOPPER, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def parse_iso_date(date_str: str):
    if not date_str:
        return None
    try:
        # Jira changelog dates are like 2025-11-17T12:34:56.000+0000 or 2025-11-17T12:34:56.000Z
        if "T" in date_str:
            return datetime.datetime.strptime(date_str.split("T")[0], "%Y-%m-%d").date()
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        # last resort: try to extract yyyy-mm-dd
        m = re.search(r"(\d{4}-\d{2}-\d{2})", date_str or "")
        if m:
            try:
                return datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except Exception:
                return None
        return None

# Regexes to detect versions from summary (you chose option A - use summary)
APIM_RE = re.compile(r"APIM[-\s]*([0-9]+\.[0-9]+\.[0-9]+)", re.IGNORECASE)
EAH_RE = re.compile(r"EAH[-\s]*([0-9]+\.[0-9]+\.[0-9]+)", re.IGNORECASE)
PATRIC_RE = re.compile(r"PATRIC-SSDP[-\s]*([0-9]+\.[0-9]+\.[0-9]+)", re.IGNORECASE)
RCZ_RE = re.compile(r"SSDP\s+RCZ[-\s]*([0-9]+\.[0-9]+\.[0-9]+)", re.IGNORECASE)

# -----------------------
# Jira session
# -----------------------
SESSION = requests.Session()
SESSION.auth = (USERNAME, API_TOKEN)
SESSION.headers.update({"Accept": "application/json"})

# -----------------------
# Jira API helpers
# -----------------------
def agile_board_issues(board_id: int, quickfilter=None, max_per_page=200) -> List[dict]:
    results = []
    start_at = 0

    while True:
        url = f"{JIRA_BASE}/rest/agile/1.0/board/{board_id}/issue"
        params = {
            "startAt": start_at,
            "maxResults": max_per_page,
            "fields": "summary,status,assignee,issuetype"
        }
        if quickfilter is not None:
            params["quickFilter"] = quickfilter

        resp = SESSION.get(url, params=params)
        if resp.status_code != 200:
            print(f"Agile API failed {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        issues = data.get("issues", [])
        total = data.get("total", len(issues))

        results.extend(issues)
        start_at += len(issues)
        if start_at >= total or not issues:
            break

    return results

def jira_get_issue_full(key: str) -> dict:
    url = f"{JIRA_BASE}/rest/api/3/issue/{key}"
    params = {
        "expand": "changelog",
        "fields": (
            "summary,status,assignee,issuetype,created,issuelinks,"
            "customfield_10041,customfield_10042,customfield_10043,customfield_10044"
        )
    }
    try:
        resp = SESSION.get(url, params=params)
    except Exception as e:
        print(f"Error fetching {key}: {e}", file=sys.stderr)
        return {}
    if resp.status_code != 200:
        print(f"Failed fetching {key}: {resp.status_code} - {resp.text}", file=sys.stderr)
        return {}
    return resp.json()

def extract_linked_issues_from_issue_json(issue_json: dict) -> List[Dict]:
    result = []
    fields = issue_json.get("fields", {})
    links = fields.get("issuelinks", []) or []

    for link in links:
        linked = link.get("outwardIssue") or link.get("inwardIssue")
        if not linked:
            continue

        key = linked.get("key", "")
        lf = linked.get("fields", {}) or {}
        issuetype = (lf.get("issuetype") or {}).get("name", "").lower()
        summary = (lf.get("summary") or "").strip()
        status = (lf.get("status") or {}).get("name", "")
        owner = (lf.get("assignee") or {}).get("displayName", "")
        created = lf.get("created") or ""

        # filters from original script
        if key.startswith("CVCP") or key.startswith("CVMP"):
            continue

        if link.get("type", {}).get("name", "").lower().startswith("cloner"):
            continue

        if not (
            key.startswith(("APIM", "EAH", "DOCG", "VDR", "VDP", "PATRIC"))
            or "story" in issuetype
            or "bug" in issuetype
        ):
            continue

        result.append({
            "key": key,
            "summary": summary,
            "status": status,
            "assignee": owner,
            "issuetype": lf.get("issuetype", {}).get("name", ""),
            "created": created
        })

    return result

# -----------------------
# History helpers
# -----------------------
def get_prod_date_from_history(issue_json):
    """Return first date (YYYY-MM-DD) where status -> 'In production' in changelog, else None"""
    histories = issue_json.get("changelog", {}).get("histories", [])
    for h in histories:
        for item in h.get("items", []):
            if item.get("field") == "status" and item.get("toString") == "In production":
                created = h.get("created")
                if created:
                    return created.split("T")[0]
    return None

def get_deploying_to_prod_date_from_history(issue_json):
    """Return first date (YYYY-MM-DD) where status -> 'Deploying to PROD' (or variant) in changelog, else None"""
    histories = issue_json.get("changelog", {}).get("histories", [])
    for h in histories:
        for item in h.get("items", []):
            if item.get("field") == "status" and item.get("toString") in ("Deploying to PROD", "Deploying To PROD"):
                created = h.get("created")
                if created:
                    return created.split("T")[0]
    return None

def get_rcz_release_date(issue_json):
    """
    RCZ rule:
    - Prefer first transition to 'In production'
    - Else first transition to 'Awaiting Go / No go PROD'
    - Else first transition to 'Deploying to PPROD'
    """
    histories = issue_json.get("changelog", {}).get("histories", [])

    priority = [
        "In production",
        "Awaiting Go / No go PROD",
        "Deploying to PROD",
    ]

    found = {}

    for h in histories:
        created = h.get("created")
        if not created:
            continue

        for item in h.get("items", []):
            if item.get("field") != "status":
                continue

            to_status = item.get("toString")
            if to_status in priority and to_status not in found:
                found[to_status] = created.split("T")[0]

    for status in priority:
        if status in found:
            return found[status]

    return None

# -----------------------
# Main: discover candidates from board
# -----------------------
stopper = load_stopper()

issues = agile_board_issues(BOARD_ID, quickfilter=QUICKFILTER_ID, max_per_page=200)

# We'll use these containers to hold candidates
records = []               # APIM/EAH discovered from summary regex
docg_candidate_keys = []
vdr_candidate_keys = []
patric_candidate_keys = []
rcz_candidate_keys = []

for it in issues:
    key = it.get("key")
    fields = it.get("fields", {}) or {}
    summary = (fields.get("summary") or "").strip()
    status = (fields.get("status") or {}).get("name", "") or ""
    assignee = (fields.get("assignee") or {}).get("displayName", "") or ""
    issuetype = (fields.get("issuetype") or {}).get("name", "") or ""

    # detect APIM / EAH via summary regex
    sys_ver = None
    m = APIM_RE.search(summary)
    if m:
        sys_ver = ("APIM", m.group(1))
    else:
        m2 = EAH_RE.search(summary)
        if m2:
            sys_ver = ("EAH", m2.group(1))

    if sys_ver:
        system, version = sys_ver
        version = version.strip().rstrip(".")
        records.append({
            "system": system,
            "version": version,
            "key": key,
            "summary": summary,
            "status": status,
            "assignee": assignee,
            "issuetype": issuetype
        })

    # enabler-type detection for DOCG/VDR/PATRIC based on summary prefix and issuetype
    if issuetype == "Enabler Version - IOT PF" and summary.startswith("DOCG"):
        docg_candidate_keys.append(key)

    if issuetype == "Enabler Version - IOT PF" and summary.startswith("VDR"):
        vdr_candidate_keys.append(key)

    if issuetype == "Enabler Version - IOT PF" and summary.startswith("PATRIC-SSDP-"):
        patric_candidate_keys.append(key)

    if issuetype == "Enabler Version - IOT PF" and summary.startswith("SSDP RCZ") :
        rcz_candidate_keys.append(key)

# -----------------------
# APIM/EAH selection (history-based)
# -----------------------
apim_eah_enablers = []  # list of dicts

for r in records:
    key = r["key"]
    sysname = r["system"]
    version = r["version"]

    full = jira_get_issue_full(key)
    if not full:
        continue

    prod_date_str = get_prod_date_from_history(full)
    prod_date = parse_iso_date(prod_date_str)
    if not prod_date:
        # no "In production" transition found — ignore
        continue

    iso_year, iso_week, _ = prod_date.isocalendar()
    if iso_year == current_year and iso_week == week_num:
        apim_eah_enablers.append({
            "key": key,
            "system": sysname,
            "version": version,
            "summary": r["summary"],
            "status": r["status"],
            "assignee": r.get("assignee") or "",
            "issuetype": r.get("issuetype") or "",
            "deploy_date": prod_date_str,
            "full": full
        })

# -----------------------
# DOCG selection (unchanged)
# -----------------------
docg_enablers = []
for key in docg_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue
    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name != "In production":
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "DOCG"
    enabler_version = (f.get("customfield_10042") or "").strip()

    deploy_date_str = get_prod_date_from_history(full)
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != week_num or iso_year != current_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")
    summary_full = (f.get("summary") or "").strip()

    docg_enablers.append({
        "key": key,
        "enabler_name": enabler_name,
        "enabler_version": enabler_version,
        "summary": summary_full,
        "status": status_name,
        "assignee": assignee,
        "issuetype": issuetype_name,
        "deploy_date": deploy_date_str,
        "full": full
    })

# -----------------------
# VDR selection (unchanged)
# -----------------------
vdr_enablers = []
for key in vdr_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")

    if status_name not in ("Deploying to PROD", "Deploying To PROD"):
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDR"
    enabler_version = (f.get("customfield_10042") or "").strip()

    full_fields = full.get("fields", {}) or {}
    prod_date_str = full_fields.get("customfield_10044")

    if not prod_date_str:
        prod_date_str = full_fields.get("customfield_10043")

    if not prod_date_str:
        prod_date_str = get_deploying_to_prod_date_from_history(full)

    prod_date = parse_iso_date(prod_date_str)
    if not prod_date:
        continue

    iso_year, iso_week, _ = prod_date.isocalendar()
    if iso_week != week_num or iso_year != current_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")
    summary_full = (f.get("summary") or "").strip()

    vdr_enablers.append({
        "key": key,
        "enabler_name": enabler_name,
        "enabler_version": enabler_version,
        "summary": summary_full,
        "status": status_name,
        "assignee": assignee,
        "issuetype": issuetype_name,
        "deploy_date": prod_date_str,
        "full": full
    })

# -----------------------
# PATRIC-SSDP selection (unchanged except regex)
# -----------------------
patric_enablers = []
for key in patric_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name != "In production":
        continue

    summary_full = (f.get("summary") or "").strip()
    enabler_name = "PATRIC-SSDP"

    enabler_version = (f.get("customfield_10042") or "").strip()
    if not enabler_version:
        m = PATRIC_RE.search(summary_full)
        if m:
            enabler_version = m.group(1)

    deploy_date_str = get_prod_date_from_history(full)
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != week_num or iso_year != current_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    patric_enablers.append({
        "key": key,
        "enabler_name": enabler_name,
        "enabler_version": enabler_version,
        "summary": summary_full,
        "status": status_name,
        "assignee": assignee,
        "issuetype": issuetype_name,
        "deploy_date": deploy_date_str,
        "full": full
    })

# -----------------------
# RCZ selection (same as PATRIC-SSDP, SSDP RCZ only)
# -----------------------
rcz_enablers = []
for key in rcz_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PPROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    if not summary_full.startswith("SSDP RCZ"):
        continue

    enabler_name = "RCZ"

    enabler_version = (f.get("customfield_10042") or "").strip()
    if not enabler_version:
        m = RCZ_RE.search(summary_full)
        if m:
            enabler_version = m.group(1)

    deploy_date_str = get_rcz_release_date(full)
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != week_num or iso_year != current_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    rcz_enablers.append({
        "key": key,
        "enabler_name": enabler_name,
        "enabler_version": enabler_version,
        "summary": summary_full,
        "status": status_name,
        "assignee": assignee,
        "issuetype": issuetype_name,
        "deploy_date": deploy_date_str,
        "full": full
    })

# -----------------------
# Write Linked_Issues_Report.txt
# -----------------------
if os.path.exists(LINKED_FILE):
    try:
        os.remove(LINKED_FILE)
    except Exception:
        pass

out_lines = []

# APIM / EAH blocks (history-based)
for item in sorted(apim_eah_enablers, key=lambda d: (d["system"], vtuple(d["version"]))):
    sysname = item["system"]
    ver = item["version"]
    out_lines.append(f"======= {sysname}-{ver} ({item['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {item['key']}")
    out_lines.append(f"Summary: {item['summary']}")
    out_lines.append(f"Status: {item['status']}")
    if item.get("assignee"):
        out_lines.append(f"Owner: {item['assignee']}")
    if item.get("issuetype"):
        out_lines.append(f"Issue Type: {item['issuetype']}")
    if item.get("deploy_date"):
        out_lines.append(f"Deploy Date: {item['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(item["full"])
    if linked:
        out_lines.append("Linked issues:")
        out_lines.append("")
        for l in linked:
            k = l.get("key") or ""
            summ = (l.get("summary") or "").replace("\n", " ").strip()
            owner = l.get("assignee") or ""
            st = l.get("status") or ""
            typ = l.get("issuetype") or ""
            created = l.get("created") or ""
            out_lines.append(f"Issue: {k}")
            out_lines.append(f"Summary: {summ}")
            out_lines.append(f"Status: {st}")
            if owner:
                out_lines.append(f"Owner: {owner}")
            if typ:
                out_lines.append(f"Issue Type: {typ}")
            if created:
                out_lines.append(f"Created: {created}")
            out_lines.append("")
    else:
        out_lines.append("No linked issues found.")
        out_lines.append("")
    out_lines.append("")

# DOCG blocks
for docg in sorted(docg_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = docg.get("enabler_name") or "DOCG"
    ver = docg.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({docg['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {docg['key']}")
    out_lines.append(f"Summary: {docg['summary']}")
    out_lines.append(f"Status: {docg['status']}")
    if docg.get("assignee"):
        out_lines.append(f"Owner: {docg['assignee']}")
    if docg.get("issuetype"):
        out_lines.append(f"Issue Type: {docg['issuetype']}")
    if docg.get("deploy_date"):
        out_lines.append(f"Deploy Date: {docg['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(docg["full"])
    if linked:
        out_lines.append("Linked issues:")
        out_lines.append("")
        for l in linked:
            k = l.get("key") or ""
            summ = (l.get("summary") or "").replace("\n", " ").strip()
            owner = l.get("assignee") or ""
            st = l.get("status") or ""
            typ = l.get("issuetype") or ""
            created = l.get("created") or ""
            out_lines.append(f"Issue: {k}")
            out_lines.append(f"Summary: {summ}")
            out_lines.append(f"Status: {st}")
            if owner:
                out_lines.append(f"Owner: {owner}")
            if typ:
                out_lines.append(f"Issue Type: {typ}")
            if created:
                out_lines.append(f"Created: {created}")
            out_lines.append("")
    else:
        out_lines.append("No linked issues found.")
        out_lines.append("")
    out_lines.append("")

# VDR blocks
for vdr in sorted(vdr_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = vdr.get("enabler_name") or "VDR"
    ver = vdr.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdr['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {vdr['key']}")
    out_lines.append(f"Summary: {vdr['summary']}")
    out_lines.append(f"Status: {vdr['status']}")
    if vdr.get("assignee"):
        out_lines.append(f"Owner: {vdr['assignee']}")
    if vdr.get("issuetype"):
        out_lines.append(f"Issue Type: {vdr['issuetype']}")
    if vdr.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdr['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdr["full"])
    if linked:
        out_lines.append("Linked issues:")
        out_lines.append("")
        for l in linked:
            k = l.get("key") or ""
            summ = (l.get("summary") or "").replace("\n", " ").strip()
            owner = l.get("assignee") or ""
            st = l.get("status") or ""
            typ = l.get("issuetype") or ""
            created = l.get("created") or ""
            out_lines.append(f"Issue: {k}")
            out_lines.append(f"Summary: {summ}")
            out_lines.append(f"Status: {st}")
            if owner:
                out_lines.append(f"Owner: {owner}")
            if typ:
                out_lines.append(f"Issue Type: {typ}")
            if created:
                out_lines.append(f"Created: {created}")
            out_lines.append("")
    else:
        out_lines.append("No linked issues found.")
        out_lines.append("")
    out_lines.append("")

# PATRIC-SSDP blocks
for patric in sorted(patric_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = patric.get("enabler_version") or ""
    header_name = f"PATRIC-SSDP-{ver}" if ver else "PATRIC-SSDP"

    out_lines.append(f"======= {header_name} ({patric['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {patric['key']}")
    out_lines.append(f"Summary: {patric['summary']}")
    out_lines.append(f"Status: {patric['status']}")
    if patric.get("assignee"):
        out_lines.append(f"Owner: {patric['assignee']}")
    if patric.get("issuetype"):
        out_lines.append(f"Issue Type: {patric['issuetype']}")
    if patric.get("deploy_date"):
        out_lines.append(f"Deploy Date: {patric['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(patric["full"])
    if linked:
        out_lines.append("Linked issues:")
        out_lines.append("")
        for l in linked:
            k = l.get("key") or ""
            summ = (l.get("summary") or "").replace("\n", " ").strip()
            owner = l.get("assignee") or ""
            st = l.get("status") or ""
            typ = l.get("issuetype") or ""
            created = l.get("created") or ""
            out_lines.append(f"Issue: {k}")
            out_lines.append(f"Summary: {summ}")
            out_lines.append(f"Status: {st}")
            if owner:
                out_lines.append(f"Owner: {owner}")
            if typ:
                out_lines.append(f"Issue Type: {typ}")
            if created:
                out_lines.append(f"Created: {created}")
            out_lines.append("")
    else:
        out_lines.append("No linked issues found.")
        out_lines.append("")
    out_lines.append("")

# RCZ blocks
for rcz in sorted(rcz_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = rcz.get("enabler_version") or ""
    header_name = f"RCZ-{ver}" if ver else "RCZ"

    out_lines.append(f"======= {header_name} ({rcz['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {rcz['key']}")
    out_lines.append(f"Summary: {rcz['summary']}")
    out_lines.append(f"Status: {rcz['status']}")
    if rcz.get("assignee"):
        out_lines.append(f"Owner: {rcz['assignee']}")
    if rcz.get("issuetype"):
        out_lines.append(f"Issue Type: {rcz['issuetype']}")
    if rcz.get("deploy_date"):
        out_lines.append(f"Deploy Date: {rcz['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(rcz["full"])
    if linked:
        out_lines.append("Linked issues:")
        out_lines.append("")
        for l in linked:
            k = l.get("key") or ""
            summ = (l.get("summary") or "").replace("\n", " ").strip()
            owner = l.get("assignee") or ""
            st = l.get("status") or ""
            typ = l.get("issuetype") or ""
            created = l.get("created") or ""
            out_lines.append(f"Issue: {k}")
            out_lines.append(f"Summary: {summ}")
            out_lines.append(f"Status: {st}")
            if owner:
                out_lines.append(f"Owner: {owner}")
            if typ:
                out_lines.append(f"Issue Type: {typ}")
            if created:
                out_lines.append(f"Created: {created}")
            out_lines.append("")
    else:
        out_lines.append("No linked issues found.")
        out_lines.append("")
    out_lines.append("")

# Save Linked_Issues_Report.txt
content = "\n".join(out_lines).rstrip() + ("\n" if out_lines else "")
with open(LINKED_FILE, "w", encoding="utf-8") as f:
    f.write(content)

# -----------------------
# Update weekly_stopper.json (STRICT STATUS RULES)
# -----------------------

store_entry = {
    "APIM": None,
    "EAH": None,
    "DOCG": None,
    "VDR": None,
    "PATRIC-SSDP": None,
    "RCZ": None
}

# -----------------------
# APIM / EAH → In production only
# -----------------------
for sysname in ("APIM", "EAH"):
    versions = [
        e["version"]
        for e in apim_eah_enablers
        if e["system"] == sysname and e.get("status") == "In production"
    ]
    if versions:
        store_entry[sysname] = ",".join(sorted(set(versions), key=vtuple))


# -----------------------
# DOCG → In production only
# -----------------------
docg_versions = [
    d["enabler_version"]
    for d in docg_enablers
    if d.get("status") == "In production" and d.get("enabler_version")
]
if docg_versions:
    store_entry["DOCG"] = ",".join(sorted(set(docg_versions), key=vtuple))


# -----------------------
# PATRIC-SSDP → In production only
# -----------------------
patric_versions = [
    p["enabler_version"]
    for p in patric_enablers
    if p.get("status") == "In production" and p.get("enabler_version")
]
if patric_versions:
    store_entry["PATRIC-SSDP"] = ",".join(sorted(set(patric_versions), key=vtuple))


# -----------------------
# RCZ → In production ONLY (NO Awaiting, NO Deploying)
# -----------------------
rcz_prod_versions = [
    r
    for r in rcz_enablers
    if r.get("status") == "In production"
       and r.get("enabler_version")
]

if rcz_prod_versions:
    store_entry["RCZ"] = ",".join(
        sorted(
            {r["enabler_version"] for r in rcz_prod_versions},
            key=vtuple
        )
    )

# -----------------------
# VDR → Deploying to PROD ONLY
# -----------------------
vdr_versions = [
    v["enabler_version"]
    for v in vdr_enablers
    if v.get("status") in ("Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdr_versions:
    store_entry["VDR"] = ",".join(sorted(set(vdr_versions), key=vtuple))


# -----------------------
# Persist stopper
# -----------------------
existing = stopper.get(week_str)
if existing is not None and not force_overwrite:
    print(f"Week {week_num} already exists in {WEEKLY_STOPPER}: {existing}")
    print("Use --force to overwrite.")
else:
    stopper[week_str] = store_entry
    save_stopper(stopper)
    print(f"Week {week_num} snapshot written: {store_entry}")

# -----------------------
# Summary output for CLI / debugging
# -----------------------
total_selected = len(apim_eah_enablers) + len(docg_enablers) + len(vdr_enablers) + len(patric_enablers)
print("Done extract.py")
print(json.dumps({
    "week": week_num,
    "curr_stoppler_entry": stopper.get(week_str),
    "apim_eah_selected": [
        {"key": d["key"], "system": d["system"], "version": d["version"], "deploy_date": d.get("deploy_date")}
        for d in apim_eah_enablers
    ],
    "docg_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "deploy_date": d.get("deploy_date")}
        for d in docg_enablers
    ],
    "vdr_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "deploy_date": d.get("deploy_date")}
        for d in vdr_enablers
    ],
    "patric_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "deploy_date": d.get("deploy_date")}
        for d in patric_enablers
    ],
    "counts": {
        "APIM/EAH": len(apim_eah_enablers),
        "DOCG": len(docg_enablers),
        "VDR": len(vdr_enablers),
        "PATRIC-SSDP": len(patric_enablers)
    },
    "linked_file": os.path.abspath(LINKED_FILE),
    "stopper_file": os.path.abspath(WEEKLY_STOPPER)
}, indent=2))

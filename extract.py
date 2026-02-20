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

from dotenv import load_dotenv
load_dotenv()

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

LINKED_FILE = os.getenv("LINKED_FILE", "Linked_Issues_Report.txt")
WEEKLY_STOPPER = os.getenv("WEEKLY_STOPPER", "weekly_stopper.json")

# kept for compatibility / fallback when scanning board
ALLOWED_STATUSES = {
    "Awaiting Go / No go PROD",
    "Deploying to PROD",
    "In production",
    "Done"
}

# -----------------------
# CLI args / week logic (supports optional year: e.g. 2026-50 or 2026-W50)
# -----------------------
override_year = None
override_week = None
force_overwrite = False

def parse_week_arg(raw: str):
    """Parse a week argument that may include a year. Returns (year, week) or (None, None).

    Acceptable forms:
      - "50" -> (None, 50)
      - "2026-50" or "2026-W50" -> (2026, 50)
      - "W50" -> (None, 50)
    """
    if not raw:
        return None, None
    raw = raw.strip()
    # year-week like 2026-W50 or 2026-50
    m = re.match(r"^(?P<year>\d{4})[-_ ]*W?(?P<week>\d{1,2})$", raw)
    if m:
        return int(m.group("year")), int(m.group("week"))
    # plain week like W50 or 50
    m2 = re.match(r"^W?(?P<week>\d{1,2})$", raw)
    if m2:
        return None, int(m2.group("week"))
    return None, None

if "--week" in sys.argv:
    raw = sys.argv[sys.argv.index("--week") + 1]
    y, w = parse_week_arg(raw)
    override_year = y
    override_week = w

if "--force" in sys.argv:
    force_overwrite = True

today = datetime.date.today()
current_year = today.isocalendar()[0]
target_year = override_year if override_year else current_year
target_week = override_week if override_week else today.isocalendar()[1]

# validate week number for the target year
def weeks_in_year(year: int) -> int:
    last_day = datetime.date(year, 12, 28)  # ISO week date trick
    return last_day.isocalendar()[1]

if target_week < 1 or target_week > weeks_in_year(target_year):
    print(f"ERROR: week {target_week} is not valid for year {target_year}", file=sys.stderr)
    sys.exit(1)

# canonical stopper key: always include year for consistency
week_str = f"{target_year}-W{target_week:02d}"

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
        # Jira changelog dates are like 2026-11-17T12:34:56.000+0000 or 2026-11-17T12:34:56.000Z
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
SERING_RE = re.compile(r"SERING[-\s]*([0-9]+\.[0-9]+\.[0-9]+)", re.IGNORECASE)

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

        try:
            resp = SESSION.get(url, params=params, timeout=30)
        except requests.exceptions.Timeout:
            print(f"Timeout fetching board issues at startAt={start_at}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}", file=sys.stderr)
            sys.exit(1)
            
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
        resp = SESSION.get(url, params=params, timeout=30)
    except requests.exceptions.Timeout:
        print(f"Timeout fetching {key}", file=sys.stderr)
        return {}
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

def get_awaiting_go_nogo_date_from_history(issue_json):
    """Return first date (YYYY-MM-DD) where status -> 'Awaiting Go / No go PROD' (or variations) in changelog, else None"""
    histories = issue_json.get("changelog", {}).get("histories", [])
    for h in histories:
        for item in h.get("items", []):
            if item.get("field") == "status":
                to_status = item.get("toString") or ""
                to_status_lower = to_status.lower()
                # Handle various capitalization and spacing patterns; require PROD as a whole word
                if (
                    "awaiting" in to_status_lower
                    and "go" in to_status_lower
                    and re.search(r"\bprod\b", to_status_lower)
                ):
                    created = h.get("created")
                    if created:
                        return created.split("T")[0]
    return None

def is_awaiting_go_nogo_status(status_name):
    """Check if status is a variant of 'Awaiting Go / No go PROD'"""
    if not status_name:
        return False
    lower_status = status_name.lower()
    return (
        "awaiting" in lower_status
        and "go" in lower_status
        and re.search(r"\bprod\b", lower_status)
    )

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
synapse_candidate_keys = []
reftel_candidate_keys = []
calva_candidate_keys = []
refser2_candidate_keys = []
sering_candidate_keys = []
vdp_proc_candidate_keys = []
vdp_ds_candidate_keys = []
vdp_ds_ssdp_candidate_keys = []
vdp_ds_mon_candidate_keys = []
vdp_store_candidate_keys = []
vdp_store_2_candidate_keys = []

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
    enabler_issue_types = ("Enabler Version - IOT PF", "Improve Enabler Version - IOT PF")
    
    if issuetype in enabler_issue_types and summary.startswith("DOCG"):
        docg_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("VDR"):
        vdr_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("PATRIC-SSDP-"):
        patric_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("SSDP RCZ"):
        rcz_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("SYNAPSE"):
        synapse_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("REFTEL"):
        reftel_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("CALVA"):
        calva_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("REFSER2"):
        refser2_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("SERING"):
        sering_candidate_keys.append(key)

    if issuetype in enabler_issue_types and (summary.startswith("VDP_PROC") or summary.startswith("VDP PROC")):
        vdp_proc_candidate_keys.append(key)

    if issuetype in enabler_issue_types and summary.startswith("VDP_DS"):
        # Differentiate VDP_DS variants by checking the summary title
        if "VDP_DS_SSDP" in summary or "VDP DS SSDP" in summary:
            vdp_ds_ssdp_candidate_keys.append(key)
        elif "VDP_DS_MON" in summary or "VDP DS MON" in summary:
           vdp_ds_mon_candidate_keys.append(key)
        else:
            # Base VDP_DS (without SSDP or MON suffix)
            vdp_ds_candidate_keys.append(key)
    
    # Check VDP_STORE_2 first before VDP_STORE to avoid duplication
    if issuetype in enabler_issue_types and (summary.startswith("VDP_STORE_2") or summary.startswith("VDP STORE 2") or summary.startswith("VDP STORE_2")):
        vdp_store_2_candidate_keys.append(key)
    elif issuetype in enabler_issue_types and (summary.startswith("VDP_STORE") or summary.startswith("VDP STORE")):
        vdp_store_candidate_keys.append(key)

print(f"Discovered {len(records)} APIM/EAH candidates", file=sys.stderr)
print(f"Discovered {len(docg_candidate_keys)} DOCG candidates", file=sys.stderr)
print(f"Discovered {len(vdr_candidate_keys)} VDR candidates", file=sys.stderr)
print(f"Discovered {len(patric_candidate_keys)} PATRIC-SSDP candidates", file=sys.stderr)
print(f"Discovered {len(rcz_candidate_keys)} RCZ candidates", file=sys.stderr)
print(f"Discovered {len(synapse_candidate_keys)} SYNAPSE candidates", file=sys.stderr)
print(f"Discovered {len(reftel_candidate_keys)} REFTEL candidates", file=sys.stderr)
print(f"Discovered {len(calva_candidate_keys)} CALVA candidates", file=sys.stderr)
print(f"Discovered {len(refser2_candidate_keys)} REFSER2 candidates", file=sys.stderr)
print(f"Discovered {len(sering_candidate_keys)} SERING candidates", file=sys.stderr)
print(f"Discovered {len(vdp_proc_candidate_keys)} VDP_PROC candidates", file=sys.stderr)
print(f"Discovered {len(vdp_ds_candidate_keys)} VDP_DS candidates", file=sys.stderr)
print(f"Discovered {len(vdp_ds_ssdp_candidate_keys)} VDP_DS_SSDP candidates", file=sys.stderr)
print(f"Discovered {len(vdp_ds_mon_candidate_keys)} VDP_DS_MON candidates", file=sys.stderr)
print(f"Discovered {len(vdp_store_candidate_keys)} VDP_STORE candidates", file=sys.stderr)
print(f"Discovered {len(vdp_store_2_candidate_keys)} VDP_STORE_2 candidates", file=sys.stderr)

# -----------------------
# APIM/EAH selection (history-based + status-based)
# -----------------------
apim_eah_enablers = []  # list of dicts

print("Processing APIM/EAH issues...", file=sys.stderr)
for r in records:
    key = r["key"]
    sysname = r["system"]
    version = r["version"]

    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")

    # Skip tickets with "Deploying to PPROD" status
    if status_name == "Deploying to PPROD":
        continue

    # Get deploy date based on status
    prod_date_str = None
    
    if status_name == "In production":
        prod_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        prod_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        prod_date_str = get_awaiting_go_nogo_date_from_history(full)
    else:
        # For other statuses, try to find any relevant transition
        prod_date_str = get_prod_date_from_history(full)
        if not prod_date_str:
            prod_date_str = get_awaiting_go_nogo_date_from_history(full)
        if not prod_date_str:
            prod_date_str = get_deploying_to_prod_date_from_history(full)
    
    prod_date = parse_iso_date(prod_date_str)
    if not prod_date:
        # no applicable transition found — ignore
        continue

    iso_year, iso_week, _ = prod_date.isocalendar()
    if iso_year == target_year and iso_week == target_week:
        apim_eah_enablers.append({
            "key": key,
            "system": sysname,
            "version": version,
            "summary": r["summary"],
            "status": status_name or r.get("status", ""),
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
    if iso_week != target_week or iso_year != target_year:
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
    if iso_week != target_week or iso_year != target_year:
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
# PATRIC-SSDP selection
# -----------------------
patric_enablers = []
for key in patric_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    enabler_name = "PATRIC-SSDP"

    enabler_version = (f.get("customfield_10042") or "").strip()
    if not enabler_version:
        m = PATRIC_RE.search(summary_full)
        if m:
            enabler_version = m.group(1)

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        deploy_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
    
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
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
    if iso_week != target_week or iso_year != target_year:
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
# SYNAPSE selection
# -----------------------
synapse_enablers = []
for key in synapse_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    if not summary_full.startswith("SYNAPSE"):
        continue

    enabler_name = "SYNAPSE"

    enabler_version = (f.get("customfield_10042") or "").strip()

    # Use appropriate date extraction based on status
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        deploy_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
    else:
        deploy_date_str = None
        
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    synapse_enablers.append({
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
# REFTEL selection
# -----------------------
reftel_enablers = []
for key in reftel_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    if not summary_full.startswith("REFTEL"):
        continue

    enabler_name = "REFTEL"

    # Try customfield_10042 first, then extract from summary
    enabler_version = (f.get("customfield_10042") or "").strip()
    if not enabler_version and "-" in summary_full:
        # Extract version from summary like "REFTEL-3.5.1"
        parts = summary_full.split("-", 1)
        if len(parts) > 1:
            enabler_version = parts[1].strip()

    # Use appropriate date extraction based on status
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        deploy_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
    else:
        deploy_date_str = None
        
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    reftel_enablers.append({
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
# CALVA selection
# -----------------------
calva_enablers = []
for key in calva_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    if not summary_full.startswith("CALVA"):
        continue

    enabler_name = "CALVA"

    # Use the full summary as the version (e.g., "CALVA-2.4.2")
    enabler_version = summary_full

    # Use appropriate date extraction based on status
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        deploy_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
    else:
        deploy_date_str = None
        
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    calva_enablers.append({
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
# REFSER2 selection
# -----------------------
refser2_enablers = []
for key in refser2_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    if not summary_full.startswith("REFSER2"):
        continue

    enabler_name = "REFSER2"

    # Use the full summary as the version (e.g., "REFSER2-1.5.1")
    enabler_version = summary_full

    # Use appropriate date extraction based on status
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        deploy_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
    else:
        deploy_date_str = None
        
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    refser2_enablers.append({
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
# SERING selection
# -----------------------
sering_enablers = []
vdp_proc_enablers = []
for key in sering_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in (
        "In production",
        "Deploying to PROD",
        "Awaiting Go / No go PROD"
    ):
        continue

    summary_full = (f.get("summary") or "").strip()
    if not summary_full.startswith("SERING"):
        continue

    enabler_name = "SERING"

    # Try customfield_10042 first, then extract from summary
    enabler_version = (f.get("customfield_10042") or "").strip()
    if not enabler_version:
        m = SERING_RE.search(summary_full)
        if m:
            enabler_version = m.group(1)
    
    # If still no version, use the full summary as version
    if not enabler_version:
        enabler_version = summary_full

    # Use appropriate date extraction based on status
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Deploying to PROD":
        deploy_date_str = get_deploying_to_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
    else:
        deploy_date_str = None
        
    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    sering_enablers.append({
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
# VDP_PROC selection
# -----------------------
for key in vdp_proc_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    # VDP_PROC supports In production and other deployment statuses
    if status_name not in ("In production", "Deploying to PROD", "Preproduction") and not is_awaiting_go_nogo_status(status_name):
        print(f"  VDP_PROC {key}: Filtered - status {status_name} not in allowed list", file=sys.stderr)
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDP_PROC"
    enabler_version = (f.get("customfield_10042") or "").strip()

    if not enabler_version:
        print(f"  VDP_PROC {key}: Filtered - no version found", file=sys.stderr)
        continue

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif is_awaiting_go_nogo_status(status_name):
        # For "Awaiting Go / No go PROD", prioritize when it entered that status
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
        # If not in history, try custom fields
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
    elif status_name in ("Preproduction", "Deploying to PROD"):
        deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
        if not deploy_date_str:
            deploy_date_str = get_deploying_to_prod_date_from_history(full)

    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        print(f"  VDP_PROC {key}: Filtered - could not parse date '{deploy_date_str}'", file=sys.stderr)
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        print(f"  VDP_PROC {key}: Filtered - date {deploy_date_str} is week {iso_week}/{iso_year}, target is {target_week}/{target_year}", file=sys.stderr)
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")
    summary_full = (f.get("summary") or "").strip()
    
    print(f"  VDP_PROC {key}: INCLUDED - {enabler_version} ({status_name}) - {deploy_date_str} (week {iso_week}/{iso_year})", file=sys.stderr)

    vdp_proc_enablers.append({
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
# VDP_DS selection (base component)
# -----------------------
vdp_ds_enablers = []
for key in vdp_ds_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in ("In production", "Deploying to PROD", "Preproduction") and not is_awaiting_go_nogo_status(status_name):
        continue
    if "VDP_DS_SSDP" in summary_full or "VDP DS SSDP" in summary_full:
        continue
    if "VDP_DS_MON" in summary_full or "VDP DS MON" in summary_full:
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDP_DS"
    enabler_version = (f.get("customfield_10042") or "").strip()

    if not enabler_version:
        continue

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        # For "Awaiting Go / No go PROD", prioritize when it entered that status
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
        # If not in history, try custom fields
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
    else:
        deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
        if not deploy_date_str:
            deploy_date_str = get_deploying_to_prod_date_from_history(full)

    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    vdp_ds_enablers.append({
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
# VDP_DS_SSDP selection  
# -----------------------
vdp_ds_ssdp_enablers = []
for key in vdp_ds_ssdp_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD", "Preproduction"):
        continue

    summary_full = (f.get("summary") or "").strip()
    
    # Verify this is VDP_DS_SSDP variant
    if not ("VDP_DS_SSDP" in summary_full or "VDP DS SSDP" in summary_full):
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDP_DS"
    enabler_version = (f.get("customfield_10042") or "").strip()

    if not enabler_version:
        continue

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif is_awaiting_go_nogo_status(status_name):
        # For "Awaiting Go / No go PROD", prioritize when it entered that status
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
        # If not in history, try custom fields
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
    else:
        deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
        if not deploy_date_str:
            deploy_date_str = get_deploying_to_prod_date_from_history(full)

    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    vdp_ds_ssdp_enablers.append({
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
# VDP_DS_MON selection
# -----------------------
vdp_ds_mon_enablers = []
for key in vdp_ds_mon_candidate_keys:
    full = jira_get_issue_full(key)
    if not full:
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD", "Preproduction"):
        continue

    summary_full = (f.get("summary") or "").strip()
    
    # Verify this is VDP_DS_MON variant
    if not ("VDP_DS_MON" in summary_full or "VDP DS MON" in summary_full):
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDP_DS"
    enabler_version = (f.get("customfield_10042") or "").strip()

    if not enabler_version:
        continue

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif status_name == "Awaiting Go / No go PROD":
        # For "Awaiting Go / No go PROD", prioritize when it entered that status
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
        # If not in history, try custom fields
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
    else:
        deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
        if not deploy_date_str:
            deploy_date_str = get_deploying_to_prod_date_from_history(full)

    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    vdp_ds_mon_enablers.append({
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
# VDP_STORE selection
# -----------------------
vdp_store_enablers = []
vdp_store_debug_count = 0
vdp_store_filtered_count = 0
for key in vdp_store_candidate_keys:
    vdp_store_debug_count += 1
    full = jira_get_issue_full(key)
    if not full:
        vdp_store_filtered_count += 1
        print(f"  VDP_STORE {key}: no full data", file=sys.stderr)
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in ("In production", "Deploying to PROD", "Preproduction") and not is_awaiting_go_nogo_status(status_name):
        vdp_store_filtered_count += 1
        print(f"  VDP_STORE {key}: status '{status_name}' not allowed", file=sys.stderr)
        continue

    summary_full = (f.get("summary") or "").strip()
    
    # Verify this is base VDP_STORE (not VDP_STORE_2 variant)
    if "VDP_STORE_2" in summary_full or "VDP STORE 2" in summary_full or "VDP STORE_2" in summary_full:
        vdp_store_filtered_count += 1
        print(f"  VDP_STORE {key}: excluded (is VDP_STORE_2 variant)", file=sys.stderr)
        continue

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDP_STORE"
    enabler_version = (f.get("customfield_10042") or "").strip()

    if not enabler_version:
        vdp_store_filtered_count += 1
        print(f"  VDP_STORE {key}: no enabler_version", file=sys.stderr)
        continue

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif is_awaiting_go_nogo_status(status_name):
        # For "Awaiting Go / No go PROD", prioritize when it entered that status
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
        # If not in history, try custom fields
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
    else:
        deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
        if not deploy_date_str:
            deploy_date_str = get_deploying_to_prod_date_from_history(full)

    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        vdp_store_filtered_count += 1
        print(f"  VDP_STORE {key}: no deploy date (raw: '{deploy_date_str}')", file=sys.stderr)
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        vdp_store_filtered_count += 1
        print(f"  VDP_STORE {key}: week mismatch {iso_year}-W{iso_week} != {target_year}-W{target_week}", file=sys.stderr)
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    vdp_store_enablers.append({
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
    print(f"  VDP_STORE {key}: SELECTED {enabler_version} ({status_name})", file=sys.stderr)

print(f"VDP_STORE: {len(vdp_store_enablers)} selected from {vdp_store_debug_count} candidates, {vdp_store_filtered_count} filtered", file=sys.stderr)

# -----------------------
# VDP_STORE_2 selection
# -----------------------
vdp_store_2_enablers = []
vdp_store_2_debug_count = 0
vdp_store_2_filtered_count = 0
for key in vdp_store_2_candidate_keys:
    vdp_store_2_debug_count += 1
    full = jira_get_issue_full(key)
    if not full:
        vdp_store_2_filtered_count += 1
        continue

    f = full.get("fields", {}) or {}
    status_name = (f.get("status") or {}).get("name", "")
    if status_name not in ("In production", "Deploying to PROD", "Preproduction") and not is_awaiting_go_nogo_status(status_name):
        continue

    summary_full = (f.get("summary") or "").strip()

    enabler_name = (f.get("customfield_10041") or {}).get("value", "") or "VDP_STORE_2"
    enabler_version = (f.get("customfield_10042") or "").strip()

    if not enabler_version:
        continue

    # Get deploy date based on status
    deploy_date_str = None
    if status_name == "In production":
        deploy_date_str = get_prod_date_from_history(full)
    elif is_awaiting_go_nogo_status(status_name):
        # For "Awaiting Go / No go PROD", prioritize when it entered that status
        deploy_date_str = get_awaiting_go_nogo_date_from_history(full)
        # If not in history, try custom fields
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
    else:
        deploy_date_str = full.get("fields", {}).get("customfield_10044")
        if not deploy_date_str:
            deploy_date_str = full.get("fields", {}).get("customfield_10043")
        if not deploy_date_str:
            deploy_date_str = get_deploying_to_prod_date_from_history(full)

    deploy_date = parse_iso_date(deploy_date_str)
    if not deploy_date:
        continue

    iso_year, iso_week, _ = deploy_date.isocalendar()
    if iso_week != target_week or iso_year != target_year:
        continue

    assignee = (f.get("assignee") or {}).get("displayName", "")
    issuetype_name = (f.get("issuetype") or {}).get("name", "")

    vdp_store_2_enablers.append({
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

print(f"VDP_STORE_2: {len(vdp_store_2_enablers)} selected from {vdp_store_2_debug_count} candidates, {vdp_store_2_filtered_count} filtered", file=sys.stderr)

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
    
    out_lines.append(f"======= {header_name} ({rcz.get('key')}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {rcz.get('key')}")
    out_lines.append(f"Summary: {rcz.get('summary')}")
    out_lines.append(f"Status: {rcz.get('status')}")
    if rcz.get("assignee"):
        out_lines.append(f"Owner: {rcz.get('assignee')}")
    if rcz.get("issuetype"):
        out_lines.append(f"Issue Type: {rcz.get('issuetype')}")
    if rcz.get("deploy_date"):
        out_lines.append(f"Deploy Date: {rcz.get('deploy_date')}")
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

# SYNAPSE blocks
for synapse in sorted(synapse_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = synapse.get("enabler_version") or ""
    header_name = f"SYNAPSE-{ver}" if ver else "SYNAPSE"

    out_lines.append(f"======= {header_name} ({synapse['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {synapse['key']}")
    out_lines.append(f"Summary: {synapse['summary']}")
    out_lines.append(f"Status: {synapse['status']}")
    if synapse.get("assignee"):
        out_lines.append(f"Owner: {synapse['assignee']}")
    if synapse.get("issuetype"):
        out_lines.append(f"Issue Type: {synapse['issuetype']}")
    if synapse.get("deploy_date"):
        out_lines.append(f"Deploy Date: {synapse['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(synapse["full"])
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

# REFTEL blocks
for reftel in sorted(reftel_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = reftel.get("enabler_version") or ""
    header_name = f"REFTEL-{ver}" if ver else "REFTEL"

    out_lines.append(f"======= {header_name} ({reftel['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {reftel['key']}")
    out_lines.append(f"Summary: {reftel['summary']}")
    out_lines.append(f"Status: {reftel['status']}")
    if reftel.get("assignee"):
        out_lines.append(f"Owner: {reftel['assignee']}")
    if reftel.get("issuetype"):
        out_lines.append(f"Issue Type: {reftel['issuetype']}")
    if reftel.get("deploy_date"):
        out_lines.append(f"Deploy Date: {reftel['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(reftel["full"])
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

# CALVA blocks
for calva in sorted(calva_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = calva.get("enabler_version") or ""
    # ver already contains "CALVA-X.Y.Z" so use it directly
    header_name = ver if ver else "CALVA"

    out_lines.append(f"======= {header_name} ({calva['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {calva['key']}")
    out_lines.append(f"Summary: {calva['summary']}")
    out_lines.append(f"Status: {calva['status']}")
    if calva.get("assignee"):
        out_lines.append(f"Owner: {calva['assignee']}")
    if calva.get("issuetype"):
        out_lines.append(f"Issue Type: {calva['issuetype']}")
    if calva.get("deploy_date"):
        out_lines.append(f"Deploy Date: {calva['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(calva["full"])
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

# REFSER2 blocks
for refser2 in sorted(refser2_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = refser2.get("enabler_version") or ""
    # ver already contains "REFSER2-X.Y.Z" so use it directly
    header_name = ver if ver else "REFSER2"

    out_lines.append(f"======= {header_name} ({refser2['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {refser2['key']}")
    out_lines.append(f"Summary: {refser2['summary']}")
    out_lines.append(f"Status: {refser2['status']}")
    if refser2.get("assignee"):
        out_lines.append(f"Owner: {refser2['assignee']}")
    if refser2.get("issuetype"):
        out_lines.append(f"Issue Type: {refser2['issuetype']}")
    if refser2.get("deploy_date"):
        out_lines.append(f"Deploy Date: {refser2['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(refser2["full"])
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

# SERING blocks
for sering in sorted(sering_enablers, key=lambda d: d.get("enabler_version") or ""):
    ver = sering.get("enabler_version") or ""
    # Use the version directly, or create proper format
    if ver.startswith("SERING"):
        header_name = ver
    else:
        header_name = f"SERING-{ver}" if ver else "SERING"

    out_lines.append(f"======= {header_name} ({sering['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {sering['key']}")
    out_lines.append(f"Summary: {sering['summary']}")
    out_lines.append(f"Status: {sering['status']}")
    if sering.get("assignee"):
        out_lines.append(f"Owner: {sering['assignee']}")
    if sering.get("issuetype"):
        out_lines.append(f"Issue Type: {sering['issuetype']}")
    if sering.get("deploy_date"):
        out_lines.append(f"Deploy Date: {sering['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(sering["full"])
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

# VDP_PROC blocks
for vdp_proc in sorted(vdp_proc_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = vdp_proc.get("enabler_name") or "VDP_PROC"
    ver = vdp_proc.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdp_proc['key']}) =======\n")
    out_lines.append("")
    out_lines.append(f"Issue: {vdp_proc['key']}")
    out_lines.append(f"Summary: {vdp_proc['summary']}")
    out_lines.append(f"Status: {vdp_proc['status']}")
    if vdp_proc.get("assignee"):
        out_lines.append(f"Owner: {vdp_proc['assignee']}")
    if vdp_proc.get("issuetype"):
        out_lines.append(f"Issue Type: {vdp_proc['issuetype']}")
    if vdp_proc.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdp_proc['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdp_proc["full"])
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

# VDP_DS blocks (base component)
for vdp_ds in sorted(vdp_ds_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = "VDP_DS"  # Display as VDP_DS
    ver = vdp_ds.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdp_ds['key']}) =======\n")
    out_lines.append("")
    out_lines.append(f"Issue: {vdp_ds['key']}")
    out_lines.append(f"Summary: {vdp_ds['summary']}")
    out_lines.append(f"Status: {vdp_ds['status']}")
    if vdp_ds.get("assignee"):
        out_lines.append(f"Owner: {vdp_ds['assignee']}")
    if vdp_ds.get("issuetype"):
        out_lines.append(f"Issue Type: {vdp_ds['issuetype']}")
    if vdp_ds.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdp_ds['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdp_ds["full"])
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

# VDP_DS_SSDP blocks
for vdp_ds_ssdp in sorted(vdp_ds_ssdp_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = "VDP_DS_SSDP"  # Display as VDP_DS_SSDP
    ver = vdp_ds_ssdp.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdp_ds_ssdp['key']}) =======\n")
    out_lines.append("")
    out_lines.append(f"Issue: {vdp_ds_ssdp['key']}")
    out_lines.append(f"Summary: {vdp_ds_ssdp['summary']}")
    out_lines.append(f"Status: {vdp_ds_ssdp['status']}")
    if vdp_ds_ssdp.get("assignee"):
        out_lines.append(f"Owner: {vdp_ds_ssdp['assignee']}")
    if vdp_ds_ssdp.get("issuetype"):
        out_lines.append(f"Issue Type: {vdp_ds_ssdp['issuetype']}")
    if vdp_ds_ssdp.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdp_ds_ssdp['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdp_ds_ssdp["full"])
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

# VDP_DS_MON blocks
for vdp_ds_mon in sorted(vdp_ds_mon_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = "VDP_DS_MON"  # Display as VDP_DS_MON
    ver = vdp_ds_mon.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdp_ds_mon['key']}) =======\n")
    out_lines.append("")
    out_lines.append(f"Issue: {vdp_ds_mon['key']}")
    out_lines.append(f"Summary: {vdp_ds_mon['summary']}")
    out_lines.append(f"Status: {vdp_ds_mon['status']}")
    if vdp_ds_mon.get("assignee"):
        out_lines.append(f"Owner: {vdp_ds_mon['assignee']}")
    if vdp_ds_mon.get("issuetype"):
        out_lines.append(f"Issue Type: {vdp_ds_mon['issuetype']}")
    if vdp_ds_mon.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdp_ds_mon['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdp_ds_mon["full"])
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

# VDP_STORE blocks
for vdp_store in sorted(vdp_store_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = "VDP_STORE"
    ver = vdp_store.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdp_store['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {vdp_store['key']}")
    out_lines.append(f"Summary: {vdp_store['summary']}")
    out_lines.append(f"Status: {vdp_store['status']}")
    if vdp_store.get("assignee"):
        out_lines.append(f"Owner: {vdp_store['assignee']}")
    if vdp_store.get("issuetype"):
        out_lines.append(f"Issue Type: {vdp_store['issuetype']}")
    if vdp_store.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdp_store['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdp_store["full"])
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

# VDP_STORE_2 blocks
for vdp_store_2 in sorted(vdp_store_2_enablers, key=lambda d: d.get("enabler_version") or ""):
    name = "VDP_STORE_2"
    ver = vdp_store_2.get("enabler_version") or ""
    header_name = f"{name}-{ver}" if ver else name

    out_lines.append(f"======= {header_name} ({vdp_store_2['key']}) =======")
    out_lines.append("")
    out_lines.append(f"Issue: {vdp_store_2['key']}")
    out_lines.append(f"Summary: {vdp_store_2['summary']}")
    out_lines.append(f"Status: {vdp_store_2['status']}")
    if vdp_store_2.get("assignee"):
        out_lines.append(f"Owner: {vdp_store_2['assignee']}")
    if vdp_store_2.get("issuetype"):
        out_lines.append(f"Issue Type: {vdp_store_2['issuetype']}")
    if vdp_store_2.get("deploy_date"):
        out_lines.append(f"Deploy Date: {vdp_store_2['deploy_date']}")
    out_lines.append("")

    linked = extract_linked_issues_from_issue_json(vdp_store_2["full"])
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
    "RCZ": None,
    "SYNAPSE": None,
    "REFTEL": None,
    "CALVA": None,
    "REFSER2": None,
    "SERING": None,
    "VDP_PROC": None,
    "VDP_DS": None,
    "VDP_DS_SSDP": None,
    "VDP_DS_MON": None,
    "VDP_STORE": None,
    "VDP_STORE_2": None
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
# RCZ → Include all valid deployment statuses
# -----------------------
rcz_versions = [
    r["enabler_version"]
    for r in rcz_enablers
    if r.get("enabler_version")
       and r.get("status") in ("In production", "Deploying to PPROD", "Awaiting Go / No go PROD")
]

if rcz_versions:
    # Format with "RCZ " prefix for consistency
    formatted_versions = []
    for v in sorted(set(rcz_versions), key=vtuple):
        if not v.startswith("RCZ "):
            formatted_versions.append(f"RCZ {v}")
        else:
            formatted_versions.append(v)
    store_entry["RCZ"] = ",".join(formatted_versions)

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
# SYNAPSE → Include all valid deployment statuses
# -----------------------
synapse_versions = [
    s["enabler_version"]
    for s in synapse_enablers
    if s.get("status") in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD")
       and s.get("enabler_version")
]
if synapse_versions:
    store_entry["SYNAPSE"] = ",".join(sorted(set(synapse_versions), key=vtuple))

# -----------------------
# REFTEL → Include all valid deployment statuses
# -----------------------
reftel_versions = [
    r["enabler_version"]
    for r in reftel_enablers
    if r.get("status") in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD")
       and r.get("enabler_version")
]
if reftel_versions:
    store_entry["REFTEL"] = ",".join(sorted(set(reftel_versions), key=vtuple))

# -----------------------
# CALVA → Include all valid deployment statuses
# -----------------------
calva_versions = [
    c["enabler_version"]
    for c in calva_enablers
    if c.get("status") in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD")
       and c.get("enabler_version")
]
if calva_versions:
    store_entry["CALVA"] = ",".join(sorted(set(calva_versions), key=vtuple))

# -----------------------
# REFSER2 → Include all valid deployment statuses
# -----------------------
refser2_versions = [
    r["enabler_version"]
    for r in refser2_enablers
    if r.get("status") in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD")
       and r.get("enabler_version")
]
if refser2_versions:
    store_entry["REFSER2"] = ",".join(sorted(set(refser2_versions), key=vtuple))

# -----------------------
# SERING → Include all valid deployment statuses
# -----------------------
sering_versions = [
    s["enabler_version"]
    for s in sering_enablers
    if s.get("status") in ("In production", "Deploying to PROD", "Awaiting Go / No go PROD")
       and s.get("enabler_version")
]
if sering_versions:
    store_entry["SERING"] = ",".join(sorted(set(sering_versions), key=vtuple))

# -----------------------
# -----------------------
# VDP_PROC → In production or Deploying to PROD ONLY
# -----------------------
vdp_proc_versions = [
    v["enabler_version"]
    for v in vdp_proc_enablers
    if v.get("status") in ("In production", "Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdp_proc_versions:
    store_entry["VDP_PROC"] = ",".join(sorted(set(vdp_proc_versions), key=vtuple))

# -----------------------
# VDP_DS → In production or Deploying to PROD ONLY
# -----------------------
vdp_ds_versions = [
    v["enabler_version"]
    for v in vdp_ds_enablers
    if v.get("status") in ("In production", "Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdp_ds_versions:
    store_entry["VDP_DS"] = ",".join(sorted(set(vdp_ds_versions), key=vtuple))

# -----------------------
# VDP_DS_SSDP → In production or Deploying to PROD ONLY
# -----------------------
vdp_ds_ssdp_versions = [
    v["enabler_version"]
    for v in vdp_ds_ssdp_enablers
    if v.get("status") in ("In production", "Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdp_ds_ssdp_versions:
    store_entry["VDP_DS_SSDP"] = ",".join(sorted(set(vdp_ds_ssdp_versions), key=vtuple))

# -----------------------
# VDP_DS_MON → In production or Deploying to PROD ONLY
# -----------------------
vdp_ds_mon_versions = [
    v["enabler_version"]
    for v in vdp_ds_mon_enablers
    if v.get("status") in ("In production", "Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdp_ds_mon_versions:
    store_entry["VDP_DS_MON"] = ",".join(sorted(set(vdp_ds_mon_versions), key=vtuple))

# -----------------------
# VDP_STORE → In production or Deploying to PROD ONLY
# -----------------------
vdp_store_versions = [
    v["enabler_version"]
    for v in vdp_store_enablers
    if v.get("status") in ("In production", "Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdp_store_versions:
    store_entry["VDP_STORE"] = ",".join(sorted(set(vdp_store_versions), key=vtuple))

# -----------------------
# VDP_STORE_2 → In production or Deploying to PROD ONLY
# -----------------------
vdp_store_2_versions = [
    v["enabler_version"]
    for v in vdp_store_2_enablers
    if v.get("status") in ("In production", "Deploying to PROD", "Deploying To PROD")
       and v.get("enabler_version")
]
if vdp_store_2_versions:
    store_entry["VDP_STORE_2"] = ",".join(sorted(set(vdp_store_2_versions), key=vtuple))


# -----------------------
# Persist stopper
# -----------------------
existing = stopper.get(week_str)

def data_has_changed(existing_data, new_data):
    """Check if new data is different from existing data"""
    if existing_data is None:
        return True
    
    # Compare each component - if any component has new releases, update
    for component, new_version in new_data.items():
        existing_version = existing_data.get(component)
        if new_version != existing_version:
            # If new version is not None/empty and different, it's a change
            if new_version and new_version != "None":
                return True
            # If existing had data and now it's None/empty, it's also a change
            if existing_version and existing_version != "None" and not new_version:
                return True
    return False

should_update = force_overwrite

# Check if we should update based on data changes
if not should_update:
    if existing is not None:
        should_update = data_has_changed(existing, store_entry)
        if should_update:
            print(f"Week {week_str} has new releases - updating existing entry")
        else:
            print(f"Week {week_str} already exists with same data: {existing}")
    else:
        should_update = True  # No existing data, so create new entry

if should_update:
    # Save only in ISO format
    stopper[week_str] = store_entry
    save_stopper(stopper)
    print(f"Week {week_str} snapshot written: {store_entry}")
else:
    print("Use --force to overwrite unchanged data.")

# -----------------------
# Summary output for CLI / debugging
# -----------------------
total_selected = len(apim_eah_enablers) + len(docg_enablers) + len(vdr_enablers) + len(patric_enablers) + len(rcz_enablers) + len(synapse_enablers) + len(reftel_enablers) + len(calva_enablers) + len(refser2_enablers) + len(sering_enablers) + len(vdp_proc_enablers) + len(vdp_ds_enablers) + len(vdp_ds_ssdp_enablers) + len(vdp_ds_mon_enablers)
print("Done extract.py")
print(json.dumps({
    "week": week_str,
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
    "rcz_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in rcz_enablers
    ],
    "synapse_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in synapse_enablers
    ],
    "reftel_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in reftel_enablers
    ],
    "calva_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in calva_enablers
    ],
    "refser2_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in refser2_enablers
    ],
    "sering_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in sering_enablers
    ],
    "vdp_proc_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in vdp_proc_enablers
    ],
    "vdp_ds_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in vdp_ds_enablers
    ],
    "vdp_ds_ssdp_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in vdp_ds_ssdp_enablers
    ],
    "vdp_ds_mon_selected": [
        {"key": d["key"], "enabler_version": d.get("enabler_version"), "status": d.get("status"), "deploy_date": d.get("deploy_date")}
        for d in vdp_ds_mon_enablers
    ],
    "counts": {
        "APIM/EAH": len(apim_eah_enablers),
        "DOCG": len(docg_enablers),
        "VDR": len(vdr_enablers),
        "PATRIC-SSDP": len(patric_enablers),
        "RCZ": len(rcz_enablers),
        "SYNAPSE": len(synapse_enablers),
        "REFTEL": len(reftel_enablers),
        "CALVA": len(calva_enablers),
        "REFSER2": len(refser2_enablers),
        "SERING": len(sering_enablers),
        "VDP_PROC": len(vdp_proc_enablers),
        "VDP_DS": len(vdp_ds_enablers),
        "VDP_DS_SSDP": len(vdp_ds_ssdp_enablers),
        "VDP_DS_MON": len(vdp_ds_mon_enablers)
    },
    "linked_file": os.path.abspath(LINKED_FILE),
    "stopper_file": os.path.abspath(WEEKLY_STOPPER)
}, indent=2))

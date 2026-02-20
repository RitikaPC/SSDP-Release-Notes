#!/usr/bin/env python3
"""
summarize.py — version-accurate release summary with APIM, EAH, DOCG, VDR, PATRIC-SSDP, and VDP_PROC

Enhancements:
• Fully supports DOCG & VDR extracted via extract.py
• Supports PATRIC-SSDP extracted via extract.py
• Separate tables per version: APIM, EAH, DOCG, VDR, PATRIC-SSDP, VDP_PROC
• Release Summary table now shows last NON-NULL version from historical stopper
• Linked issues table includes all systems
• Classification:
      User Story → FEATURES
      Technical Story → CODE
      Bug/Bug Enabler → BUGS
"""

import os
import sys
import re
import json
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

LINKED_FILE = os.getenv("LINKED_FILE", "Linked_Issues_Report.txt")
SUMMARY_HTML = os.getenv("SUMMARY_HTML", "summary_output.html")
STOPPER_FILE = os.getenv("WEEKLY_STOPPER", "weekly_stopper.json")
WEEK_FILE = os.getenv("WEEK_FILE", "week_number.txt")
META_FILE = os.getenv("META_FILE", "summary_meta.json")

forced_week_raw = None
forced_year = None
forced_week = None
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
    fy, fw = parse_week_arg(forced_week_raw)
    forced_year = fy
    forced_week = fw


def vtuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v))
    except Exception:
        return ()


def extract_latest_version(version_string):
    """Extract the latest version from a comma-separated version string"""
    if not version_string or version_string == "None":
        return "None"
    
    # Handle comma-separated versions
    if "," in version_string:
        versions = [v.strip() for v in version_string.split(",")]
        # Sort by version tuple to get the latest
        try:
            latest = max(versions, key=vtuple)
            return latest
        except Exception:
            # If version parsing fails, return the last one
            return versions[-1]
    
    return version_string.strip()


def load_text(path):
    return open(path, "r", encoding="utf-8").read() if os.path.exists(path) else ""


def write(path, txt):
    open(path, "w", encoding="utf-8").write(txt)


def read_week():
    """Return (year, week, display_str)

    display_str will always be like '2026-W04' to maintain consistency.
    """
    today = datetime.date.today()
    current_year = today.isocalendar()[0]
    current_week = today.isocalendar()[1]

    year = forced_year if forced_year is not None else current_year
    week = forced_week if forced_week is not None else current_week

    # validate week
    last_day = datetime.date(year, 12, 28)
    maxw = last_day.isocalendar()[1]
    if week < 1 or week > maxw:
        print(f"Invalid week {week} for year {year}")
        sys.exit(1)

    # Always use year-week format for consistency
    display = f"{year}-W{week:02d}"
    return year, week, display


def parse_blocks(raw):
    header_re = re.compile(r"^=+\s*(.*?)\s*\(([^)]+)\)\s*=+\s*$", re.MULTILINE)
    matches = list(header_re.finditer(raw))

    known_systems = [
        "PATRIC-SSDP",
        "VDP_DS_SSDP",
        "VDP_DS_MON",
        "VDP_STORE_2",
        "VDP_STORE",
        "VDP_PROC",
        "VDP_DS",
        "SYNAPSE",
        "REFTEL",
        "REFSER2",
        "SERING",
        "CALVA",
        "APIM",
        "EAH",
        "DOCG",
        "VDR",
        "RCZ",
    ]

    def split_header(header_text: str):
        header_clean = (header_text or "").strip()
        upper = header_clean.upper()

        for system in known_systems:
            if upper.startswith(system.upper()):
                remainder = header_clean[len(system):].strip()
                remainder = re.sub(r"^[-\s]+", "", remainder).strip()
                version = remainder.rstrip(".") if remainder else ""
                return system, version

        return "", ""

    def infer_version_from_body(system: str, fallback_version: str, body: str):
        if fallback_version and fallback_version != "None":
            return fallback_version
        m = re.search(r"^Summary:\s*(.+)$", body, re.MULTILINE)
        if not m:
            return fallback_version or ""
        summary = m.group(1).strip().rstrip(".")
        if not summary:
            return fallback_version or ""

        if system == "DOCG":
            return summary
        if summary.upper().startswith(system.upper()):
            return summary[len(system):].lstrip("- ").strip().rstrip(".")
        return summary

    blocks = []
    for i, m in enumerate(matches):
        header_text = (m.group(1) or "").strip()
        key = (m.group(2) or "").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()

        system, version = split_header(header_text)
        if not system:
            continue

        version = infer_version_from_body(system, version, body)

        blocks.append({
            "system": system,
            "version": version,
            "enabler_key": key,
            "body": body
        })

    return blocks

def extract_issues(body):
    issues = []
    parts = body.split("Issue:")

    for p in parts[1:]:
        summary = ""
        issue_type = ""
        status = ""
        owner = ""
        created = ""
        deploy_date = ""

        m = re.search(r"Summary:\s*(.*)", p)
        if m:
            summary = m.group(1).strip()

        m = re.search(r"Issue Type:\s*(.*)", p)
        if m:
            issue_type = m.group(1).strip()

        m = re.search(r"Status:\s*(.*)", p)
        if m:
            status = m.group(1).strip()

        m = re.search(r"(Owner|Assignee):\s*(.*)", p)
        if m:
            owner = m.group(2).strip()

        m = re.search(r"Created:\s*(.*)", p)
        if m:
            created = m.group(1).strip()

        m = re.search(r"Deploy Date:\s*(.*)", p)
        if m:
            deploy_date = m.group(1).strip()

        issues.append({
            "summary": summary,
            "issue_type": issue_type,
            "status": status,
            "owner": owner,
            "created": created,
            "deploy_date": deploy_date
        })

    return issues


def classify(issue_type):
    it = issue_type.lower()
    if "user story" in it:
        return "FEATURES"
    if "technical story" in it or "tech" in it:
        return "CODE"
    if "bug" in it:
        return "BUGS"
    return "FEATURES"


def build_changes(blocks):
    out = {"APIM": {}, "EAH": {}, "DOCG": {}, "VDR": {}, "PATRIC-SSDP": {}, "RCZ":{}, "SYNAPSE": {}, "REFTEL": {}, "CALVA": {}, "REFSER2": {}, "SERING": {}, "VDP_PROC": {}, "VDP_DS": {}, "VDP_DS_SSDP": {}, "VDP_DS_MON": {}, "VDP_STORE": {}, "VDP_STORE_2": {}}

    for b in blocks:
        sysname = b["system"]
        ver = b["version"]
        issues = extract_issues(b["body"])

        out[sysname].setdefault(
            ver,
            {"FEATURES": [], "CODE": [], "BUGS": [], "DEPLOY": "", "STATUS": ""}
        )

        for iss in issues:
            if sysname in ("DOCG", "VDR", "PATRIC-SSDP", "RCZ", "SYNAPSE", "REFTEL", "CALVA", "REFSER2", "SERING", "VDP_PROC", "VDP_DS", "VDP_DS_SSDP", "VDP_DS_MON", "VDP_STORE", "VDP_STORE_2") and iss["deploy_date"]:
                out[sysname][ver]["DEPLOY"] = iss["deploy_date"]

        for iss in issues:
            if not out[sysname][ver]["STATUS"] and iss["status"]:
                out[sysname][ver]["STATUS"] = iss["status"]

            bucket = classify(iss["issue_type"])
            if iss["summary"]:
                out[sysname][ver][bucket].append(iss["summary"])

    return out


def make_box(title, color, items):
    if not items:
        return ""

    list_html = "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"

    return (
        '<ac:structured-macro ac:name="panel">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>'
        f'<ac:parameter ac:name="bgColor">{color}</ac:parameter>'
        f'<ac:rich-text-body>{list_html}</ac:rich-text-body>'
        '</ac:structured-macro>'
    )


def make_table(ver, boxhtml, status="", extra=""):
    return f"""
    <table style="width:100%;border-collapse:collapse;margin:12px 0;border:1px solid #ccc;">
      <tr><th style="background:#F4F5F7;width:200px;padding:10px;border:1px solid #ccc;">Version</th><td style="padding:10px;border:1px solid #ccc;">{ver}</td></tr>
      <tr><th style="background:#F4F5F7;padding:10px;border:1px solid #ccc;">Status</th><td style="padding:10px;border:1px solid #ccc;">{status}</td></tr>
      <tr><th style="background:#F4F5F7;padding:10px;border:1px solid #ccc;">Dependencies</th><td style="padding:10px;border:1px solid #ccc;"></td></tr>
      <tr><th style="background:#F4F5F7;padding:10px;border:1px solid #ccc;">INDUS configuration</th><td style="padding:10px;border:1px solid #ccc;"></td></tr>
      <tr><th style="background:#F4F5F7;padding:10px;border:1px solid #ccc;">Swagger Release</th><td style="padding:10px;border:1px solid #ccc;">{extra}</td></tr>
      <tr><th style="background:#F4F5F7;padding:10px;border:1px solid #ccc;">Main Changes</th><td style="padding:10px;border:1px solid #ccc;">{boxhtml}</td></tr>
    </table>
    """


def build_linked_table(blocks):
    html = """
    <h2>Combined Linked Issues</h2>
    <table style="width:100%;border-collapse:collapse;border:1px solid #ccc;">
    <tr style="background:#eee;">
    <th style="padding:10px;border:1px solid #ccc;">System</th><th style="padding:10px;border:1px solid #ccc;">Version</th><th style="padding:10px;border:1px solid #ccc;">Key</th><th style="padding:10px;border:1px solid #ccc;">Summary</th><th style="padding:10px;border:1px solid #ccc;">Owner</th><th style="padding:10px;border:1px solid #ccc;">Status</th><th style="padding:10px;border:1px solid #ccc;">Issue Type</th>
    </tr>
    """

    for b in blocks:
        system = b["system"]
        version = b["version"]
        parts = b["body"].split("Issue:")

        for p in parts[1:]:
            m = re.match(r"\s*(\S+)", p)
            if not m:
                continue
            key = m.group(1)

            summary = re.search(r"Summary:\s*(.*)", p)
            status = re.search(r"Status:\s*(.*)", p)
            owner = re.search(r"(Owner|Assignee):\s*(.*)", p)
            itype = re.search(r"Issue Type:\s*(.*)", p)

            html += (
                "<tr>"
                f"<td style='padding:10px;border:1px solid #ccc;'>{system}</td>"
                f"<td style='padding:10px;border:1px solid #ccc;'>{version}</td>"
                f"<td style='padding:10px;border:1px solid #ccc;'><a target='_blank' href='https://stla-iotpf-jira.atlassian.net/browse/{key}'>{key}</a></td>"
                f"<td style='padding:10px;border:1px solid #ccc;'>{summary.group(1).strip() if summary else ''}</td>"
                f"<td style='padding:10px;border:1px solid #ccc;'>{owner.group(2).strip() if owner else ''}</td>"
                f"<td style='padding:10px;border:1px solid #ccc;'>{status.group(1).strip() if status else ''}</td>"
                f"<td style='padding:10px;border:1px solid #ccc;'>{itype.group(1).strip() if itype else ''}</td>"
                "</tr>"
            )

    html += "</table>"
    return html


raw = load_text(LINKED_FILE)
blocks = parse_blocks(raw)
pv = build_changes(blocks)

year, week, week_display = read_week()

stopper_data = json.load(open(STOPPER_FILE)) if os.path.exists(STOPPER_FILE) else {}

# Try both new format (2026-W04) and legacy format (4) for backward compatibility
week_str = week_display
legacy_week_str = str(week)

def get_stopper_value(component):
    """Get stopper value, trying both new and legacy week formats"""
    # Try new format first (e.g., "2026-W04")
    if week_str in stopper_data and stopper_data[week_str].get(component):
        return stopper_data[week_str].get(component)
    # Fall back to legacy format (e.g., "4")
    if legacy_week_str in stopper_data and stopper_data[legacy_week_str].get(component):
        return stopper_data[legacy_week_str].get(component)
    return None


# -------------------------------------------------------
# NEW FUNCTION: find last non-null version historically
# -------------------------------------------------------
def _parse_stopper_key(k: str):
    """Return (year, week, rawkey). Numeric keys without year return (None, int(key))."""
    m = re.match(r"^(?P<y>\d{4})-W?(?P<w>\d{1,2})$", k)
    if m:
        return int(m.group("y")), int(m.group("w")), k
    m2 = re.match(r"^(?P<w>\d{1,2})$", k)
    if m2:
        return None, int(m2.group("w")), k
    return None, None, k

def last_non_null(stopper, target_year, target_week, key):
    """Return the last non-null version for `key` before (target_year, target_week).

    Searches stopper keys parsed as either 'YYYY-Www' or numeric weeks. Best-effort across mixed formats.
    """
    parsed = []
    for k in stopper.keys():
        y, w, raw = _parse_stopper_key(k)
        if w is None:
            continue
        # If year missing, infer year based on week number and target
        # High week numbers (>40) when target is early (<=10) likely mean previous year
        if y is None:
            if target_week <= 10 and w > 40:
                y_effective = target_year - 1
            else:
                y_effective = target_year
        else:
            y_effective = y
        parsed.append((y_effective, w, raw))

    # sort chronologically
    parsed.sort()

    # find entries strictly before target
    candidates = [(y, w, raw) for (y, w, raw) in parsed if (y, w) < (target_year, target_week)]
    for y, w, raw in reversed(candidates):
        val = stopper.get(raw, {}).get(key)
        if val not in (None, "", "None"):
            return val
    return None


def safe(v):
    """Return the latest version from a version string, or 'None' if empty"""
    if not v:
        return "None"
    return extract_latest_version(v)


# -------------------------------------------------------
# REPLACE ALL prev_* LOGIC WITH NON-NULL LOOKUP
# -------------------------------------------------------
prev_apim = safe(last_non_null(stopper_data, year, week, "APIM"))
prev_eah = safe(last_non_null(stopper_data, year, week, "EAH"))
prev_docg = safe(last_non_null(stopper_data, year, week, "DOCG"))
prev_vdr = safe(last_non_null(stopper_data, year, week, "VDR"))
prev_patric = safe(last_non_null(stopper_data, year, week, "PATRIC-SSDP"))
prev_rcz = safe(last_non_null(stopper_data, year, week, "RCZ"))
curr_rcz = safe(get_stopper_value("RCZ"))
prev_synapse = safe(last_non_null(stopper_data, year, week, "SYNAPSE"))
curr_synapse = safe(get_stopper_value("SYNAPSE"))
prev_reftel = safe(last_non_null(stopper_data, year, week, "REFTEL"))
curr_reftel = safe(get_stopper_value("REFTEL"))
prev_calva = safe(last_non_null(stopper_data, year, week, "CALVA"))
curr_calva = safe(get_stopper_value("CALVA"))
prev_refser2 = safe(last_non_null(stopper_data, year, week, "REFSER2"))
curr_refser2 = safe(get_stopper_value("REFSER2"))
prev_sering = safe(last_non_null(stopper_data, year, week, "SERING"))
curr_sering = safe(get_stopper_value("SERING"))
prev_vdp_proc = safe(last_non_null(stopper_data, year, week, "VDP_PROC"))
curr_vdp_proc = safe(get_stopper_value("VDP_PROC"))
prev_vdp_ds = safe(last_non_null(stopper_data, year, week, "VDP_DS"))
curr_vdp_ds = safe(get_stopper_value("VDP_DS"))
prev_vdp_ds_ssdp = safe(last_non_null(stopper_data, year, week, "VDP_DS_SSDP"))
curr_vdp_ds_ssdp = safe(get_stopper_value("VDP_DS_SSDP"))
prev_vdp_ds_mon = safe(last_non_null(stopper_data, year, week, "VDP_DS_MON"))
curr_vdp_ds_mon = safe(get_stopper_value("VDP_DS_MON"))
prev_vdp_store = safe(last_non_null(stopper_data, year, week, "VDP_STORE"))
curr_vdp_store = safe(get_stopper_value("VDP_STORE"))
prev_vdp_store_2 = safe(last_non_null(stopper_data, year, week, "VDP_STORE_2"))
curr_vdp_store_2 = safe(get_stopper_value("VDP_STORE_2"))
curr_apim = safe(get_stopper_value("APIM"))
curr_eah = safe(get_stopper_value("EAH"))
curr_docg = safe(get_stopper_value("DOCG"))
curr_vdr = safe(get_stopper_value("VDR"))
curr_patric = safe(get_stopper_value("PATRIC-SSDP"))


# -------------------------------------------------------
# EXPANDED RELEASE SUMMARY FOR ALL COMPONENTS
# -------------------------------------------------------

# Determine which components have releases this week
def get_highlight_style(component):
    """Returns bgcolor attribute for Confluence if component has releases"""
    # Check the current version for this component
    current_versions = {
        "APIM": curr_apim,
        "EAH": curr_eah,
        "DOCG": curr_docg,
        "VDR": curr_vdr,
        "PATRIC-SSDP": curr_patric,
        "RCZ": curr_rcz,
        "SYNAPSE": curr_synapse,
        "REFTEL": curr_reftel,
        "CALVA": curr_calva,
        "REFSER2": curr_refser2,
        "SERING": curr_sering,
        "VDP_PROC": curr_vdp_proc,
        "VDP_DS": curr_vdp_ds,
        "VDP_DS_SSDP": curr_vdp_ds_ssdp,
        "VDP_DS_MON": curr_vdp_ds_mon,
        "VDP_STORE": curr_vdp_store,
        "VDP_STORE_2": curr_vdp_store_2
    }
    
    current_version = current_versions.get(component, "None")
    if current_version and current_version != "None" and current_version.strip():
        # Component has releases - use bgcolor with hex color code (light green)
        return 'bgcolor="DFFCF0"'
    else:
        # No releases - no color
        return ''


def normalize_version_for_lookup(component, version):
    if version is None:
        return ""
    value = str(version).strip()
    if not value or value == "None":
        return ""

    component_prefixes = [
        f"{component}-",
        f"{component} ",
    ]

    if component == "DOCG":
        component_prefixes.append("DOCG-")
    if component == "RCZ":
        component_prefixes.append("RCZ ")

    upper_value = value.upper()
    for prefix in component_prefixes:
        if upper_value.startswith(prefix.upper()):
            return value[len(prefix):].strip()

    return value


def build_enabler_key_map(parsed_blocks):
    enabler_keys = {}
    for b in parsed_blocks:
        system = b.get("system")
        version = (b.get("version") or "").strip()
        key = (b.get("enabler_key") or "").strip()
        if not system or not version or not key:
            continue
        enabler_keys.setdefault(system, {})[version] = key
    return enabler_keys


enabler_key_map = build_enabler_key_map(blocks)

JIRA_BASE = os.getenv("JIRA_BASE", "https://stla-iotpf-jira.atlassian.net")
JIRA_USERNAME = os.getenv("JIRA_USERNAME")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
_jira_key_cache = {}


def find_enabler_key_in_jira(component, version):
    cache_key = (component, version)
    if cache_key in _jira_key_cache:
        return _jira_key_cache[cache_key]

    if not JIRA_USERNAME or not JIRA_API_TOKEN:
        _jira_key_cache[cache_key] = None
        return None

    version_txt = str(version or "").strip()
    exact_hyphen = f"{component}-{version_txt}".strip("-")
    exact_space = f"{component} {version_txt}".strip()
    jql_candidates = [
        (
            'project = IOTPF '
            'AND issuetype = "Enabler Version - IOT PF" '
            f'AND summary ~ "{exact_hyphen}" '
            'ORDER BY created DESC'
        ),
        (
            'project = IOTPF '
            'AND issuetype = "Enabler Version - IOT PF" '
            f'AND summary ~ "{exact_space}" '
            'ORDER BY created DESC'
        ),
        (
            'project = IOTPF '
            'AND issuetype = "Enabler Version - IOT PF" '
            f'AND summary ~ "{component}" '
            f'AND summary ~ "{version_txt}" '
            'ORDER BY created DESC'
        ),
    ]

    try:
        for jql in jql_candidates:
            resp = requests.get(
                f"{JIRA_BASE}/rest/api/3/search/jql",
                auth=(JIRA_USERNAME, JIRA_API_TOKEN),
                headers={"Accept": "application/json"},
                params={"jql": jql, "maxResults": 1, "fields": "key"},
                timeout=20,
            )
            if resp.status_code != 200:
                continue
            issues = (resp.json() or {}).get("issues", [])
            if issues:
                key = issues[0].get("key")
                _jira_key_cache[cache_key] = key
                return key
    except Exception:
        pass

    _jira_key_cache[cache_key] = None
    return None


def format_version_with_link(component, version):
    if version is None:
        return "None"

    display_version = str(version).strip() or "None"
    if display_version == "None":
        return display_version

    key_map = enabler_key_map.get(component, {})
    normalized = normalize_version_for_lookup(component, display_version)
    issue_key = key_map.get(normalized) or key_map.get(display_version)

    if not issue_key:
        issue_key = find_enabler_key_in_jira(component, display_version)
    if not issue_key:
        issue_key = find_enabler_key_in_jira(component, normalized)
    if not issue_key:
        return display_version

    return f"<a target='_blank' href='{JIRA_BASE}/browse/{issue_key}'>{display_version}</a>"


def summary_row(component, prev_version, curr_version):
    row_style = get_highlight_style(component)
    prev_html = format_version_with_link(component, prev_version)
    curr_html = format_version_with_link(component, curr_version)
    return (
        f"<tr><td {row_style} style=\"padding:10px;border:1px solid #ccc;\">{component}</td>"
        f"<td {row_style} style=\"padding:10px;border:1px solid #ccc;\">{prev_html}</td>"
        f"<td {row_style} style=\"padding:10px;border:1px solid #ccc;\">{curr_html}</td></tr>"
    )


def build_release_summary_rows():
    rows = [
        ("APIM", prev_apim, curr_apim),
        ("EAH", prev_eah, curr_eah),
        ("DOCG", prev_docg, curr_docg),
        ("VDR", prev_vdr, curr_vdr),
        ("PATRIC-SSDP", prev_patric, curr_patric),
        ("RCZ", prev_rcz, curr_rcz),
        ("SYNAPSE", prev_synapse, curr_synapse),
        ("REFTEL", prev_reftel, curr_reftel),
        ("CALVA", prev_calva, curr_calva),
        ("REFSER2", prev_refser2, curr_refser2),
        ("SERING", prev_sering, curr_sering),
        ("VDP_PROC", prev_vdp_proc, curr_vdp_proc),
        ("VDP_DS", prev_vdp_ds, curr_vdp_ds),
        ("VDP_DS_SSDP", prev_vdp_ds_ssdp, curr_vdp_ds_ssdp),
        ("VDP_DS_MON", prev_vdp_ds_mon, curr_vdp_ds_mon),
        ("VDP_STORE", prev_vdp_store, curr_vdp_store),
        ("VDP_STORE_2", prev_vdp_store_2, curr_vdp_store_2),
    ]
    return "\n".join(summary_row(component, prev_version, curr_version) for component, prev_version, curr_version in rows)

release_summary_html = f"""
<h2>Release Summary</h2>
<table style="width:100%;border-collapse:collapse;border:1px solid #ccc;">
<tr style="background:#0747A6;color:white;">
    <th style="padding:10px;text-align:left;border:1px solid #0747A6;">Enabler/Application</th>
    <th style="padding:10px;text-align:left;border:1px solid #0747A6;">Last Version</th>
    <th style="padding:10px;text-align:left;border:1px solid #0747A6;">New Version</th>
</tr>

{build_release_summary_rows()}
</table>
"""

# -------------------------------------------------------
# RELEASE NOTE SUMMARY SECTION
# -------------------------------------------------------
def count_components_with_releases():
    """Count how many components have new releases this week"""
    components_with_releases = 0
    all_current_versions = {
        "APIM": curr_apim,
        "EAH": curr_eah, 
        "DOCG": curr_docg,
        "VDR": curr_vdr,
        "PATRIC-SSDP": curr_patric,
        "RCZ": curr_rcz,
        "SYNAPSE": curr_synapse,
        "REFTEL": curr_reftel,
        "CALVA": curr_calva,
        "REFSER2": curr_refser2,
        "SERING": curr_sering,
        "VDP_PROC": curr_vdp_proc,
        "VDP_DS": curr_vdp_ds,
        "VDP_DS_SSDP": curr_vdp_ds_ssdp,
        "VDP_DS_MON": curr_vdp_ds_mon,
        "VDP_STORE": curr_vdp_store,
        "VDP_STORE_2": curr_vdp_store_2
    }
    
    for component, version in all_current_versions.items():
        if version and version != "None" and version.strip():
            components_with_releases += 1
    
    return components_with_releases

def generate_component_toc():
    """Generate table of contents for components that have releases"""
    component_links = []
    
    # Define component mapping for consistent anchor IDs
    component_anchors = {
        "APIM": "apim",
        "EAH": "eah", 
        "DOCG": "docg",
        "VDR": "vdr",
        "PATRIC-SSDP": "patric-ssdp",
        "RCZ": "rcz",
        "SYNAPSE": "synapse",
        "REFTEL": "reftel",
        "CALVA": "calva",
        "REFSER2": "refser2",
        "SERING": "sering",
        "VDP_PROC": "vdp-proc",
        "VDP_DS": "vdp-ds",
        "VDP_DS_SSDP": "vdp-ds-ssdp",
        "VDP_DS_MON": "vdp-ds-mon",
        "VDP_STORE": "vdp-store",
        "VDP_STORE_2": "vdp-store-2"
    }
    
    # Check each component for releases and add to TOC if it has releases
    for component in ["APIM", "EAH", "DOCG", "VDR", "PATRIC-SSDP", "RCZ", "SYNAPSE", "REFTEL", "CALVA", "REFSER2", "SERING", "VDP_PROC", "VDP_DS", "VDP_DS_SSDP", "VDP_DS_MON", "VDP_STORE", "VDP_STORE_2"]:
        if component in pv and pv[component]:
            # Use component name as anchor - Confluence will auto-generate for native headings
            anchor_id = component_anchors[component]
            component_links.append(f'<li><a href="#{anchor_id}" style="color:#0066CC;text-decoration:none;">{component}</a></li>')
    
    return '\n'.join(component_links)

# Check how many components have releases to determine layout
num_releases = count_components_with_releases()

if num_releases == 0:
    # No releases - show condensed version
    release_note_summary_html = f"""
<div style="background:#F8F9FA;border:1px solid #e0e0e0;border-radius:8px;padding:25px;margin-bottom:30px;">
    <h2 style="margin-top:0;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:10px;">Release Note Summary</h2>
    <p style="color:#666;margin-bottom:20px;">No component releases this week.</p>
    
    <div style="background:#E3F2FD;border:1px solid #2196F3;border-radius:4px;padding:15px;">
        <div style="display:flex;align-items:center;margin-bottom:10px;">
            <span style="background:#2196F3;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">ℹ</span>
            <strong>Product Team Notes:</strong>
        </div>
        <div style="margin-left:30px;">
            <p style="margin:0;color:#666;font-style:italic;">High-level business impact and change descriptions will be added by the Product Team after release deployment.</p>
        </div>
    </div>
</div>
"""
else:
    # One or more releases - show full detailed version
    release_note_summary_html = f"""
<div style="display:flex;gap:30px;margin-bottom:30px;align-items:flex-start;flex-wrap:nowrap;width:100%;">
    <div style="flex:2;min-width:400px;max-width:65%;">
        <h2 style="margin-top:0;">Release Note Summary</h2>
        <p style="color:#666;margin-bottom:20px;"><em>High-level description of the primary changes from a business perspective, updated by the Product Team.</em></p>
        
        <div style="background:#E8F5E8;border:1px solid #4CAF50;border-radius:4px;padding:15px;margin-bottom:15px;">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span style="background:#4CAF50;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">✓</span>
                <strong>What we added:</strong>
            </div>
            <div style="margin-left:30px;min-height:40px;padding:10px;background:#f9f9f9;border-radius:3px;border:1px dashed #ccc;">
                <em style="color:#999;">Content to be filled manually by Product Team</em>
            </div>
        </div>
        
        <div style="background:#E8F5E8;border:1px solid #4CAF50;border-radius:4px;padding:15px;margin-bottom:15px;">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span style="background:#4CAF50;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">✓</span>
                <strong>What we changed:</strong>
            </div>
            <div style="margin-left:30px;min-height:40px;padding:10px;background:#f9f9f9;border-radius:3px;border:1px dashed #ccc;">
                <em style="color:#999;">Content to be filled manually by Product Team</em>
            </div>
        </div>
        
        <div style="background:#F3E5F5;border:1px solid #9C27B0;border-radius:4px;padding:15px;margin-bottom:15px;">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span style="background:#9C27B0;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">🗑</span>
                <strong>What we Deprecated/ Removed:</strong>
            </div>
            <div style="margin-left:30px;">
                <p style="margin:0;color:#666;">No explicit deprecations or removals were executed in this release.</p>
            </div>
        </div>
        
        <div style="background:#E8F5E8;border:1px solid #4CAF50;border-radius:4px;padding:15px;margin-bottom:15px;">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span style="background:#4CAF50;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">✓</span>
                <strong>What we fixed:</strong>
            </div>
            <div style="margin-left:30px;min-height:40px;padding:10px;background:#f9f9f9;border-radius:3px;border:1px dashed #ccc;">
                <em style="color:#999;">Content to be filled manually by Product Team</em>
            </div>
        </div>
    </div>
    
    <div style="flex:1;min-width:300px;max-width:35%;background:#F8F9FA;padding:20px;border-radius:4px;height:fit-content;border:1px solid #e0e0e0;">
        <h3 style="margin-top:0;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:5px;">TABLE of Contents</h3>
        <ol style="line-height:1.8;margin-left:0;padding-left:20px;">
            <li><a href="#release-summary" style="color:#0066CC;text-decoration:none;">Release Summary</a></li>
            <li><a href="#release-note-summary" style="color:#0066CC;text-decoration:none;">Release Note Summary</a></li>
            <li><a href="#release-notes" style="color:#0066CC;text-decoration:none;">RELEASE NOTES</a>
                <ol style="margin-left:10px;">
                    <li><a href="#high-level-summary" style="color:#0066CC;text-decoration:none;">High level summary by application</a>
                        <ol style="margin-left:10px;">
                            {generate_component_toc()}
                        </ol>
                    </li>
                    <li><a href="#detailed-list" style="color:#0066CC;text-decoration:none;">Detailed List</a></li>
                </ol>
            </li>
        </ol>
    </div>
</div>
"""

# -------------------------------------------------------
# RELEASE NOTES INTRODUCTION SECTION
# -------------------------------------------------------
release_notes_intro_html = """
<h2>RELEASE NOTES</h2>
<p>To clarify the releases across the various enablers and components, we have divided the release notes into a high-level summary and a detailed list of user stories extracted from Jira.</p>

<h3>High level summary by application</h3>
"""

# -------------------------------------------------------
# SECTIONS: unchanged from your script
# -------------------------------------------------------
section_html = ""

# APIM
apim_html = ""
for ver in sorted(pv["APIM"].keys(), key=vtuple, reverse=True):
    d = pv["APIM"][ver]
    apim_html += make_table(
        f"APIM-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra="<a href='https://pages.github.psa-cloud.com/mph00/cloud-api-capabilities/#/changelog' target='_blank'>Swagger Changelog</a>"
    )
if apim_html.strip():
    section_html += "<h3 id='APIM'>APIM</h3>\n" + apim_html

# EAH
eah_html = ""
for ver in sorted(pv["EAH"].keys(), key=vtuple, reverse=True):
    d = pv["EAH"][ver]
    eah_html += make_table(
        f"EAH-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra="<a href='https://pages.github.psa-cloud.com/mph00/cloud-api-capabilities/#/changelog' target='_blank'>Swagger Changelog</a>"
    )
if eah_html.strip():
    section_html += "<h3 id='EAH'>EAH</h3>\n" + eah_html

# DOCG
docg_html = ""
for ver in sorted(pv["DOCG"].keys(), key=vtuple, reverse=True):
    d = pv["DOCG"][ver]
    extra = ""
    docg_html += make_table(
        f"DOCG-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )
if docg_html.strip():
    section_html += "<h3 id='DOCG'>DOCG</h3>\n" + docg_html

# VDR
vdr_html = ""
for ver in sorted(pv.get("VDR", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDR"][ver]
    extra = ""
    vdr_html += make_table(
        f"VDR-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )
if vdr_html.strip():
    section_html += "<h3 id='VDR'>VDR</h3>\n" + vdr_html

# PATRIC-SSDP
patric_html = ""
for ver in sorted(pv.get("PATRIC-SSDP", {}).keys(), key=vtuple, reverse=True):
    d = pv["PATRIC-SSDP"][ver]
    extra = ""
    patric_html += make_table(
        f"PATRIC-SSDP-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )
if patric_html.strip():
    section_html += "<h3 id='PATRIC-SSDP'>PATRIC-SSDP</h3>\n" + patric_html

# RCZ
rcz_html = ""
for ver in sorted(pv.get("RCZ", {}).keys(), key=vtuple, reverse=True):
    d = pv["RCZ"][ver]
    extra = ""
    rcz_html += make_table(
        f"RCZ-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if rcz_html.strip():
    section_html += "<h3 id='RCZ'>RCZ</h3>\n" + rcz_html

# SYNAPSE
synapse_html = ""
for ver in sorted(pv.get("SYNAPSE", {}).keys(), key=vtuple, reverse=True):
    d = pv["SYNAPSE"][ver]
    extra = ""
    synapse_html += make_table(
        f"SYNAPSE-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if synapse_html.strip():
    section_html += "<h3 id='SYNAPSE'>SYNAPSE</h3>\n" + synapse_html

# REFTEL
reftel_html = ""
for ver in sorted(pv.get("REFTEL", {}).keys(), key=vtuple, reverse=True):
    d = pv["REFTEL"][ver]
    extra = ""
    reftel_html += make_table(
        f"REFTEL-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if reftel_html.strip():
    section_html += "<h3 id='REFTEL'>REFTEL</h3>\n" + reftel_html

# CALVA
calva_html = ""
for ver in sorted(pv.get("CALVA", {}).keys(), key=vtuple, reverse=True):
    d = pv["CALVA"][ver]
    extra = ""
    calva_html += make_table(
        f"CALVA-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if calva_html.strip():
    section_html += "<h3 id='CALVA'>CALVA</h3>\n" + calva_html

# REFSER2
refser2_html = ""
for ver in sorted(pv.get("REFSER2", {}).keys(), key=vtuple, reverse=True):
    d = pv["REFSER2"][ver]
    extra = ""
    refser2_html += make_table(
        f"REFSER2-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if refser2_html.strip():
    section_html += "<h3 id='REFSER2'>REFSER2</h3>\n" + refser2_html

# SERING
sering_html = ""
for ver in sorted(pv.get("SERING", {}).keys(), key=vtuple, reverse=True):
    d = pv["SERING"][ver]
    extra = ""
    sering_html += make_table(
        f"SERING-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if sering_html.strip():
    section_html += "<h3 id='SERING'>SERING</h3>\n" + sering_html

# VDP_PROC
vdp_proc_html = ""
for ver in sorted(pv.get("VDP_PROC", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDP_PROC"][ver]
    extra = ""
    vdp_proc_html += make_table(
        f"VDP_PROC-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if vdp_proc_html.strip():
    section_html += "<h3 id='VDP_PROC'>VDP_PROC</h3>\n" + vdp_proc_html

# VDP_DS
vdp_ds_html = ""
for ver in sorted(pv.get("VDP_DS", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDP_DS"][ver]
    extra = ""
    vdp_ds_html += make_table(
        f"VDP_DS-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if vdp_ds_html.strip():
    section_html += "<h3 id='VDP_DS'>VDP_DS</h3>\n" + vdp_ds_html

# VDP_DS_SSDP
vdp_ds_ssdp_html = ""
for ver in sorted(pv.get("VDP_DS_SSDP", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDP_DS_SSDP"][ver]
    extra = ""
    vdp_ds_ssdp_html += make_table(
        f"VDP_DS_SSDP-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if vdp_ds_ssdp_html.strip():
    section_html += "<h3 id='VDP_DS_SSDP'>VDP_DS_SSDP</h3>\n" + vdp_ds_ssdp_html

# VDP_DS_MON
vdp_ds_mon_html = ""
for ver in sorted(pv.get("VDP_DS_MON", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDP_DS_MON"][ver]
    extra = ""
    vdp_ds_mon_html += make_table(
        f"VDP_DS_MON-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if vdp_ds_mon_html.strip():
    section_html += "<h3 id='VDP_DS_MON'>VDP_DS_MON</h3>\n" + vdp_ds_mon_html

# VDP_STORE
vdp_store_html = ""
for ver in sorted(pv.get("VDP_STORE", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDP_STORE"][ver]
    extra = ""
    vdp_store_html += make_table(
        f"VDP_STORE-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if vdp_store_html.strip():
    section_html += "<h3 id='VDP_STORE'>VDP_STORE</h3>\n" + vdp_store_html

# VDP_STORE_2
vdp_store_2_html = ""
for ver in sorted(pv.get("VDP_STORE_2", {}).keys(), key=vtuple, reverse=True):
    d = pv["VDP_STORE_2"][ver]
    extra = ""
    vdp_store_2_html += make_table(
        f"VDP_STORE_2-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )

if vdp_store_2_html.strip():
    section_html += "<h3 id='VDP_STORE_2'>VDP_STORE_2</h3>\n" + vdp_store_2_html


linked_html = build_linked_table(blocks)

html = f"""
<h1 style="color:#0747A6;">SSDP Release Notes Week {week_display}</h1>

{release_summary_html}

{release_note_summary_html}

{release_notes_intro_html}

{section_html}

{linked_html}
"""

write(SUMMARY_HTML, html)

# Check if there are actual releases in the parsed data (not just stopper file)
has_actual_releases = any(pv.get(comp, {}) for comp in pv.keys())

meta = {
    "week": week_display,
    "has_releases": has_actual_releases,
    "prev_versions": {
        "APIM": prev_apim,
        "EAH": prev_eah,
        "DOCG": prev_docg,
        "VDR": prev_vdr,
        "PATRIC-SSDP": prev_patric,
        "RCZ": prev_rcz,
        "SYNAPSE": prev_synapse,
        "REFTEL": prev_reftel,
        "CALVA": prev_calva,
        "REFSER2": prev_refser2,
        "SERING": prev_sering,
        "VDP_PROC": prev_vdp_proc,
        "VDP_DS": prev_vdp_ds,
        "VDP_DS_SSDP": prev_vdp_ds_ssdp,
        "VDP_DS_MON": prev_vdp_ds_mon,
        "VDP_STORE": prev_vdp_store,
        "VDP_STORE_2": prev_vdp_store_2,
    },
    "curr_versions": {
        "APIM": curr_apim,
        "EAH": curr_eah,
        "DOCG": curr_docg,
        "VDR": curr_vdr,
        "PATRIC-SSDP": curr_patric,
        "RCZ": curr_rcz,
        "SYNAPSE": curr_synapse,
        "REFTEL": curr_reftel,
        "CALVA": curr_calva,
        "REFSER2": curr_refser2,
        "SERING": curr_sering,
        "VDP_PROC": curr_vdp_proc,
        "VDP_DS": curr_vdp_ds,
        "VDP_DS_SSDP": curr_vdp_ds_ssdp,
        "VDP_DS_MON": curr_vdp_ds_mon,
        "VDP_STORE": curr_vdp_store,
        "VDP_STORE_2": curr_vdp_store_2,
    },
}

write(META_FILE, json.dumps(meta, indent=2))

with open(WEEK_FILE, "w", encoding="utf-8") as f:
    f.write(str(week_display))

print("summarize.py completed successfully with historical lookback for Release Summary.")

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
    pattern = re.compile(
        r"^=+\s*(APIM|EAH|VDR|PATRIC-SSDP|PATRIC|SYNAPSE|REFTEL|VDP_PROC|VDP_STORE_2)\s*[- ]\s*([A-Za-z0-9._-]+)\s*\((.*?)\)"
        r"|^=+\s*(DOCG)\s*-\s*DOCG-([^(]+)\s*\((.*?)\)"  # DOCG-DOCG-X.Y.Z - 1.7.3 (KEY)
        r"|^=+\s*(RCZ)\s*-\s*RCZ\s*([A-Za-z0-9._-]+)\s*\((.*?)\)"
        r"|^=+\s*(CALVA|REFSER2|SERING)\s*-\s*([A-Za-z0-9._-]+)\s*\((.*?)\)",
        re.MULTILINE
    )
    matches = list(pattern.finditer(raw))

    blocks = []
    for i, m in enumerate(matches):
        if m.group(1):  # normal systems (APIM, EAH, VDR, PATRIC, SYNAPSE, REFTEL)
            system = m.group(1)
            if system in ("PATRIC", "PATRIC-SSDP"):
                system = "PATRIC"
            version = m.group(2).rstrip(".")
            key = m.group(3)
        elif m.group(4):  # DOCG-DOCG-X.Y.Z format
            system = "DOCG"
            version = m.group(5).strip().rstrip(".")
            key = m.group(6)
        elif m.group(7):  # RCZ-RCZ X.Y.Z format
            system = "RCZ"
            version = m.group(8).rstrip(".")
            key = m.group(9)
        else:  # CALVA-X.Y.Z format
            system = m.group(10)
            version = m.group(11).rstrip(".")
            key = m.group(12)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()

        blocks.append({
            "system": system,
            "version": version,
            "release_key": key,
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
    out = {"APIM": {}, "EAH": {}, "DOCG": {}, "VDR": {}, "PATRIC": {}, "RCZ":{}, "SYNAPSE": {}, "REFTEL": {}, "CALVA": {}, "REFSER2": {}, "SERING": {}, "VDP_PROC": {}, "VDP_STORE_2": {}}

    for b in blocks:
        sysname = b["system"]
        ver = b["version"]
        issues = extract_issues(b["body"])

        out[sysname].setdefault(
            ver,
            {"FEATURES": [], "CODE": [], "BUGS": [], "DEPLOY": "", "STATUS": ""}
        )

        for iss in issues:
            if sysname in ("DOCG", "VDR", "PATRIC", "RCZ", "SYNAPSE", "REFTEL", "CALVA", "REFSER2", "SERING", "VDP_PROC", "VDP_STORE_2") and iss["deploy_date"]:
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
    <table style="width:100%;border-collapse:collapse;margin:12px 0;">
      <tr><th style="background:#F4F5F7;width:200px;">Version</th><td>{ver}</td></tr>
      <tr><th style="background:#F4F5F7;">Status</th><td>{status}</td></tr>
      <tr><th style="background:#F4F5F7;">Dependencies</th><td></td></tr>
      <tr><th style="background:#F4F5F7;">INDUS configuration</th><td></td></tr>
      <tr><th style="background:#F4F5F7;">Swagger Release</th><td>{extra}</td></tr>
      <tr><th style="background:#F4F5F7;">Main Changes</th><td>{boxhtml}</td></tr>
    </table>
    """


def build_linked_table(blocks):
    html = """
    <h1 id='combined-linked-issues'>Combined Linked Issues</h1>
    <table style="width:100%;border-collapse:collapse;border:1px solid #ccc;">
    <tr style="background:#eee;">
    <th>System</th><th>Version</th><th>Key</th><th>Summary</th><th>Owner</th><th>Status</th><th>Issue Type</th>
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
                f"<td>{system}</td>"
                f"<td>{version}</td>"
                f"<td><a target='_blank' href='https://stla-iotpf-jira.atlassian.net/browse/{key}'>{key}</a></td>"
                f"<td>{summary.group(1).strip() if summary else ''}</td>"
                f"<td>{owner.group(2).strip() if owner else ''}</td>"
                f"<td>{status.group(1).strip() if status else ''}</td>"
                f"<td>{itype.group(1).strip() if itype else ''}</td>"
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
prev_patric = safe(last_non_null(stopper_data, year, week, "PATRIC"))
prev_patric_ssdp = safe(last_non_null(stopper_data, year, week, "PATRIC-SSDP"))
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
prev_vdp_store_2 = safe(last_non_null(stopper_data, year, week, "VDP_STORE_2"))
curr_vdp_store_2 = safe(get_stopper_value("VDP_STORE_2"))
curr_apim = safe(get_stopper_value("APIM"))
curr_eah = safe(get_stopper_value("EAH"))
curr_docg = safe(get_stopper_value("DOCG"))
curr_vdr = safe(get_stopper_value("VDR"))
curr_patric_ssdp = safe(get_stopper_value("PATRIC-SSDP"))
curr_patric = safe(get_stopper_value("PATRIC") or get_stopper_value("PATRIC-SSDP"))

if curr_patric_ssdp == "None":
    curr_patric_ssdp = curr_patric
if prev_patric_ssdp == "None":
    prev_patric_ssdp = prev_patric

# Keep last version null for VDP_STORE_2 when unchanged
if prev_vdp_store_2 == curr_vdp_store_2 and curr_vdp_store_2 != "None":
    prev_vdp_store_2 = "None"

# Backward compatibility for previous PATRIC key name
if prev_patric == "None":
    prev_patric = prev_patric_ssdp

def latest_version_from_changes(system_name):
    versions = list(pv.get(system_name, {}).keys())
    if not versions:
        return "None"
    return sorted(versions, key=vtuple)[-1]

if curr_patric == "None":
    curr_patric = latest_version_from_changes("PATRIC")


# -------------------------------------------------------
# EXPANDED RELEASE SUMMARY FOR ALL COMPONENTS
# -------------------------------------------------------

# Determine which components have releases this week
def get_highlight_bg(component):
    """Returns a Confluence-safe cell background attribute for released components."""
    current_versions = {
        "APIM": curr_apim,
        "EAH": curr_eah,
        "DOCG": curr_docg,
        "VDR": curr_vdr,
        "PATRIC-SSDP": curr_patric_ssdp,
        "PATRIC": curr_patric,
        "RCZ": curr_rcz,
        "SYNAPSE": curr_synapse,
        "REFTEL": curr_reftel,
        "CALVA": curr_calva,
        "REFSER2": curr_refser2,
        "SERING": curr_sering,
        "VDP_PROC": curr_vdp_proc,
    }
    current_version = current_versions.get(component, "None")
    if current_version and current_version != "None" and current_version.strip():
        return 'bgcolor="#DFFCF0"'
    return ""


def normalize_version_for_lookup(component, version):
    if not version:
        return ""
    value = str(version).strip()
    value = re.sub(r"\s+", " ", value)
    if component == "DOCG":
        value = re.sub(r"^DOCG-", "", value, flags=re.IGNORECASE)
    if component == "RCZ":
        value = re.sub(r"^RCZ\s+", "", value, flags=re.IGNORECASE)
    return value.strip()


def build_release_key_lookup(blocks):
    lookup = {
        "APIM": {}, "EAH": {}, "DOCG": {}, "VDR": {}, "PATRIC": {},
        "RCZ": {}, "SYNAPSE": {}, "REFTEL": {}, "CALVA": {}, "REFSER2": {},
        "SERING": {}, "VDP_PROC": {}, "VDP_STORE_2": {}
    }
    for b in blocks:
        system = b.get("system")
        version = b.get("version")
        key = b.get("release_key")
        if not system or not version or not key:
            continue
        normalized = normalize_version_for_lookup(system, version)
        lookup.setdefault(system, {})[normalized] = key
    return lookup


release_key_lookup = build_release_key_lookup(blocks)


def format_version_with_link(component, version):
    if not version or version == "None":
        return "None"
    normalized = normalize_version_for_lookup(component, version)
    key = release_key_lookup.get(component, {}).get(normalized)
    if not key and component == "PATRIC-SSDP":
        key = release_key_lookup.get("PATRIC", {}).get(normalized)
    if key and str(key).upper().startswith("IOTPF-"):
        return f"<a target='_blank' href='https://stla-iotpf-jira.atlassian.net/browse/{key}'>{version}</a>"
    return version


def release_summary_row(component, prev_version, curr_version):
    cell_bg = get_highlight_bg(component)
    prev_html = format_version_with_link(component, prev_version)
    curr_html = format_version_with_link(component, curr_version)
    return (
        f"<tr>"
        f"<td {cell_bg} style=\"padding:10px;border:1px solid #ccc;\">{component}</td>"
        f"<td {cell_bg} style=\"padding:10px;border:1px solid #ccc;\">{prev_html}</td>"
        f"<td {cell_bg} style=\"padding:10px;border:1px solid #ccc;\">{curr_html}</td>"
        f"</tr>"
    )


release_summary_html = f"""
<hr style="border:none;border-top:1px solid #DFE1E6;margin:20px 0;">
<h1 id="release-version-summary-by-enabler">Release Version Summary by Enabler</h1>
<table style="width:100%;border-collapse:collapse;border:1px solid #ccc;">
<tr style="background:#0747A6;color:white;">
    <th style="padding:10px;text-align:left;border:1px solid #0747A6;">Enabler/Application</th>
    <th style="padding:10px;text-align:left;border:1px solid #0747A6;">Last Version</th>
    <th style="padding:10px;text-align:left;border:1px solid #0747A6;">New Version</th>
</tr>

{release_summary_row("APIM", prev_apim, curr_apim)}
{release_summary_row("EAH", prev_eah, curr_eah)}
{release_summary_row("DOCG", prev_docg, curr_docg)}
{release_summary_row("VDR", prev_vdr, curr_vdr)}
{release_summary_row("PATRIC-SSDP", prev_patric_ssdp, curr_patric_ssdp)}
{release_summary_row("PATRIC", prev_patric, curr_patric)}
{release_summary_row("RCZ", prev_rcz, curr_rcz)}
{release_summary_row("SYNAPSE", prev_synapse, curr_synapse)}
{release_summary_row("REFTEL", prev_reftel, curr_reftel)}
{release_summary_row("CALVA", prev_calva, curr_calva)}
{release_summary_row("REFSER2", prev_refser2, curr_refser2)}
{release_summary_row("SERING", prev_sering, curr_sering)}
{release_summary_row("VDP_PROC", prev_vdp_proc, curr_vdp_proc)}
{release_summary_row("VDP_STORE_2", prev_vdp_store_2, curr_vdp_store_2)}

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
        "PATRIC": curr_patric,
        "RCZ": curr_rcz,
        "SYNAPSE": curr_synapse,
        "REFTEL": curr_reftel,
        "CALVA": curr_calva,
        "REFSER2": curr_refser2,
        "SERING": curr_sering,
        "VDP_PROC": curr_vdp_proc,
        "VDP_STORE_2": curr_vdp_store_2
    }
    
    for component, version in all_current_versions.items():
        if version and version != "None" and version.strip():
            components_with_releases += 1
    
    return components_with_releases

def generate_component_toc():
    """Generate table of contents for components that have releases"""
    component_links = []
    
    # Check each component for releases and add to TOC if it has releases
    for component in ["APIM", "EAH", "DOCG", "VDR", "PATRIC", "RCZ", "SYNAPSE", "REFTEL", "CALVA", "REFSER2", "SERING", "VDP_PROC", "VDP_STORE_2"]:
        if component in pv and pv[component]:
            anchor_id = component
            component_links.append(f'<li><a href="#{anchor_id}" style="color:#0066CC;text-decoration:none;">{component}</a></li>')
    
    return '\n'.join(component_links)

# Check how many components have releases to determine layout
num_releases = count_components_with_releases()

if num_releases == 0:
    # No releases - show condensed version
    release_note_summary_html = f"""
<div style="background:#F8F9FA;border:1px solid #e0e0e0;border-radius:8px;padding:25px;margin-bottom:30px;">
    <h1 id="release-summary" style="margin-top:0;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:10px;">Release Summary</h1>
    <h2>➕ What we added:</h2>
    <p>&nbsp;</p>

    <h2>🔧 What we changed:</h2>
    <p>&nbsp;</p>

    <h2>🚮 What we Deprecated/ Removed:</h2>
    <p>&nbsp;</p>

    <h2>🔨 What we fixed:</h2>
    <p>&nbsp;</p>
</div>
"""
else:
    # One or more releases - show full detailed version
    release_note_summary_html = f"""
<div style="display:flex;gap:30px;margin-bottom:30px;align-items:flex-start;flex-wrap:nowrap;width:100%;">
    <div style="flex:2;min-width:400px;max-width:65%;">
        <h1 id="release-summary" style="margin-top:0;">Release Summary</h1>
        <h2>➕ What we added:</h2>
        <p>&nbsp;</p>

        <h2>🔧 What we changed:</h2>
        <p>&nbsp;</p>

        <h2>🚮 What we Deprecated/ Removed:</h2>
        <p>&nbsp;</p>

        <h2>🔨 What we fixed:</h2>
        <p>&nbsp;</p>
    </div>
    
    <div style="flex:1;min-width:300px;max-width:35%;background:#F8F9FA;padding:20px;border-radius:4px;height:fit-content;border:1px solid #e0e0e0;">
        <hr style="border:none;border-top:1px solid #DFE1E6;margin:0 0 16px 0;">
        <h1 style="margin-top:0;color:#0747A6;border-bottom:2px solid #0747A6;padding-bottom:5px;">TABLE of Contents</h1>
        <ol style="line-height:1.8;margin-left:0;padding-left:20px;">
            <li><a href="#release-summary" style="color:#0066CC;text-decoration:none;">Release Summary</a></li>
            <li><a href="#release-version-summary-by-enabler" style="color:#0066CC;text-decoration:none;">Release Version Summary by Enabler</a></li>
            <li><a href="#release-notes" style="color:#0066CC;text-decoration:none;">RELEASE NOTES</a>
                <ol style="margin-left:10px;">
                    <li><a href="#high-level-summary" style="color:#0066CC;text-decoration:none;">High level summary by application</a>
                        <ol style="margin-left:10px;">
                            {generate_component_toc()}
                        </ol>
                    </li>
                    <li><a href="#combined-linked-issues" style="color:#0066CC;text-decoration:none;">Combined Linked Issues</a></li>
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
<h1 id="release-notes">RELEASE NOTES</h1>
<p>To clarify the releases across the various enablers and components, we have divided the release notes into a high-level summary and a detailed list of user stories extracted from Jira.</p>

<h3>High level summary by application</h3>
"""

# -------------------------------------------------------
# SECTIONS: unchanged from your script
# -------------------------------------------------------
section_html = ""

# APIM
apim_html = ""
for ver in sorted(pv["APIM"].keys(), key=vtuple):
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
for ver in sorted(pv["EAH"].keys(), key=vtuple):
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
for ver in sorted(pv["DOCG"].keys(), key=vtuple):
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
for ver in sorted(pv.get("VDR", {}).keys(), key=vtuple):
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

# PATRIC
patric_html = ""
for ver in sorted(pv.get("PATRIC", {}).keys(), key=vtuple):
    d = pv["PATRIC"][ver]
    extra = ""
    patric_html += make_table(
        f"PATRIC-{ver}",
        make_box("Features", "#E3FCEF", d["FEATURES"])
        + make_box("Code Refactoring", "#DEEBFF", d["CODE"])
        + make_box("Bug Fixes", "#FFEBE6", d["BUGS"]),
        status=d["STATUS"],
        extra=extra
    )
if patric_html.strip():
    section_html += "<h3 id='PATRIC'>PATRIC</h3>\n" + patric_html

# RCZ
rcz_html = ""
for ver in sorted(pv.get("RCZ", {}).keys(), key=vtuple):
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
for ver in sorted(pv.get("SYNAPSE", {}).keys(), key=vtuple):
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
for ver in sorted(pv.get("REFTEL", {}).keys(), key=vtuple):
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
for ver in sorted(pv.get("CALVA", {}).keys(), key=vtuple):
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
for ver in sorted(pv.get("REFSER2", {}).keys(), key=vtuple):
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
for ver in sorted(pv.get("SERING", {}).keys(), key=vtuple):
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
for ver in sorted(pv.get("VDP_PROC", {}).keys(), key=vtuple):
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

# VDP_STORE_2
vdp_store_2_html = ""
for ver in sorted(pv.get("VDP_STORE_2", {}).keys(), key=vtuple):
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
{release_note_summary_html}

{release_summary_html}

{release_notes_intro_html}

{section_html}

{linked_html}
"""

write(SUMMARY_HTML, html)

meta = {
    "week": week_display,
    "has_releases": num_releases > 0,
    "prev_versions": {
        "APIM": prev_apim,
        "EAH": prev_eah,
        "DOCG": prev_docg,
        "VDR": prev_vdr,
        "PATRIC": prev_patric,
        "RCZ": prev_rcz,
        "SYNAPSE": prev_synapse,
        "REFTEL": prev_reftel,
        "CALVA": prev_calva,
        "REFSER2": prev_refser2,
        "SERING": prev_sering,
        "VDP_PROC": prev_vdp_proc,
        "VDP_STORE_2": prev_vdp_store_2,
    },
    "curr_versions": {
        "APIM": curr_apim,
        "EAH": curr_eah,
        "DOCG": curr_docg,
        "VDR": curr_vdr,
        "PATRIC": curr_patric,
        "RCZ": curr_rcz,
        "SYNAPSE": curr_synapse,
        "REFTEL": curr_reftel,
        "CALVA": curr_calva,
        "REFSER2": curr_refser2,
        "SERING": curr_sering,
        "VDP_PROC": curr_vdp_proc,
        "VDP_STORE_2": curr_vdp_store_2,
    },
}

write(META_FILE, json.dumps(meta, indent=2))

with open(WEEK_FILE, "w", encoding="utf-8") as f:
    f.write(str(week_display))

print("summarize.py completed successfully with historical lookback for Release Summary.")

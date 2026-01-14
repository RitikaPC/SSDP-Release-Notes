#!/usr/bin/env python3
"""
summarize.py — version-accurate release summary with APIM, EAH, DOCG, VDR and PATRIC-SSDP

Enhancements:
• Fully supports DOCG & VDR extracted via extract.py
• Supports PATRIC-SSDP extracted via extract.py
• Separate tables per version: APIM, EAH, DOCG, VDR, PATRIC-SSDP
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


def load_text(path):
    return open(path, "r", encoding="utf-8").read() if os.path.exists(path) else ""


def write(path, txt):
    open(path, "w", encoding="utf-8").write(txt)


def read_week():
    """Return (year, week, display_str)

    If a year was provided, display_str will be like '2025-W50'. If no year, display_str is '50'.
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

    display = f"{year}-W{week:02d}" if forced_year else str(week)
    return year, week, display


def parse_blocks(raw):
    pattern = re.compile(
        r"^=+\s*(APIM|EAH|DOCG|VDR|PATRIC-SSDP|SYNAPSE|REFTEL)\s*[- ]\s*([\d\.]+)\s*\((.*?)\)"
        r"|^=+\s*(RCZ)\s*-\s*RCZ\s*([\d\.]+)\s*\((.*?)\)"
        r"|^=+\s*(CALVA|REFSER2|SERING)\s*-\s*([\d\.]+)\s*\((.*?)\)",
        re.MULTILINE
    )
    matches = list(pattern.finditer(raw))

    blocks = []
    for i, m in enumerate(matches):
        if m.group(1):  # normal systems
            system = m.group(1)
            version = m.group(2).rstrip(".")
            key = m.group(3)
        elif m.group(4):  # RCZ-RCZ X.Y.Z format
            system = "RCZ"
            version = m.group(5).rstrip(".")
            key = m.group(6)
        else:  # CALVA-X.Y.Z format
            system = "CALVA"
            version = m.group(8).rstrip(".")
            key = m.group(9)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()

        blocks.append({
            "system": system,
            "version": version,
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
    out = {"APIM": {}, "EAH": {}, "DOCG": {}, "VDR": {}, "PATRIC-SSDP": {}, "RCZ":{}, "SYNAPSE": {}, "REFTEL": {}, "CALVA": {}, "REFSER2": {}, "SERING": {}}

    for b in blocks:
        sysname = b["system"]
        ver = b["version"]
        issues = extract_issues(b["body"])

        out[sysname].setdefault(
            ver,
            {"FEATURES": [], "CODE": [], "BUGS": [], "DEPLOY": "", "STATUS": ""}
        )

        for iss in issues:
            if sysname in ("DOCG", "VDR", "PATRIC-SSDP", "RCZ", "SYNAPSE", "REFTEL", "CALVA", "REFSER2", "SERING") and iss["deploy_date"]:
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
    <h2>Combined Linked Issues</h2>
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
week_str = week_display


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
    return v if v else "None"


# -------------------------------------------------------
# REPLACE ALL prev_* LOGIC WITH NON-NULL LOOKUP
# -------------------------------------------------------
prev_apim = safe(last_non_null(stopper_data, year, week, "APIM"))
prev_eah = safe(last_non_null(stopper_data, year, week, "EAH"))
prev_docg = safe(last_non_null(stopper_data, year, week, "DOCG"))
prev_vdr = safe(last_non_null(stopper_data, year, week, "VDR"))
prev_patric = safe(last_non_null(stopper_data, year, week, "PATRIC-SSDP"))
prev_rcz = safe(last_non_null(stopper_data, year, week, "RCZ"))
curr_rcz = safe(stopper_data.get(week_str, {}).get("RCZ"))
prev_synapse = safe(last_non_null(stopper_data, year, week, "SYNAPSE"))
curr_synapse = safe(stopper_data.get(week_str, {}).get("SYNAPSE"))
prev_reftel = safe(last_non_null(stopper_data, year, week, "REFTEL"))
curr_reftel = safe(stopper_data.get(week_str, {}).get("REFTEL"))
prev_calva = safe(last_non_null(stopper_data, year, week, "CALVA"))
curr_calva = safe(stopper_data.get(week_str, {}).get("CALVA"))
prev_refser2 = safe(last_non_null(stopper_data, year, week, "REFSER2"))
curr_refser2 = safe(stopper_data.get(week_str, {}).get("REFSER2"))
prev_sering = safe(last_non_null(stopper_data, year, week, "SERING"))
curr_sering = safe(stopper_data.get(week_str, {}).get("SERING"))
curr_apim = safe(stopper_data.get(week_str, {}).get("APIM"))
curr_eah = safe(stopper_data.get(week_str, {}).get("EAH"))
curr_docg = safe(stopper_data.get(week_str, {}).get("DOCG"))
curr_vdr = safe(stopper_data.get(week_str, {}).get("VDR"))
curr_patric = safe(stopper_data.get(week_str, {}).get("PATRIC-SSDP"))


# -------------------------------------------------------
# EXPANDED RELEASE SUMMARY FOR ALL COMPONENTS
# -------------------------------------------------------

# Determine which components have releases this week
def get_highlight_style(component):
    """Returns highlight style if component has releases, otherwise normal style"""
    if component in pv and pv[component]:
        # Component has releases - highlight with green background
        return 'style="background-color:#E3FCEF;font-weight:bold;"'
    else:
        # No releases - normal style
        return ''

release_summary_html = f"""
<h2>Release Summary</h2>
<table style="width:100%;border-collapse:collapse;">
<tr style="background:#0747A6;color:white;">
    <th>Enabler/Application</th>
    <th>Last Version</th>
    <th>New Version</th>
</tr>

<tr {get_highlight_style("APIM")}><td>APIM</td><td>{prev_apim}</td><td>{curr_apim}</td></tr>
<tr {get_highlight_style("EAH")}><td>EAH</td><td>{prev_eah}</td><td>{curr_eah}</td></tr>
<tr {get_highlight_style("DOCG")}><td>DOCG</td><td>{prev_docg}</td><td>{curr_docg}</td></tr>
<tr {get_highlight_style("VDR")}><td>VDR</td><td>{prev_vdr}</td><td>{curr_vdr}</td></tr>
<tr {get_highlight_style("PATRIC-SSDP")}><td>PATRIC-SSDP</td><td>{prev_patric}</td><td>{curr_patric}</td></tr>
<tr {get_highlight_style("RCZ")}><td>RCZ</td><td>{prev_rcz}</td><td>{curr_rcz}</td></tr>
<tr {get_highlight_style("SYNAPSE")}><td>SYNAPSE</td><td>{prev_synapse}</td><td>{curr_synapse}</td></tr>
<tr {get_highlight_style("REFTEL")}><td>REFTEL</td><td>{prev_reftel}</td><td>{curr_reftel}</td></tr>
<tr {get_highlight_style("CALVA")}><td>CALVA</td><td>{prev_calva}</td><td>{curr_calva}</td></tr>
<tr {get_highlight_style("REFSER2")}><td>REFSER2</td><td>{prev_refser2}</td><td>{curr_refser2}</td></tr>
<tr {get_highlight_style("SERING")}><td>SERING</td><td>{prev_sering}</td><td>{curr_sering}</td></tr>

</table>
"""

# -------------------------------------------------------
# RELEASE NOTE SUMMARY SECTION
# -------------------------------------------------------
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
        "SERING": "sering"
    }
    
    # Check each component for releases and add to TOC if it has releases
    for component in ["APIM", "EAH", "DOCG", "VDR", "PATRIC-SSDP", "RCZ", "SYNAPSE", "REFTEL", "CALVA", "REFSER2", "SERING"]:
        if component in pv and pv[component]:
            anchor_id = component_anchors[component]
            component_links.append(f'<li><a href="#{anchor_id}" style="color:#0066CC;text-decoration:none;">{component}</a></li>')
    
    return '\n'.join(component_links)

release_note_summary_html = f"""
<div style="display:flex;gap:30px;margin-bottom:30px;">
    <div style="flex:2;">
        <h2>Release Note Summary</h2>
        <p style="color:#666;margin-bottom:20px;">High-level description of the primary changes from a business perspective, updated by the Product Team.</p>
        
        <div style="background:#E8F5E8;border:1px solid #4CAF50;border-radius:4px;padding:15px;margin-bottom:15px;">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span style="background:#4CAF50;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">✓</span>
                <strong>What we added:</strong>
            </div>
            <div style="margin-left:30px;">
                <!-- Content to be filled manually -->
            </div>
        </div>
        
        <div style="background:#E8F5E8;border:1px solid #4CAF50;border-radius:4px;padding:15px;margin-bottom:15px;">
            <div style="display:flex;align-items:center;margin-bottom:10px;">
                <span style="background:#4CAF50;color:white;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;margin-right:10px;font-size:12px;">✓</span>
                <strong>What we changed:</strong>
            </div>
            <div style="margin-left:30px;">
                <!-- Content to be filled manually -->
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
            <div style="margin-left:30px;">
                <!-- Content to be filled manually -->
            </div>
        </div>
    </div>
    
    <div style="flex:1;background:#F8F9FA;padding:20px;border-radius:4px;height:fit-content;">
        <h3 style="margin-top:0;">TABLE of Contents</h3>
        <ol style="line-height:1.8;">
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
    section_html += "<div id=\"apim\"><h2>APIM</h2>\n" + apim_html + "</div>"

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
    section_html += "<div id=\"eah\"><h2>EAH</h2>\n" + eah_html + "</div>"

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
    section_html += "<div id=\"docg\"><h2>DOCG</h2>\n" + docg_html + "</div>"

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
    section_html += "<div id=\"vdr\"><h2>VDR</h2>\n" + vdr_html + "</div>"

# PATRIC-SSDP
patric_html = ""
for ver in sorted(pv.get("PATRIC-SSDP", {}).keys(), key=vtuple):
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
    section_html += "<div id=\"patric-ssdp\"><h2>PATRIC-SSDP</h2>\n" + patric_html + "</div>"

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
    section_html += "<div id=\"rcz\"><h2>RCZ</h2>\n" + rcz_html + "</div>"

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
    section_html += "<div id=\"synapse\"><h2>SYNAPSE</h2>\n" + synapse_html + "</div>"

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
    section_html += "<div id=\"reftel\"><h2>REFTEL</h2>\n" + reftel_html + "</div>"

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
    section_html += "<div id=\"calva\"><h2>CALVA</h2>\n" + calva_html + "</div>"

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
    section_html += "<div id=\"refser2\"><h2>REFSER2</h2>\n" + refser2_html + "</div>"

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
    section_html += "<div id=\"sering\"><h2>SERING</h2>\n" + sering_html + "</div>"


linked_html = build_linked_table(blocks)

html = f"""
<h1 style="color:#0747A6;">SSDP Release Notes Week {week_display}</h1>

{release_summary_html}

{release_note_summary_html}

{section_html}

{linked_html}
"""

write(SUMMARY_HTML, html)

meta = {
    "week": week_display,
    "prev_versions": {
        "APIM": prev_apim,
        "EAH": prev_eah,
        "DOCG": prev_docg,
        "VDR": prev_vdr,
        "PATRIC-SSDP": prev_patric,
        "RCZ": prev_rcz,
    },
    "curr_versions": {
        "APIM": curr_apim,
        "EAH": curr_eah,
        "DOCG": curr_docg,
        "VDR": curr_vdr,
        "PATRIC-SSDP": curr_patric,
        "RCZ": curr_rcz,
    },
}

write(META_FILE, json.dumps(meta, indent=2))

with open(WEEK_FILE, "w", encoding="utf-8") as f:
    f.write(str(week_display))

print("summarize.py completed successfully with historical lookback for Release Summary.")

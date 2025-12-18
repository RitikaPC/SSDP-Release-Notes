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

LINKED_FILE = "Linked_Issues_Report.txt"
SUMMARY_HTML = "summary_output.html"
STOPPER_FILE = "weekly_stopper.json"
WEEK_FILE = "week_number.txt"
META_FILE = "summary_meta.json"

forced_week = None
if "--week" in sys.argv:
    try:
        forced_week = int(sys.argv[sys.argv.index("--week") + 1])
    except Exception:
        forced_week = None


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
    if forced_week is not None:
        return forced_week
    return datetime.date.today().isocalendar()[1]


def parse_blocks(raw):
    pattern = re.compile(
        r"^=+\s*(APIM|EAH|DOCG|VDR|PATRIC-SSDP)\s*[- ]\s*([\d\.]+)\s*\((.*?)\)"
        r"|^=+\s*(RCZ)\s*-\s*RCZ\s*([\d\.]+)\s*\((.*?)\)",
        re.MULTILINE
    )
    matches = list(pattern.finditer(raw))

    blocks = []
    for i, m in enumerate(matches):
        if m.group(1):  # normal systems
            system = m.group(1)
            version = m.group(2).rstrip(".")
            key = m.group(3)
        else:  # RCZ-RCZ X.Y.Z format
            system = "RCZ"
            version = m.group(5).rstrip(".")
            key = m.group(6)
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
    out = {"APIM": {}, "EAH": {}, "DOCG": {}, "VDR": {}, "PATRIC-SSDP": {}, "RCZ":{}}

    for b in blocks:
        sysname = b["system"]
        ver = b["version"]
        issues = extract_issues(b["body"])

        out[sysname].setdefault(
            ver,
            {"FEATURES": [], "CODE": [], "BUGS": [], "DEPLOY": "", "STATUS": ""}
        )

        for iss in issues:
            if sysname in ("DOCG", "VDR", "PATRIC-SSDP", "RCZ") and iss["deploy_date"]:
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

week = read_week()

stopper_data = json.load(open(STOPPER_FILE)) if os.path.exists(STOPPER_FILE) else {}
week_str = str(week)


# -------------------------------------------------------
# NEW FUNCTION: find last non-null version historically
# -------------------------------------------------------
def last_non_null(stopper, week, key):
    """Return the last non-null version for the given key before 'week'."""
    for w in range(week - 1, 0, -1):
        w_str = str(w)
        if w_str in stopper:
            val = stopper[w_str].get(key)
            if val not in (None, "", "None"):
                return val
    return None


def safe(v):
    return v if v else "None"


# -------------------------------------------------------
# REPLACE ALL prev_* LOGIC WITH NON-NULL LOOKUP
# -------------------------------------------------------
prev_apim = safe(last_non_null(stopper_data, week, "APIM"))
prev_eah = safe(last_non_null(stopper_data, week, "EAH"))
prev_docg = safe(last_non_null(stopper_data, week, "DOCG"))
prev_vdr = safe(last_non_null(stopper_data, week, "VDR"))
prev_patric = safe(last_non_null(stopper_data, week, "PATRIC-SSDP"))
prev_rcz = safe(last_non_null(stopper_data, week, "RCZ"))
curr_rcz = safe(stopper_data.get(week_str, {}).get("RCZ"))
curr_apim = safe(stopper_data.get(week_str, {}).get("APIM"))
curr_eah = safe(stopper_data.get(week_str, {}).get("EAH"))
curr_docg = safe(stopper_data.get(week_str, {}).get("DOCG"))
curr_vdr = safe(stopper_data.get(week_str, {}).get("VDR"))
curr_patric = safe(stopper_data.get(week_str, {}).get("PATRIC-SSDP"))


# -------------------------------------------------------
# EXPANDED RELEASE SUMMARY FOR ALL COMPONENTS
# -------------------------------------------------------
release_summary_html = f"""
<h2>Release Summary</h2>
<table style="width:100%;border-collapse:collapse;">
<tr style="background:#0747A6;color:white;">
    <th>Enabler/Application</th>
    <th>Last Version</th>
    <th>New Version</th>
</tr>

<tr><td>APIM</td><td>{prev_apim}</td><td>{curr_apim}</td></tr>
<tr><td>EAH</td><td>{prev_eah}</td><td>{curr_eah}</td></tr>
<tr><td>DOCG</td><td>{prev_docg}</td><td>{curr_docg}</td></tr>
<tr><td>VDR</td><td>{prev_vdr}</td><td>{curr_vdr}</td></tr>
<tr><td>PATRIC-SSDP</td><td>{prev_patric}</td><td>{curr_patric}</td></tr>
<tr><td>RCZ</td><td>{prev_rcz}</td><td>{curr_rcz}</td></tr>

</table>
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
    section_html += "<h2>APIM</h2>\n" + apim_html

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
    section_html += "<h2>EAH</h2>\n" + eah_html

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
    section_html += "<h2>DOCG</h2>\n" + docg_html

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
    section_html += "<h2>VDR</h2>\n" + vdr_html

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
    section_html += "<h2>PATRIC-SSDP</h2>\n" + patric_html

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
    section_html += "<h2>RCZ</h2>\n" + rcz_html


linked_html = build_linked_table(blocks)

html = f"""
<h1 style="color:#0747A6;">SSDP Release Notes Week {week}</h1>

{release_summary_html}

{section_html}

{linked_html}
"""

write(SUMMARY_HTML, html)

meta = {
    "week": week,
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
    f.write(str(week))

print("summarize.py completed successfully with historical lookback for Release Summary.")

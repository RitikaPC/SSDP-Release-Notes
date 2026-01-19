#!/usr/bin/env python3
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template_string, jsonify, request
import subprocess
import re
import os
import json

app = Flask(__name__)

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>SSDP Release Notes</title>

<style>
    :root {
        --bg: #f3f4f6;
        --panel: #ffffff;
        --primary: #2f5bea;
        --text: #111827;
        --muted: #6b7280;
        --border: #e5e7eb;
    }

    * { box-sizing: border-box; }

    body {
        margin: 0;
        height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: "Segoe UI", Inter, system-ui, sans-serif;
        background: linear-gradient(180deg, #f6f7f9, #eef0f3);
        color: var(--text);
    }

    .panel {
        width: 440px;
        background: var(--panel);
        border-radius: 14px;
        border: 1px solid var(--border);
        box-shadow: 0 25px 50px rgba(0,0,0,0.08);
        padding: 28px 32px 36px;
        text-align: center;
    }

    .logo { margin-bottom: 12px; }

    h1 {
        font-size: 21px;
        font-weight: 600;
        margin: 0 0 6px;
    }

    .subtitle {
        font-size: 13px;
        color: var(--muted);
        margin-bottom: 24px;
    }

    label {
        display: block;
        font-size: 13px;
        margin-bottom: 6px;
        color: var(--muted);
        text-align: left;
    }

    input {
        width: 100%;
        padding: 12px 14px;
        font-size: 14px;
        border-radius: 8px;
        border: 1px solid var(--border);
        outline: none;
        margin-bottom: 18px;
    }

    input:focus {
        border-color: var(--primary);
        box-shadow: 0 0 0 3px rgba(47,91,234,0.12);
    }

    button {
        width: 100%;
        padding: 12px;
        font-size: 14px;
        font-weight: 600;
        border-radius: 8px;
        border: none;
        background: var(--primary);
        color: #fff;
        cursor: pointer;
    }

    button:disabled {
        opacity: 0.6;
        cursor: default;
    }

    .spinner {
        width: 26px;
        height: 26px;
        margin: 26px auto 10px;
        border: 3px solid var(--border);
        border-top-color: var(--primary);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
        display: none;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }

    .status {
        font-size: 13px;
        color: var(--muted);
        display: none;
    }

    .footer {
        margin-top: 28px;
        font-size: 11px;
        color: var(--muted);
    }
</style>
</head>

<body>
<div class="panel">

    <div class="logo">
        <svg width="160" height="28" viewBox="0 0 160 28">
            <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle"
                  font-size="18" font-weight="600"
                  letter-spacing="4"
                  fill="#111827">
                STELLANTIS
            </text>
        </svg>
    </div>

    <h1>SSDP Release Notes</h1>
    <div class="subtitle">Generate weekly release documentation</div>

    <label for="week">Release Week</label>
    <input id="week" placeholder="e.g. 45" />

    <label for="year">Year (optional)</label>
    <input id="year" placeholder="e.g. 2026" />

    <button id="start">Generate & Open</button>

    <div class="spinner" id="spinner"></div>
    <div class="status" id="status">Redirecting to release notes…</div>
    <div class="status" id="gap-info" style="color: #2f5bea; font-weight: 500;"></div>

    <div class="footer">Internal Use Only · Stellantis SSDP</div>
</div>

<script>
    const start = document.getElementById("start");
    const spinner = document.getElementById("spinner");
    const status = document.getElementById("status");
    const gapInfo = document.getElementById("gap-info");
    const week = document.getElementById("week");
    const year = document.getElementById("year");

    start.onclick = async () => {
        start.disabled = true;
        spinner.style.display = "block";
        status.style.display = "block";
        gapInfo.style.display = "none";

        let url = "/run";
        const rawWeek = week.value.trim();
        const rawYear = year.value.trim();
        if (rawWeek) {
            // normalize week digits (allow W50 or 50)
            const weekDigits = rawWeek.replace(/^W/i, "").padStart(2, "0");
            if (rawYear) {
                url += "?week=" + encodeURIComponent(`${rawYear}-W${weekDigits}`);
            } else {
                url += "?week=" + encodeURIComponent(rawWeek);
            }
        }

        try {
            const resp = await fetch(url);
            const data = await resp.json();

            if (data.success) {
                // Handle "No releases this week" case
                if (!data.page_url && data.message) {
                    status.innerText = data.message;
                    status.style.color = "#f59e0b"; // Orange color for warning
                    return;
                }
                
                if (data.page_url) {
                    status.innerText = "Successfully published release notes";
                    if (data.filled_gaps > 0) {
                        gapInfo.innerText = `Published ${data.filled_gaps} missing week${data.filled_gaps > 1 ? 's' : ''} + target week`;
                        gapInfo.style.display = "block";
                        setTimeout(() => {
                            window.location.replace(data.page_url);
                        }, 2000);
                    } else {
                        window.location.replace(data.page_url);
                    }
                    return;
                }
            }

            status.innerText = "Release generated but no page URL returned";
        } catch (e) {
            status.innerText = "Error while generating release notes";
        } finally {
            spinner.style.display = "none";
            start.disabled = false;
        }
    };
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

def run_cmd(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr

@app.route("/run")
def run_workflow():
    week = request.args.get("week")
    if not week or not week.strip():
        week = None

    # Check for unpublished weeks with updates
    check_cmd = ["python3", "check_gaps.py"]
    if week:
        check_cmd += ["--week", week]
    
    code, out, err = run_cmd(check_cmd)
    unpublished_weeks = []
    
    if out:
        try:
            gap_result = json.loads(out)
            unpublished_weeks = gap_result.get("unpublished_weeks", [])
        except Exception:
            pass
    
    # Process unpublished weeks first, then target week
    weeks_to_process = unpublished_weeks + ([week] if week else [None])
    published_urls = []
    
    for process_week in weeks_to_process:
        extract_cmd = ["python3", "extract.py"]
        if process_week:
            extract_cmd += ["--week", process_week]

        code, out, err = run_cmd(extract_cmd)
        if code != 0:
            return jsonify({"success": False, "error": f"Extract failed for week {process_week}: {err or out}"})

        # Check if there are any releases found
        extract_output = out or ""
        total_releases = 0
        try:
            # Extract JSON from the output
            json_start = extract_output.rfind('{\n  "week"')
            if json_start != -1:
                json_str = extract_output[json_start:]
                extract_data = json.loads(json_str)
                counts = extract_data.get("counts", {})
                total_releases = sum(counts.values())
        except (json.JSONDecodeError, ValueError, AttributeError):
            # If we can't parse the JSON, assume there are releases to be safe
            total_releases = 1

        # If no releases found, write message and skip confluence page creation
        if total_releases == 0:
            week_display = process_week or "current week"
            print(f"No releases found for {week_display}")
            published_urls.append({
                "week": process_week or "current", 
                "url": None, 
                "message": "No releases this week"
            })
            continue

        summarize_cmd = ["python3", "summarize.py"]
        if process_week:
            summarize_cmd += ["--week", process_week]

        code, out, err = run_cmd(summarize_cmd)
        if code != 0:
            return jsonify({"success": False, "error": f"Summarize failed for week {process_week}: {err or out}"})

        publish_cmd = ["python3", "publish.py"]
        if process_week:
            publish_cmd += ["--week", process_week]
            
        code, out, err = run_cmd(publish_cmd)
        if code != 0:
            return jsonify({"success": False, "error": f"Publish failed for week {process_week}: {err or out}"})

        combined = (out or "") + "\n" + (err or "")
        for line in combined.splitlines():
            if line.startswith("CONFLUENCE_PAGE_URL="):
                page_url = line.split("=", 1)[1].strip()
                published_urls.append({"week": process_week or "current", "url": page_url})
                break

    # Return the last published URL (target week) as main URL
    # If no releases were found, return appropriate message
    if not published_urls:
        return jsonify({
            "success": True,
            "page_url": None,
            "message": "No releases this week",
            "all_published": [],
            "filled_gaps": 0
        })
    
    # Check if the last entry has no URL (no releases case)
    last_entry = published_urls[-1]
    if last_entry.get("url") is None:
        return jsonify({
            "success": True,
            "page_url": None,
            "message": last_entry.get("message", "No releases this week"),
            "all_published": published_urls,
            "filled_gaps": len(unpublished_weeks)
        })
    
    main_url = last_entry["url"]
    
    return jsonify({
        "success": True,
        "page_url": main_url,
        "all_published": published_urls,
        "filled_gaps": len(unpublished_weeks)
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

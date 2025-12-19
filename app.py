#!/usr/bin/env python3
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

    <button id="start">Generate & Open</button>

    <div class="spinner" id="spinner"></div>
    <div class="status" id="status">Redirecting to release notes…</div>

    <div class="footer">Internal Use Only · Stellantis SSDP</div>
</div>

<script>
    const start = document.getElementById("start");
    const spinner = document.getElementById("spinner");
    const status = document.getElementById("status");
    const week = document.getElementById("week");

    start.onclick = async () => {
        start.disabled = true;
        spinner.style.display = "block";
        status.style.display = "block";

        let url = "/run";
        if (week.value.trim()) {
            url += "?week=" + encodeURIComponent(week.value.trim());
        }

        try {
            const resp = await fetch(url);
            const data = await resp.json();

            if (data.success && data.page_url) {
                window.location.replace(data.page_url);
                return;
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

    extract_cmd = ["python3", "extract.py"]
    if week:
        extract_cmd += ["--week", week]

    code, out, err = run_cmd(extract_cmd)
    if code != 0:
        return jsonify({"success": False, "error": err or out})

    summarize_cmd = ["python3", "summarize.py"]
    if week:
        summarize_cmd += ["--week", week]

    code, out, err = run_cmd(summarize_cmd)
    if code != 0:
        return jsonify({"success": False, "error": err or out})

    code, out, err = run_cmd(["python3", "publish.py"])
    if code != 0:
        return jsonify({"success": False, "error": err or out})

    combined = (out or "") + "\n" + (err or "")

    page_url = None
    for line in combined.splitlines():
        if line.startswith("CONFLUENCE_PAGE_URL="):
            page_url = line.split("=", 1)[1].strip()
            break

    return jsonify({
        "success": True,
        "page_url": page_url
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

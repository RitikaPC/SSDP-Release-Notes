#!/usr/bin/env python3
from flask import Flask, render_template_string, jsonify, request
import subprocess
import re
import os
import json

app = Flask(__name__)

INDEX_HTML = """
<html>
<head>
  <title>Release Workflow</title>
  <style>
    body { font-family: Arial, sans-serif; background:#f4f5f7; display:flex; justify-content:center; align-items:center; min-height:100vh; }
    .container { background:white; padding:24px; border-radius:8px; width:900px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }
    input[type=number] { padding:8px; width:120px; margin-right:8px; }
    button { background:#0747A6; color:white; border:none; padding:8px 12px; border-radius:6px; cursor:pointer; }
    #status { margin-top:12px; font-weight:600; }
    pre { background:#f7f7f7; padding:8px; border-radius:6px; overflow:auto; }
    table { border-collapse:collapse; width:100%; margin-top:12px; }
    th, td { border:1px solid #ddd; padding:8px; text-align:left; }
    th { background:#0747A6; color:white; }
    a { color:#0747A6; text-decoration:none; }
  </style>
</head>
<body>
  <div class="container">
    <h2>Generate Release Notes</h2>
    <div>
      <label>Week (optional): </label>
      <input id="weekInput" type="number" min="1" max="53" placeholder="e.g. 45" />
      <button id="runBtn" onclick="runWorkflow()">Start</button>
    </div>
    <div id="status"></div>
    <div id="link"></div>
    <h3>Preview</h3>
    <div id="preview" style="border:1px solid #eee; padding:12px; border-radius:6px; max-height:500px; overflow:auto;"></div>
    <h3>Meta</h3>
    <div id="meta"></div>
  </div>

  <script>
    async function runWorkflow(){
      const btn = document.getElementById('runBtn');
      const status = document.getElementById('status');
      const link = document.getElementById('link');
      const preview = document.getElementById('preview');
      const meta = document.getElementById('meta');
      const week = document.getElementById('weekInput').value;

      btn.disabled = true;
      status.innerText = "Running extract → summarize → publish...";
      link.innerHTML = "";
      preview.innerHTML = "";
      meta.innerHTML = "";

      let url = '/run';
      if (week) url += '?week=' + encodeURIComponent(week);

      try {
        const resp = await fetch(url);
        const data = await resp.json();
        if (data.success) {
          status.innerText = "Workflow Completed Successfully";
          if (data.page_url) link.innerHTML = '<p>Page: <a href="'+data.page_url+'" target="_blank">'+data.page_url+'</a></p>';
          else link.innerHTML = "<p>No page published (check logs)</p>";
          if (data.summary_html) preview.innerHTML = data.summary_html;
          if (data.meta) meta.innerHTML = '<pre>' + JSON.stringify(data.meta, null, 2) + '</pre>';
        } else {
          status.innerText = "Error: " + (data.error || "unknown");
        }
      } catch (err) {
        status.innerText = "Error: " + err;
      } finally {
        btn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

def run_cmd(cmd):
    """Run a command and return (returncode, stdout, stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr

@app.route('/run')
def run_workflow():
    week = request.args.get('week')

    # Normalize week: treat empty string, whitespace, or None as "no week provided"
    if week is None or str(week).strip() == "":
        week = None

    try:
        # 1) extract
        extract_cmd = ["python3", "extract.py"]
        if week is not None:
            extract_cmd += ["--week", week]

        code, out, err = run_cmd(extract_cmd)
        if code != 0:
            return jsonify({"success": False, "error": f"extract.py failed: {err or out}"}), 200

        # 2) summarize
        summarize_cmd = ["python3", "summarize.py"]
        if week is not None:
            summarize_cmd += ["--week", week]

        code, out, err = run_cmd(summarize_cmd)
        if code != 0:
            return jsonify({"success": False, "error": f"summarize.py failed: {err or out}"}), 200

        # 3) publish
        code, out, err = run_cmd(["python3", "publish.py"])
        if code != 0:
            return jsonify({"success": False, "error": f"publish.py failed: {err or out}"}), 200

        # parse out page url from publish output
        page_url = None
        m = re.search(r"https?://[^\s'\"<>]+", out or "")
        if m:
            page_url = m.group(0)

        # load summary & meta for preview
        summary_html = ""
        meta = {}
        if os.path.exists("summary_output.html"):
            with open("summary_output.html", "r", encoding="utf-8") as f:
                summary_html = f.read()
        if os.path.exists("summary_meta.json"):
            with open("summary_meta.json", "r", encoding="utf-8") as f:
                try:
                    meta = json.load(f)
                except:
                    meta = {"error": "invalid JSON in summary_meta.json"}

        return jsonify({
            "success": True,
            "page_url": page_url,
            "summary_html": summary_html,
            "meta": meta
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 200

if __name__ == '__main__':
    app.run(port=5000, debug=True)

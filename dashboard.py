"""
Web Dashboard for GKE Self-Healing Agent
Provides real-time visibility into agent status, issues, and healing actions.
"""

import json
import logging
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template_string

logger = logging.getLogger("self-healing-agent.dashboard")

# ─────────────────────────────────────────────────────────────────────────────
# Shared state — the agent workflow writes here, the dashboard reads
# ─────────────────────────────────────────────────────────────────────────────
_state = {
    "status": "initializing",
    "started_at": None,
    "last_check": None,
    "checks_total": 0,
    "issues_detected": 0,
    "actions_taken": 0,
    "dry_run": False,
    "namespaces": [],
    "recent_issues": [],      # last 50 issues
    "recent_actions": [],     # last 50 actions
    "incidents": [],          # full incident reports
}
_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# State mutation helpers (called from agent_workflow)
# ─────────────────────────────────────────────────────────────────────────────

def set_status(status):
    with _lock:
        _state["status"] = status

def set_config(dry_run, namespaces):
    with _lock:
        _state["dry_run"] = dry_run
        _state["namespaces"] = namespaces
        _state["started_at"] = datetime.utcnow().isoformat()

def record_check(issues):
    with _lock:
        _state["last_check"] = datetime.utcnow().isoformat()
        _state["checks_total"] += 1
        _state["issues_detected"] += len(issues)
        for issue in issues:
            _state["recent_issues"].append(issue)
            if len(_state["recent_issues"]) > 50:
                _state["recent_issues"].pop(0)

def record_action(action_result, issue, analysis):
    with _lock:
        _state["actions_taken"] += 1
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "issue_type": issue.get("type"),
            "pod": issue.get("pod"),
            "namespace": issue.get("namespace"),
            "action": action_result.get("action"),
            "success": action_result.get("success"),
            "dry_run": action_result.get("dry_run", False),
            "message": action_result.get("message"),
        }
        _state["recent_actions"].append(entry)
        if len(_state["recent_actions"]) > 50:
            _state["recent_actions"].pop(0)

def record_incident(incident_report):
    with _lock:
        _state["incidents"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "report": incident_report[:2000],
        })
        if len(_state["incidents"]) > 20:
            _state["incidents"].pop(0)


# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GKE Self-Healing Agent</title>
<style>
  :root { --bg: #0f172a; --card: #1e293b; --border: #334155;
          --text: #e2e8f0; --muted: #94a3b8; --green: #22c55e;
          --red: #ef4444; --yellow: #eab308; --blue: #3b82f6; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg);
         color: var(--text); min-height: 100vh; }
  .container { max-width: 1200px; margin: 0 auto; padding: 1.5rem; }
  header { display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem;
           padding-bottom: 1rem; border-bottom: 1px solid var(--border); }
  header h1 { font-size: 1.5rem; }
  .badge { padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.75rem;
           font-weight: 600; text-transform: uppercase; }
  .badge-green { background: rgba(34,197,94,.15); color: var(--green); }
  .badge-yellow { background: rgba(234,179,8,.15); color: var(--yellow); }
  .badge-red { background: rgba(239,68,68,.15); color: var(--red); }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
           gap: 1rem; margin-bottom: 2rem; }
  .stat { background: var(--card); border: 1px solid var(--border);
          border-radius: 0.75rem; padding: 1.25rem; }
  .stat-label { font-size: 0.8rem; color: var(--muted); margin-bottom: 0.25rem; }
  .stat-value { font-size: 1.75rem; font-weight: 700; }
  .section { margin-bottom: 2rem; }
  .section h2 { font-size: 1.1rem; margin-bottom: 0.75rem; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          border: 1px solid var(--border); border-radius: 0.75rem; overflow: hidden; }
  th, td { padding: 0.6rem 1rem; text-align: left; border-bottom: 1px solid var(--border);
           font-size: 0.85rem; }
  th { background: rgba(255,255,255,.03); color: var(--muted); font-weight: 600; }
  .severity-critical { color: var(--red); font-weight: 600; }
  .severity-warning { color: var(--yellow); }
  .success { color: var(--green); }
  .failure { color: var(--red); }
  .empty { text-align: center; padding: 2rem; color: var(--muted); }
  .dry-run-banner { background: rgba(234,179,8,.1); border: 1px solid var(--yellow);
                    border-radius: 0.5rem; padding: 0.75rem 1rem; margin-bottom: 1.5rem;
                    color: var(--yellow); font-size: 0.9rem; }
  footer { text-align: center; color: var(--muted); font-size: 0.75rem;
           padding-top: 1rem; border-top: 1px solid var(--border); }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>GKE Self-Healing Agent</h1>
    <span class="badge" id="status-badge">Loading...</span>
  </header>

  <div id="dry-run-banner" class="dry-run-banner" style="display:none;">
    DRY RUN MODE — No actual changes are being made to the cluster
  </div>

  <div class="stats">
    <div class="stat"><div class="stat-label">Total Checks</div>
      <div class="stat-value" id="checks-total">0</div></div>
    <div class="stat"><div class="stat-label">Issues Detected</div>
      <div class="stat-value" id="issues-detected">0</div></div>
    <div class="stat"><div class="stat-label">Actions Taken</div>
      <div class="stat-value" id="actions-taken">0</div></div>
    <div class="stat"><div class="stat-label">Last Check</div>
      <div class="stat-value" id="last-check" style="font-size:0.95rem;">—</div></div>
  </div>

  <div class="section">
    <h2>Recent Issues</h2>
    <table><thead><tr>
      <th>Time</th><th>Type</th><th>Severity</th><th>Pod</th><th>Namespace</th>
    </tr></thead><tbody id="issues-body">
      <tr><td colspan="5" class="empty">No issues detected yet</td></tr>
    </tbody></table>
  </div>

  <div class="section">
    <h2>Recent Actions</h2>
    <table><thead><tr>
      <th>Time</th><th>Action</th><th>Target</th><th>Result</th><th>Message</th>
    </tr></thead><tbody id="actions-body">
      <tr><td colspan="5" class="empty">No actions taken yet</td></tr>
    </tbody></table>
  </div>

  <footer>GKE Self-Healing Agent v2.0.0 &mdash; Powered by Vertex AI &amp; Gemini</footer>
</div>

<script>
function statusClass(s) {
  if (s === 'running') return 'badge-green';
  if (s === 'error') return 'badge-red';
  return 'badge-yellow';
}
function sevClass(s) { return s === 'critical' ? 'severity-critical' : 'severity-warning'; }
function shortTime(iso) {
  if (!iso) return '—';
  return new Date(iso + 'Z').toLocaleTimeString();
}

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    document.getElementById('status-badge').textContent = d.status.toUpperCase();
    document.getElementById('status-badge').className = 'badge ' + statusClass(d.status);
    document.getElementById('checks-total').textContent = d.checks_total;
    document.getElementById('issues-detected').textContent = d.issues_detected;
    document.getElementById('actions-taken').textContent = d.actions_taken;
    document.getElementById('last-check').textContent = shortTime(d.last_check);
    document.getElementById('dry-run-banner').style.display = d.dry_run ? 'block' : 'none';

    const ib = document.getElementById('issues-body');
    if (d.recent_issues.length === 0) {
      ib.innerHTML = '<tr><td colspan="5" class="empty">No issues detected yet</td></tr>';
    } else {
      ib.innerHTML = d.recent_issues.slice().reverse().map(i => `<tr>
        <td>${shortTime(i.detected_at)}</td>
        <td>${i.type}</td>
        <td class="${sevClass(i.severity)}">${i.severity}</td>
        <td>${i.pod || '—'}</td>
        <td>${i.namespace || '—'}</td>
      </tr>`).join('');
    }

    const ab = document.getElementById('actions-body');
    if (d.recent_actions.length === 0) {
      ab.innerHTML = '<tr><td colspan="5" class="empty">No actions taken yet</td></tr>';
    } else {
      ab.innerHTML = d.recent_actions.slice().reverse().map(a => `<tr>
        <td>${shortTime(a.timestamp)}</td>
        <td>${a.action || '—'}</td>
        <td>${a.namespace}/${a.pod || '—'}</td>
        <td class="${a.success ? 'success' : 'failure'}">${a.success ? 'OK' : 'FAIL'}${a.dry_run ? ' (dry)' : ''}</td>
        <td>${a.message || '—'}</td>
      </tr>`).join('');
    }
  } catch(e) { console.error('Refresh error', e); }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


def create_app():
    app = Flask(__name__)
    app.logger.setLevel(logging.WARNING)
    # Suppress Flask request logs
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})

    @app.route("/api/status")
    def status():
        with _lock:
            return jsonify({**_state})

    @app.route("/api/incidents")
    def incidents():
        with _lock:
            return jsonify(_state["incidents"])

    return app


def start_dashboard(port=8080):
    """Start the dashboard in a background thread."""
    app = create_app()
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    logger.info(f"Dashboard started on http://0.0.0.0:{port}")
    return thread

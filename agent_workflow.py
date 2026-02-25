"""
Main Agent Workflow — Entry Point
Orchestrates monitoring, AI analysis, healing actions, and the web dashboard.
"""

import logging
import os
import sys
import signal
import time

from agent_config import AGENT_CONFIG
from gcp_monitor import GCPMonitor
from healing_actions import HealingActions


# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging():
    level = getattr(logging, AGENT_CONFIG["log_level"].upper(), logging.INFO)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)
    # Quieten noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("kubernetes").setLevel(logging.WARNING)


logger = logging.getLogger("self-healing-agent")

# Graceful shutdown flag
_running = True


def _signal_handler(signum, frame):
    global _running
    logger.info("Received shutdown signal — stopping agent")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    _setup_logging()
    config = AGENT_CONFIG

    logger.info("=" * 60)
    logger.info(f"  {config['name']}  v{config['version']}")
    logger.info(f"  Project:    {config['gcp_project']}")
    logger.info(f"  Cluster:    {config['gke_cluster']}")
    logger.info(f"  Namespaces: {', '.join(config['namespaces'])}")
    logger.info(f"  Interval:   {config['check_interval']}s")
    logger.info(f"  Dry Run:    {config['dry_run']}")
    logger.info(f"  Model:      {config['model']}")
    logger.info("=" * 60)

    # ── Start web dashboard ──────────────────────────────────────────────
    if config["dashboard_enabled"]:
        try:
            from dashboard import start_dashboard, set_status, set_config, \
                record_check, record_action, record_incident
            set_config(config["dry_run"], config["namespaces"])
            start_dashboard(port=config["dashboard_port"])
            logger.info(f"Dashboard: http://localhost:{config['dashboard_port']}")
        except ImportError:
            logger.warning("Flask not installed — dashboard disabled")
            config["dashboard_enabled"] = False
        except Exception as e:
            logger.warning(f"Dashboard failed to start: {e}")
            config["dashboard_enabled"] = False

    # ── Initialise components ────────────────────────────────────────────
    monitor = GCPMonitor(
        project_id=config["gcp_project"],
        cluster_name=config["gke_cluster"],
        vertex_ai_location=config["vertex_ai_location"],
        model_name=config["model"],
    )
    healer = HealingActions(
        dry_run=config["dry_run"],
        max_actions_per_hour=config["safety"]["max_actions_per_hour"],
        cooldown_seconds=config["safety"]["cooldown_seconds"],
    )

    if config["dashboard_enabled"]:
        set_status("running")

    # ── Main monitoring loop ─────────────────────────────────────────────
    while _running:
        try:
            _run_check_cycle(config, monitor, healer)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception(f"Unexpected error in check cycle: {e}")
            if config["dashboard_enabled"]:
                set_status("error")

        # Sleep in small increments so we can respond to SIGTERM quickly
        for _ in range(config["check_interval"]):
            if not _running:
                break
            time.sleep(1)

    logger.info("Agent stopped.")
    if config["dashboard_enabled"]:
        set_status("stopped")


def _run_check_cycle(config, monitor, healer):
    """Run one full monitor → analyze → heal cycle across all namespaces."""
    if config["dashboard_enabled"]:
        from dashboard import record_check, record_action, record_incident

    all_issues = []

    for ns in config["namespaces"]:
        ns = ns.strip()
        if ns in config["safety"]["excluded_namespaces"]:
            continue

        logger.info(f"Checking namespace: {ns}")
        issues = monitor.check_pod_health(namespace=ns)
        all_issues.extend(issues)

    if config["dashboard_enabled"]:
        record_check(all_issues)

    if not all_issues:
        logger.info("All healthy — no issues detected")
        return

    logger.warning(f"Found {len(all_issues)} issue(s)")

    for issue in all_issues:
        _handle_issue(config, monitor, healer, issue)


def _handle_issue(config, monitor, healer, issue):
    """Process a single issue: gather context → AI analysis → healing action."""
    if config["dashboard_enabled"]:
        from dashboard import record_action, record_incident

    pod_name = issue.get("pod")
    namespace = issue.get("namespace")
    issue_type = issue.get("type")

    logger.info(f"Processing: {issue_type} on {namespace}/{pod_name}")

    # ── Gather context ───────────────────────────────────────────────
    logs = ""
    if pod_name:
        logs = monitor.get_pod_logs(pod_name, namespace)

    # ── AI analysis ──────────────────────────────────────────────────
    analysis = monitor.analyze_with_gemini(issue, logs)
    logger.info(f"Analysis → root_cause: {analysis.get('root_cause', 'N/A')}")

    # ── Determine and execute healing action ─────────────────────────
    action_result = _execute_healing(config, monitor, healer, issue, analysis)

    if action_result is None:
        logger.info(f"No healing action mapped for issue type: {issue_type}")
        return

    # ── Generate incident report ─────────────────────────────────────
    report = healer.generate_incident_report(issue, analysis, action_result)

    # Save report to disk
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    report_dir = os.environ.get("REPORT_DIR", ".")
    filename = os.path.join(report_dir, f"incident_report_{timestamp}.md")
    try:
        with open(filename, "w") as f:
            f.write(report)
        logger.info(f"Incident report saved: {filename}")
    except OSError as e:
        logger.warning(f"Could not save report: {e}")

    # Update dashboard
    if config["dashboard_enabled"]:
        record_action(action_result, issue, analysis)
        record_incident(report)

    logger.info(f"Result: {action_result.get('message', 'done')}")


def _execute_healing(config, monitor, healer, issue, analysis):
    """Map issue type to a healing action and execute it."""
    issue_type = issue.get("type")
    namespace = issue.get("namespace")
    pod_name = issue.get("pod")
    defaults = config["healing_defaults"]

    if issue_type == "oom_killed":
        deployment = monitor.get_deployment_for_pod(pod_name, namespace)
        return healer.increase_resource_limits(
            deployment, namespace,
            defaults["oom_memory_increase"],
            defaults["oom_cpu_increase"],
        )

    elif issue_type in ("high_restart_count", "crash_loop_backoff"):
        return healer.delete_pod(pod_name, namespace)

    elif issue_type == "pod_not_running":
        deployment = monitor.get_deployment_for_pod(pod_name, namespace)
        return healer.restart_deployment(deployment, namespace)

    return None


if __name__ == "__main__":
    main()

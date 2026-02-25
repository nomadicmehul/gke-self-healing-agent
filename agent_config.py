"""
Self-Healing GKE Agent Configuration
Supports environment variables for all settings
"""

import os
import subprocess


def _get_env(key, default=None, cast_type=str):
    """Get environment variable with optional type casting."""
    value = os.environ.get(key, default)
    if value is None:
        return None
    if cast_type == bool:
        return str(value).lower() in ("true", "1", "yes")
    return cast_type(value)


def _get_gcloud_project():
    """Try to get the project ID from gcloud config."""
    try:
        import subprocess
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, check=True
        )
        project = result.stdout.strip()
        if project and "unset" not in project:
            return project
    except Exception:
        pass
    return "your-gcp-project-id"

AGENT_CONFIG = {
    "name": "GKE Self-Healing Agent",
    "description": "Autonomous agent that monitors GKE cluster health and performs healing actions",
    "version": "2.0.0",

    # AI Model Configuration
    "model": _get_env("AGENT_MODEL", "gemini-2.0-flash-001"),
    "vertex_ai_location": _get_env("VERTEX_AI_LOCATION", "us-central1"),

    # Capabilities
    "capabilities": [
        "monitor_cluster_health",
        "analyze_logs",
        "diagnose_issues",
        "execute_kubectl_commands",
        "scale_deployments",
        "update_resources",
        "generate_incident_reports"
    ],

    # Operational Settings
    "check_interval": _get_env("CHECK_INTERVAL", 30, int),
    "dry_run": _get_env("DRY_RUN", False, bool),
    "log_level": _get_env("LOG_LEVEL", "INFO"),
    "dashboard_enabled": _get_env("DASHBOARD_ENABLED", True, bool),
    "dashboard_port": _get_env("DASHBOARD_PORT", 8080, int),

    # GCP Settings
    "gcp_project": _get_env("GCP_PROJECT", _get_gcloud_project()),
    "gke_cluster": _get_env("GKE_CLUSTER", "cloud-aittt2026"),
    "region": _get_env("GCP_REGION", "us-central1"),

    # Monitoring Scope
    "namespaces": _get_env("WATCH_NAMESPACES", "cloud-aittt2026").split(","),

    # Alert Thresholds
    "alert_thresholds": {
        "memory_usage": _get_env("THRESHOLD_MEMORY", 80, int),
        "cpu_usage": _get_env("THRESHOLD_CPU", 80, int),
        "pod_restart_count": _get_env("THRESHOLD_RESTART_COUNT", 3, int),
        "crash_loop_count": _get_env("THRESHOLD_CRASHLOOP", 2, int),
    },

    # Healing Policies
    "healing_policies": {
        "memory_pressure": "scale_up",
        "crash_loop": "restart_with_backoff",
        "high_cpu": "scale_horizontal",
        "pod_oom": "increase_limits",
    },

    # Resource Limits for Healing Actions
    "healing_defaults": {
        "oom_memory_increase": _get_env("OOM_MEMORY_INCREASE", "256Mi"),
        "oom_cpu_increase": _get_env("OOM_CPU_INCREASE", "200m"),
        "scale_up_increment": _get_env("SCALE_UP_INCREMENT", 1, int),
        "max_replicas": _get_env("MAX_REPLICAS", 10, int),
    },

    # Safety Settings
    "safety": {
        "max_actions_per_hour": _get_env("MAX_ACTIONS_PER_HOUR", 20, int),
        "cooldown_seconds": _get_env("COOLDOWN_SECONDS", 60, int),
        "excluded_namespaces": ["kube-system", "kube-public", "istio-system"],
    },
}

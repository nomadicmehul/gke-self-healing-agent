"""
Self-Healing Actions for GKE
Provides Kubernetes remediation actions with safety controls.
"""

import json
import logging
import time
from collections import defaultdict
from datetime import datetime

from kubernetes import client
from kubernetes.client.rest import ApiException

logger = logging.getLogger("self-healing-agent.healer")


class HealingActions:
    def __init__(self, dry_run=False, max_actions_per_hour=20, cooldown_seconds=60):
        self.dry_run = dry_run
        self.max_actions_per_hour = max_actions_per_hour
        self.cooldown_seconds = cooldown_seconds
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()

        # Track actions for rate limiting
        self._action_log = []
        self._last_action_time = defaultdict(float)

        # Incident history
        self.incidents = []

    def _check_rate_limit(self):
        """Enforce rate limit on healing actions."""
        now = time.time()
        one_hour_ago = now - 3600
        self._action_log = [t for t in self._action_log if t > one_hour_ago]
        if len(self._action_log) >= self.max_actions_per_hour:
            logger.warning(
                f"Rate limit reached: {len(self._action_log)}/{self.max_actions_per_hour} "
                "actions in the last hour"
            )
            return False
        return True

    def _check_cooldown(self, resource_key):
        """Prevent repeated actions on the same resource within cooldown period."""
        now = time.time()
        last = self._last_action_time.get(resource_key, 0)
        if now - last < self.cooldown_seconds:
            remaining = int(self.cooldown_seconds - (now - last))
            logger.info(f"Cooldown active for {resource_key} — {remaining}s remaining")
            return False
        return True

    def _record_action(self, resource_key):
        """Record an action for rate limiting and cooldown tracking."""
        now = time.time()
        self._action_log.append(now)
        self._last_action_time[resource_key] = now

    def scale_deployment(self, name, namespace, replicas):
        """Scale deployment to specified number of replicas."""
        resource_key = f"scale:{namespace}/{name}"
        if not self._check_rate_limit() or not self._check_cooldown(resource_key):
            return {"success": False, "error": "Rate limited or in cooldown"}

        if self.dry_run:
            msg = f"[DRY RUN] Would scale {namespace}/{name} to {replicas} replicas"
            logger.info(msg)
            return {"success": True, "action": "scale_deployment", "dry_run": True,
                    "deployment": name, "namespace": namespace,
                    "new_replicas": replicas, "message": msg}

        try:
            body = {"spec": {"replicas": replicas}}
            self.k8s_apps.patch_namespaced_deployment_scale(
                name=name, namespace=namespace, body=body
            )
            self._record_action(resource_key)
            msg = f"Scaled {namespace}/{name} to {replicas} replicas"
            logger.info(msg)
            return {"success": True, "action": "scale_deployment",
                    "deployment": name, "namespace": namespace,
                    "new_replicas": replicas, "message": msg}
        except ApiException as e:
            logger.error(f"Failed to scale {namespace}/{name}: {e.reason}")
            return {"success": False, "error": e.reason}
        except Exception as e:
            logger.error(f"Unexpected error scaling {namespace}/{name}: {e}")
            return {"success": False, "error": str(e)}

    def increase_resource_limits(self, deployment_name, namespace, memory_limit, cpu_limit):
        """Increase resource limits for all containers in a deployment."""
        resource_key = f"limits:{namespace}/{deployment_name}"
        if not self._check_rate_limit() or not self._check_cooldown(resource_key):
            return {"success": False, "error": "Rate limited or in cooldown"}

        if self.dry_run:
            msg = (f"[DRY RUN] Would increase limits for {namespace}/{deployment_name} "
                   f"to memory={memory_limit}, cpu={cpu_limit}")
            logger.info(msg)
            return {"success": True, "action": "increase_limits", "dry_run": True,
                    "deployment": deployment_name, "namespace": namespace,
                    "new_limits": {"memory": memory_limit, "cpu": cpu_limit},
                    "message": msg}

        try:
            deployment = self.k8s_apps.read_namespaced_deployment(
                deployment_name, namespace
            )

            for container in deployment.spec.template.spec.containers:
                if container.resources is None:
                    container.resources = client.V1ResourceRequirements()
                if container.resources.limits is None:
                    container.resources.limits = {}
                if container.resources.requests is None:
                    container.resources.requests = {}

                container.resources.limits["memory"] = memory_limit
                container.resources.limits["cpu"] = cpu_limit
                # Also bump requests to be reasonable
                container.resources.requests["memory"] = memory_limit
                container.resources.requests["cpu"] = cpu_limit

            self.k8s_apps.patch_namespaced_deployment(
                name=deployment_name, namespace=namespace, body=deployment
            )
            self._record_action(resource_key)
            msg = f"Increased resource limits for {namespace}/{deployment_name}"
            logger.info(msg)
            return {"success": True, "action": "increase_limits",
                    "deployment": deployment_name, "namespace": namespace,
                    "new_limits": {"memory": memory_limit, "cpu": cpu_limit},
                    "message": msg}
        except ApiException as e:
            logger.error(f"Failed to update limits for {namespace}/{deployment_name}: {e.reason}")
            return {"success": False, "error": e.reason}
        except Exception as e:
            logger.error(f"Unexpected error updating limits: {e}")
            return {"success": False, "error": str(e)}

    def restart_deployment(self, name, namespace):
        """Restart deployment by patching the restart annotation."""
        resource_key = f"restart:{namespace}/{name}"
        if not self._check_rate_limit() or not self._check_cooldown(resource_key):
            return {"success": False, "error": "Rate limited or in cooldown"}

        if self.dry_run:
            msg = f"[DRY RUN] Would restart deployment {namespace}/{name}"
            logger.info(msg)
            return {"success": True, "action": "restart_deployment", "dry_run": True,
                    "deployment": name, "namespace": namespace, "message": msg}

        try:
            now = datetime.utcnow().isoformat() + "Z"
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now
                            }
                        }
                    }
                }
            }
            self.k8s_apps.patch_namespaced_deployment(
                name=name, namespace=namespace, body=body
            )
            self._record_action(resource_key)
            msg = f"Restarted deployment {namespace}/{name}"
            logger.info(msg)
            return {"success": True, "action": "restart_deployment",
                    "deployment": name, "namespace": namespace, "message": msg}
        except ApiException as e:
            logger.error(f"Failed to restart {namespace}/{name}: {e.reason}")
            return {"success": False, "error": e.reason}
        except Exception as e:
            logger.error(f"Unexpected error restarting deployment: {e}")
            return {"success": False, "error": str(e)}

    def delete_pod(self, pod_name, namespace):
        """Delete a problematic pod (will be recreated by its controller)."""
        resource_key = f"delete:{namespace}/{pod_name}"
        if not self._check_rate_limit() or not self._check_cooldown(resource_key):
            return {"success": False, "error": "Rate limited or in cooldown"}

        if self.dry_run:
            msg = f"[DRY RUN] Would delete pod {namespace}/{pod_name}"
            logger.info(msg)
            return {"success": True, "action": "delete_pod", "dry_run": True,
                    "pod": pod_name, "namespace": namespace, "message": msg}

        try:
            self.k8s_core.delete_namespaced_pod(
                name=pod_name, namespace=namespace
            )
            self._record_action(resource_key)
            msg = f"Deleted pod {namespace}/{pod_name} — will be recreated by controller"
            logger.info(msg)
            return {"success": True, "action": "delete_pod",
                    "pod": pod_name, "namespace": namespace, "message": msg}
        except ApiException as e:
            logger.error(f"Failed to delete pod {namespace}/{pod_name}: {e.reason}")
            return {"success": False, "error": e.reason}
        except Exception as e:
            logger.error(f"Unexpected error deleting pod: {e}")
            return {"success": False, "error": str(e)}

    def generate_incident_report(self, issue, analysis, action_taken):
        """Generate a markdown incident report and store in memory."""
        timestamp = datetime.utcnow().isoformat()
        is_ai = isinstance(analysis, dict)

        report = f"""# Incident Report
**Generated:** {timestamp}
**Agent Version:** 2.0.0

## Issue Detected
- **Type:** {issue.get('type', 'unknown')}
- **Severity:** {issue.get('severity', 'unknown')}
- **Resource:** {issue.get('pod', 'N/A')}
- **Namespace:** {issue.get('namespace', 'N/A')}
- **Container:** {issue.get('container', 'N/A')}
- **Detected At:** {issue.get('detected_at', timestamp)}

## AI Analysis
- **Root Cause:** {analysis.get('root_cause', 'N/A') if is_ai else analysis}
- **Risk Level:** {analysis.get('risk_level', 'N/A') if is_ai else 'N/A'}
- **Explanation:** {analysis.get('explanation', 'N/A') if is_ai else 'N/A'}

## Action Taken
```json
{json.dumps(action_taken, indent=2)}
```

## Resolution Status
**Result:** {'Successful' if action_taken.get('success') else 'Failed'}
**Dry Run:** {'Yes' if action_taken.get('dry_run') else 'No'}
"""

        incident = {
            "timestamp": timestamp,
            "issue": issue,
            "analysis": analysis if is_ai else {"raw": analysis},
            "action": action_taken,
            "report": report,
        }
        self.incidents.append(incident)

        return report

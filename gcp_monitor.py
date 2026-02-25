"""
GCP Monitoring and Logging Integration
Provides cluster health monitoring, metrics collection, and AI-powered analysis.
"""

import logging
import time
from datetime import datetime, timedelta

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger("self-healing-agent.monitor")


class GCPMonitor:
    def __init__(self, project_id, cluster_name, vertex_ai_location="us-central1",
                 model_name="gemini-2.0-flash"):
        self.project_id = project_id
        self.cluster_name = cluster_name
        self.vertex_ai_location = vertex_ai_location
        self.model_name = model_name

        # Initialize Kubernetes clients
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Loaded local kubeconfig")
            except config.ConfigException:
                logger.warning("No Kubernetes config found — running in offline/demo mode")

        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()

        # Initialize Vertex AI client (lazy — only when needed)
        self._genai_model = None

        # Initialize Cloud Monitoring client (optional)
        self._monitoring_client = None
        self._logging_client = None

    def _get_genai_model(self):
        """Lazy-load Google GenAI model."""
        if self._genai_model is None:
            try:
                from google import genai

                ai_client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.vertex_ai_location,
                )
                self._genai_model = ai_client
                logger.info(f"Initialized Vertex AI with model {self.model_name}")
            except ImportError:
                logger.warning("google-genai not installed — AI analysis disabled")
            except Exception as e:
                logger.warning(f"Could not initialize Vertex AI: {e}")
        return self._genai_model

    def _get_monitoring_client(self):
        """Lazy-load Cloud Monitoring client."""
        if self._monitoring_client is None:
            try:
                from google.cloud import monitoring_v3
                self._monitoring_client = monitoring_v3.MetricServiceClient()
                logger.info("Initialized Cloud Monitoring client")
            except ImportError:
                logger.warning("google-cloud-monitoring not installed")
            except Exception as e:
                logger.warning(f"Could not initialize monitoring client: {e}")
        return self._monitoring_client

    def check_pod_health(self, namespace="Cloud-aittt2026"):
        """Check health of all pods in a namespace. Returns list of issues."""
        issues = []

        try:
            pods = self.k8s_core.list_namespaced_pod(namespace)
        except ApiException as e:
            logger.error(f"Failed to list pods in namespace '{namespace}': {e.reason}")
            return issues
        except Exception as e:
            logger.error(f"Unexpected error listing pods: {e}")
            return issues

        for pod in pods.items:
            pod_name = pod.metadata.name

            # Check container statuses
            container_statuses = pod.status.container_statuses or []
            for cs in container_statuses:
                # High restart count
                if cs.restart_count > 3:
                    issues.append({
                        "type": "high_restart_count",
                        "severity": "warning",
                        "pod": pod_name,
                        "namespace": namespace,
                        "container": cs.name,
                        "restart_count": cs.restart_count,
                        "state": str(cs.state),
                        "detected_at": datetime.utcnow().isoformat(),
                    })

                # OOMKilled detection
                if cs.last_state and cs.last_state.terminated:
                    if cs.last_state.terminated.reason == "OOMKilled":
                        issues.append({
                            "type": "oom_killed",
                            "severity": "critical",
                            "pod": pod_name,
                            "namespace": namespace,
                            "container": cs.name,
                            "detected_at": datetime.utcnow().isoformat(),
                        })

                # CrashLoopBackOff detection
                if cs.state and cs.state.waiting:
                    if cs.state.waiting.reason == "CrashLoopBackOff":
                        issues.append({
                            "type": "crash_loop_backoff",
                            "severity": "critical",
                            "pod": pod_name,
                            "namespace": namespace,
                            "container": cs.name,
                            "restart_count": cs.restart_count,
                            "detected_at": datetime.utcnow().isoformat(),
                        })

            # Check pod phase
            if pod.status.phase not in ("Running", "Succeeded"):
                issues.append({
                    "type": "pod_not_running",
                    "severity": "warning",
                    "pod": pod_name,
                    "namespace": namespace,
                    "phase": pod.status.phase,
                    "reason": pod.status.reason or "Unknown",
                    "detected_at": datetime.utcnow().isoformat(),
                })

        return issues

    def get_deployment_for_pod(self, pod_name, namespace):
        """Safely resolve the owning Deployment name for a pod."""
        try:
            pod = self.k8s_core.read_namespaced_pod(pod_name, namespace)
            for owner in (pod.metadata.owner_references or []):
                if owner.kind == "ReplicaSet":
                    rs = self.k8s_apps.read_namespaced_replica_set(owner.name, namespace)
                    for rs_owner in (rs.metadata.owner_references or []):
                        if rs_owner.kind == "Deployment":
                            return rs_owner.name
        except Exception as e:
            logger.warning(f"Could not resolve deployment for pod {pod_name}: {e}")

        # Fallback: strip the last two segments (replicaset hash + pod hash)
        parts = pod_name.rsplit("-", 2)
        if len(parts) >= 2:
            return parts[0]
        return pod_name

    def get_resource_metrics(self, namespace="Cloud-aittt2026"):
        """Get resource utilization metrics from Cloud Monitoring."""
        metrics = {}
        monitoring_client = self._get_monitoring_client()
        if monitoring_client is None:
            logger.info("Cloud Monitoring unavailable — skipping resource metrics")
            return metrics

        try:
            from google.cloud import monitoring_v3

            project_name = f"projects/{self.project_id}"
            interval = monitoring_v3.TimeInterval()
            now = time.time()
            interval.end_time.seconds = int(now)
            interval.start_time.seconds = int(now - 300)

            memory_filter = (
                f'resource.type="k8s_pod" AND '
                f'resource.labels.namespace_name="{namespace}" AND '
                f'metric.type="kubernetes.io/pod/memory/used_bytes"'
            )

            results = monitoring_client.list_time_series(
                request={
                    "name": project_name,
                    "filter": memory_filter,
                    "interval": interval,
                }
            )

            for result in results:
                pod_name = result.resource.labels.get("pod_name")
                if pod_name and result.points:
                    metrics[pod_name] = {
                        "memory_bytes": result.points[0].value.double_value,
                    }
        except Exception as e:
            logger.error(f"Error fetching resource metrics: {e}")

        return metrics

    def get_pod_logs(self, pod_name, namespace="Cloud-aittt2026", lines=50):
        """Fetch recent pod logs."""
        try:
            logs = self.k8s_core.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                tail_lines=lines,
            )
            return logs
        except ApiException as e:
            msg = f"K8s API error fetching logs for {pod_name}: {e.reason}"
            logger.warning(msg)
            return msg
        except Exception as e:
            msg = f"Error fetching logs for {pod_name}: {e}"
            logger.warning(msg)
            return msg

    def analyze_with_gemini(self, issue_data, logs):
        """Send issue context to Gemini for AI-powered root-cause analysis."""
        prompt = (
            "You are an expert Kubernetes SRE. Analyze the following issue and "
            "provide a structured JSON response with keys: root_cause, recommended_action, "
            "risk_level (low/medium/high), and explanation.\n\n"
            f"Issue Data:\n{issue_data}\n\n"
            f"Recent Pod Logs:\n{logs}\n\n"
            "Respond ONLY with valid JSON."
        )

        ai_client = self._get_genai_model()
        if ai_client is None:
            logger.info("AI analysis unavailable — returning default analysis")
            return {
                "root_cause": f"Detected {issue_data.get('type', 'unknown')} issue",
                "recommended_action": "apply_default_healing",
                "risk_level": "medium",
                "explanation": "AI analysis unavailable; applying rule-based healing.",
            }

        try:
            response = ai_client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            import json
            # Try to parse JSON from response
            text = response.text.strip()
            # Handle markdown code blocks
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            analysis = json.loads(text)
            logger.info(f"Gemini analysis: {analysis.get('root_cause', 'N/A')}")
            return analysis
        except Exception as e:
            logger.warning(f"Gemini analysis failed: {e}")
            return {
                "root_cause": f"Detected {issue_data.get('type', 'unknown')} issue",
                "recommended_action": "apply_default_healing",
                "risk_level": "medium",
                "explanation": f"AI analysis error: {e}. Applying rule-based healing.",
            }

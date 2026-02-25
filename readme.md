# Self-Healing GKE Infrastructure Agent

> An autonomous DevOps agent powered by Vertex AI (Gemini) that monitors, diagnoses, and heals Google Kubernetes Engine clusters without human intervention.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GCP](https://img.shields.io/badge/Google_Cloud-4285F4?logo=google-cloud&logoColor=white)](https://cloud.google.com)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?logo=kubernetes&logoColor=white)](https://kubernetes.io)

---

## Overview

This project demonstrates **autonomous, self-healing infrastructure** that operates without human intervention. Using Vertex AI's Gemini model for intelligent root-cause analysis and the Kubernetes API for remediation, the agent continuously monitors your GKE cluster, diagnoses problems using AI, and fixes them automatically.

**What it does:**

- Monitors GKE cluster health in real-time (configurable interval)
- Detects OOMKilled pods, CrashLoopBackOff, high restart counts, and failed pods
- Sends issue context + pod logs to Gemini for AI-powered root-cause analysis
- Executes healing actions: increase resource limits, delete/restart pods, scale deployments
- Logs every action with detailed incident reports (Markdown)
- Exposes a live web dashboard at `http://localhost:8080`

**Demo scenarios included:**

| Scenario | Trigger | Agent Response |
|----------|---------|----------------|
| OOMKilled pod | Pod exceeds memory limit | Increases deployment memory/CPU limits |
| CrashLoopBackOff | Container exits repeatedly | Deletes problematic pod (recreated by controller) |
| High restart count | >3 restarts | Deletes and recreates pod |
| Pod not running | Failed/Pending phase | Restarts the deployment |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│             Vertex AI  (Gemini)                  │
│          AI-powered root-cause analysis          │
└─────────────────────┬───────────────────────────┘
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
┌─────────────────┐       ┌──────────────────┐
│   GCP Monitor   │◄──────│  Cloud Monitoring │
│   (gcp_monitor) │       │  & Cloud Logging  │
└────────┬────────┘       └──────────────────┘
         │
         ▼
┌─────────────────┐       ┌──────────────────┐
│ Healing Actions  │──────►│  Web Dashboard   │
│ (healing_actions)│       │  (Flask :8080)   │
└────────┬────────┘       └──────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│        GKE Cluster               │
│  ┌─────────┐  ┌─────────┐      │
│  │  Pod 1  │  │  Pod 2  │ ...  │
│  └─────────┘  └─────────┘      │
└─────────────────────────────────┘
```

---

## Prerequisites

- **Google Cloud Project** with billing enabled
- **gcloud CLI** installed and authenticated
- **kubectl** installed
- **Python 3.9+**
- **Docker** (for containerized deployment)

---

## Quick Start (15 minutes)

### Option 1: Automated Setup (Recommended)

```bash
git clone https://github.com/yourusername/gke-self-healing-agent.git
cd gke-self-healing-agent

chmod +x quick-start.sh cleanup.sh demo-scenarios.sh

# Run automated setup — creates cluster, deploys demo apps, configures everything
./quick-start.sh

# Start the agent
source venv/bin/activate
python agent_workflow.py

# Open dashboard in browser
open http://localhost:8080
```

### Option 2: Run Locally (manual)

```bash
# 1. Set your GCP project
export GCP_PROJECT="your-project-id"
export GCP_REGION="us-central1"
export GKE_CLUSTER="cloud-aittt2026"

# Add project label if required by organization policy
gcloud projects add-labels $GCP_PROJECT --labels=environment=testing

# 2. Create cluster (Cost-optimized zonal cluster)
# Use a single zone to minimize IP usage (1-2 nodes instead of 3-6)
export ZONE="${GCP_REGION}-b" 

gcloud container clusters create $GKE_CLUSTER \
  --zone=$ZONE \
  --num-nodes=1 \
  --machine-type=e2-standard-2 \
  --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM \
  --enable-autoscaling --min-nodes=1 --max-nodes=3

# Get credentials
gcloud container clusters get-credentials $GKE_CLUSTER --zone=$ZONE


# 3. Deploy demo apps
kubectl apply -f demo-app.yaml

# 4. Install dependencies and run
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python agent_workflow.py
```

### Option 3: Deploy In-Cluster (containerized)

```bash
# 1. Build and push the Docker image
export PROJECT_ID="your-project-id"
docker build -t gcr.io/$PROJECT_ID/self-healing-agent:latest .
docker push gcr.io/$PROJECT_ID/self-healing-agent:latest

# 2. Update the image reference in k8s/agent-deployment.yaml
sed -i "s|gcr.io/YOUR_PROJECT|gcr.io/$PROJECT_ID|g" k8s/agent-deployment.yaml

# 3. Update the ConfigMap values in k8s/agent-deployment.yaml
# Edit GCP_PROJECT, GKE_CLUSTER, etc.

# 4. Deploy
kubectl apply -f k8s/agent-deployment.yaml

# 5. Access the dashboard
kubectl get svc -n self-healing-agent
# Open the EXTERNAL-IP in your browser
```

---

## Testing the Agent

Use the interactive demo scenario runner:

```bash
./demo-scenarios.sh          # Interactive menu
./demo-scenarios.sh oom      # Trigger OOMKill scenario
./demo-scenarios.sh crashloop # Trigger crash loop scenario
./demo-scenarios.sh stress   # Trigger memory stress
./demo-scenarios.sh all      # Run everything
./demo-scenarios.sh reset    # Reset demo namespace
```

Or trigger scenarios manually:

```bash
# Scenario 1: OOMKill
kubectl apply -f stress-test.yaml

# Scenario 2: Crash loops (auto-deployed by demo-app.yaml)
kubectl get pods -n demo-app -w

# Watch the dashboard
open http://localhost:8080
```

---

## Configuration

All settings can be configured via **environment variables** or by editing `agent_config.py`.

| Variable | Default | Description |
|----------|---------|-------------|
| `GCP_PROJECT` | `your-gcp-project-id` | GCP project ID |
| `GKE_CLUSTER` | `demo-gke-cluster` | GKE cluster name |
| `GCP_REGION` | `us-central1` | GCP region |
| `WATCH_NAMESPACES` | `demo-app` | Comma-separated namespaces to monitor |
| `CHECK_INTERVAL` | `30` | Seconds between health checks |
| `DRY_RUN` | `false` | If `true`, log actions without executing |
| `LOG_LEVEL` | `INFO` | Python log level |
| `DASHBOARD_ENABLED` | `true` | Enable web dashboard |
| `DASHBOARD_PORT` | `8080` | Dashboard port |
| `AGENT_MODEL` | `gemini-2.0-flash-001` | Vertex AI model name |
| `MAX_ACTIONS_PER_HOUR` | `20` | Rate limit on healing actions |
| `COOLDOWN_SECONDS` | `60` | Per-resource cooldown between actions |

### Dry Run Mode

Start the agent in dry-run mode to see what it **would** do without making changes:

```bash
DRY_RUN=true python agent_workflow.py
```

The dashboard will show a yellow banner indicating dry-run mode.

---

## Project Structure

```
gke-self-healing-agent/
├── agent_workflow.py          # Main entry point — orchestrates the loop
├── agent_config.py            # Configuration with env var support
├── gcp_monitor.py             # K8s health checks + Vertex AI analysis
├── healing_actions.py         # Remediation actions with safety controls
├── dashboard.py               # Flask web dashboard
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container image definition
├── demo-app.yaml              # Demo workloads with intentional issues
├── stress-test.yaml           # Memory stress test job
├── demo-scenarios.sh          # Interactive scenario runner
├── quick-start.sh             # Automated GCP setup script
├── cleanup.sh                 # Resource cleanup script
├── k8s/
│   └── agent-deployment.yaml  # K8s manifests for in-cluster deployment
│                               (Namespace, SA, RBAC, Deployment, Service)
├── .gitignore
└── readme.md
```

---

## Safety & Security

**Built-in protections:**

- **Rate limiting** — max 20 actions per hour (configurable)
- **Cooldown** — 60-second cooldown per resource after each action
- **Excluded namespaces** — `kube-system`, `kube-public`, `istio-system` are never touched
- **Dry-run mode** — test without making changes
- **Graceful shutdown** — responds to SIGTERM/SIGINT cleanly
- **Incident reports** — every action is documented in Markdown
- **RBAC** — in-cluster deployment uses least-privilege ClusterRole
- **Audit trail** — structured logging with timestamps

**For production:**

1. Start in dry-run mode and review incident reports
2. Enable healing on non-critical namespaces first
3. Set up alerting on the agent itself (dashboard health endpoint)
4. Review incident reports regularly
5. Consider adding Slack/PagerDuty notifications

---

## Cleanup

```bash
# Automated cleanup (reads .env for your settings)
./cleanup.sh

# Verify no resources remain
gcloud container clusters list
gcloud compute instances list
```

**Cost estimate:** ~$0.13/hour (~$3.10/day) for the demo cluster. Delete immediately after testing!

---

## Conference Demo Guide

### Setup (15 min before talk)

```bash
./quick-start.sh
python agent_workflow.py &
open http://localhost:8080
```

### Live Demo (10 min)

**Terminal 1:** Agent running + dashboard open in browser
**Terminal 2:** Run scenarios

```bash
./demo-scenarios.sh oom        # Show OOMKill detection + healing
./demo-scenarios.sh crashloop  # Show crash loop detection + pod deletion
```

### Teardown

```bash
./cleanup.sh
```

---

## Author

**Mehul Patel**
Docker Captain | DevOps Engineer | AI Enthusiast

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Future Enhancements

- [ ] Multi-cluster support
- [ ] Slack/PagerDuty notifications
- [ ] Custom healing policies via CRDs
- [ ] GitOps integration (ArgoCD/Flux)
- [ ] Prometheus metrics exporter
- [ ] ML-based anomaly detection
- [ ] Cost optimization recommendations

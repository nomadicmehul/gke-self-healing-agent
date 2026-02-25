#!/bin/bash
#
# Demo Scenario Runner for GKE Self-Healing Agent
# Run individual scenarios to test the agent's healing capabilities.
#
# Usage:
#   ./demo-scenarios.sh              # Interactive menu
#   ./demo-scenarios.sh oom          # Trigger OOM scenario
#   ./demo-scenarios.sh crashloop    # Trigger crash loop scenario
#   ./demo-scenarios.sh stress       # Trigger memory stress scenario
#   ./demo-scenarios.sh all          # Run all scenarios
#   ./demo-scenarios.sh reset        # Reset demo namespace
#

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

NAMESPACE="Cloud-aittt2026"

print_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

wait_and_watch() {
    local seconds=$1
    local msg=$2
    echo -e "${YELLOW}  Watching for ${seconds}s — ${msg}${NC}"
    echo -e "${YELLOW}  (Open the dashboard at http://localhost:8080 to see live updates)${NC}"
    echo ""
    kubectl get pods -n "$NAMESPACE" -w &
    WATCH_PID=$!
    sleep "$seconds"
    kill $WATCH_PID 2>/dev/null || true
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
scenario_oom() {
    print_header "Scenario: OOMKilled Pod"
    echo "  This deploys a pod that allocates more memory than its limit,"
    echo "  causing the kernel to OOMKill it. The agent should detect this"
    echo "  and increase the deployment's resource limits."
    echo ""

    cat <<'YAML' | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oom-demo
  namespace: demo-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: oom-demo
  template:
    metadata:
      labels:
        app: oom-demo
    spec:
      containers:
        - name: memory-hog
          image: polinux/stress
          command: ["stress"]
          args: ["--vm", "1", "--vm-bytes", "200M", "--vm-hang", "0"]
          resources:
            requests:
              memory: "64Mi"
            limits:
              memory: "100Mi"   # Will be OOMKilled
YAML

    echo -e "${GREEN}  Deployed oom-demo — it will be OOMKilled shortly${NC}"
    wait_and_watch 60 "agent should detect OOMKilled and increase limits"
}

# ─────────────────────────────────────────────────────────────────────────────
scenario_crashloop() {
    print_header "Scenario: CrashLoopBackOff"
    echo "  This deploys a pod that exits with error every 5 seconds."
    echo "  Kubernetes will put it in CrashLoopBackOff. The agent should"
    echo "  detect the high restart count and delete the pod."
    echo ""

    cat <<'YAML' | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: crashloop-demo
  namespace: demo-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: crashloop-demo
  template:
    metadata:
      labels:
        app: crashloop-demo
    spec:
      containers:
        - name: crasher
          image: busybox
          command: ["sh", "-c", "echo 'Starting...' && sleep 5 && echo 'Crashing!' && exit 1"]
YAML

    echo -e "${GREEN}  Deployed crashloop-demo — will enter CrashLoopBackOff${NC}"
    wait_and_watch 120 "agent should detect crash loop and delete pod"
}

# ─────────────────────────────────────────────────────────────────────────────
scenario_stress() {
    print_header "Scenario: Memory Stress Job"
    echo "  This runs a stress Job that pushes memory close to its limit."
    echo "  Useful for seeing how the agent monitors resource usage."
    echo ""

    kubectl apply -f stress-test.yaml 2>/dev/null || cat <<'YAML' | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: memory-stress
  namespace: demo-app
spec:
  template:
    spec:
      containers:
        - name: stress
          image: polinux/stress
          resources:
            requests:
              memory: "256Mi"
            limits:
              memory: "512Mi"
          command: ["stress"]
          args: ["--vm", "1", "--vm-bytes", "400M", "--vm-hang", "0"]
      restartPolicy: Never
YAML

    echo -e "${GREEN}  Deployed memory-stress job${NC}"
    wait_and_watch 60 "agent monitors resource usage"
}

# ─────────────────────────────────────────────────────────────────────────────
scenario_reset() {
    print_header "Resetting Demo Namespace"
    echo "  Deleting and recreating the demo-app namespace..."
    kubectl delete namespace "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    echo "  Waiting for namespace deletion..."
    sleep 10
    kubectl apply -f demo-app.yaml
    echo ""
    echo -e "${GREEN}  Demo namespace reset. Pods:${NC}"
    sleep 5
    kubectl get pods -n "$NAMESPACE"
}

# ─────────────────────────────────────────────────────────────────────────────
scenario_all() {
    print_header "Running All Scenarios"
    scenario_oom
    echo ""
    scenario_crashloop
    echo ""
    scenario_stress
}

# ─────────────────────────────────────────────────────────────────────────────
interactive_menu() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║   GKE Self-Healing Agent — Demo Scenarios               ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  1) OOMKilled pod         — triggers memory limit increase"
    echo "  2) CrashLoopBackOff      — triggers pod deletion"
    echo "  3) Memory stress job     — tests resource monitoring"
    echo "  4) Run all scenarios"
    echo "  5) Reset demo namespace"
    echo "  6) Exit"
    echo ""
    read -p "  Choose a scenario (1-6): " choice

    case "$choice" in
        1) scenario_oom ;;
        2) scenario_crashloop ;;
        3) scenario_stress ;;
        4) scenario_all ;;
        5) scenario_reset ;;
        6) exit 0 ;;
        *) echo -e "${RED}  Invalid choice${NC}"; exit 1 ;;
    esac
}

# ─────────────────────────────────────────────────────────────────────────────
# Ensure namespace exists
kubectl get namespace "$NAMESPACE" &>/dev/null || kubectl create namespace "$NAMESPACE"

case "${1:-}" in
    oom)       scenario_oom ;;
    crashloop) scenario_crashloop ;;
    stress)    scenario_stress ;;
    all)       scenario_all ;;
    reset)     scenario_reset ;;
    "")        interactive_menu ;;
    *)
        echo "Usage: $0 {oom|crashloop|stress|all|reset}"
        exit 1
        ;;
esac

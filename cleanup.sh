#!/bin/bash
#
# Complete cleanup script for GKE Self-Healing Agent
# Reads configuration from .env if available, otherwise uses defaults.
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info()    { echo -e "${BLUE}  $1${NC}"; }
print_success() { echo -e "${GREEN}  $1${NC}"; }
print_warning() { echo -e "${YELLOW}  $1${NC}"; }
print_error()   { echo -e "${RED}  $1${NC}"; }

# ── Load configuration ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    print_info "Loading configuration from .env"
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
else
    print_warning "No .env file found — using defaults / prompting"
    
    # Try to get current project from gcloud
    CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)
    
    if [ -n "$CURRENT_PROJECT" ]; then
        read -p "Enter your GCP Project ID (default: $CURRENT_PROJECT): " PROJECT_ID
        PROJECT_ID=${PROJECT_ID:-$CURRENT_PROJECT}
    else
        read -p "Enter your GCP Project ID: " PROJECT_ID
    fi
    
    read -p "Enter GCP Region (default: us-central1): " REGION
    REGION=${REGION:-us-central1}
    read -p "Enter GKE Cluster Name (default: cloud-aittt2026): " CLUSTER_NAME
    CLUSTER_NAME=${CLUSTER_NAME:-cloud-aittt2026}
    SA_EMAIL="antigravity-agent@${PROJECT_ID}.iam.gserviceaccount.com"
    KEY_FILE="$HOME/antigravity-sa-key.json"
fi

echo ""
echo -e "${RED}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${RED}║   CLEANUP — This will DELETE all agent resources        ║${NC}"
echo -e "${RED}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
print_info "Project:  $PROJECT_ID"
print_info "Region:   $REGION"
print_info "Cluster:  $CLUSTER_NAME"
echo ""

read -p "Are you sure you want to proceed? (y/n): " CONFIRM
if [[ ! $CONFIRM =~ ^[Yy]$ ]]; then
    print_warning "Cleanup cancelled."
    exit 0
fi

# ── Step 1: Stop the agent ──────────────────────────────────────────────────
echo ""
print_info "Step 1: Stopping agent process..."
pkill -f "python.*agent_workflow" 2>/dev/null && print_success "Agent stopped" || print_info "Agent not running"

# ── Step 2: Delete in-cluster agent (if deployed via k8s/) ──────────────────
print_info "Step 2: Deleting in-cluster agent namespace..."
kubectl delete namespace self-healing-agent --ignore-not-found=true 2>/dev/null || true

# ── Step 3: Delete demo applications ────────────────────────────────────────
print_info "Step 3: Deleting demo applications..."
kubectl delete namespace cloud-aittt2026 --ignore-not-found=true 2>/dev/null || true
kubectl delete job memory-stress --ignore-not-found=true 2>/dev/null || true

# ── Step 4: Delete GKE cluster ──────────────────────────────────────────────
print_info "Step 4: Deleting GKE cluster (this takes 5-10 minutes)..."
if gcloud container clusters describe "$CLUSTER_NAME" --region="$REGION" &>/dev/null; then
    gcloud container clusters delete "$CLUSTER_NAME" \
        --region="$REGION" \
        --quiet
    print_success "Cluster deleted"
else
    print_info "Cluster $CLUSTER_NAME not found — skipping"
fi

# ── Step 5: Delete service account ──────────────────────────────────────────
print_info "Step 5: Deleting service account..."
if gcloud iam service-accounts describe "$SA_EMAIL" &>/dev/null 2>&1; then
    gcloud iam service-accounts delete "$SA_EMAIL" --quiet
    print_success "Service account deleted"
else
    print_info "Service account not found — skipping"
fi

# ── Step 6: Delete key file ────────────────────────────────────────────────
print_info "Step 6: Deleting service account key..."
if [ -f "$KEY_FILE" ]; then
    rm -f "$KEY_FILE"
    print_success "Key file deleted"
else
    print_info "Key file not found — skipping"
fi

# ── Step 7: Check for orphaned resources ────────────────────────────────────
print_info "Step 7: Checking for orphaned load balancers..."
gcloud compute forwarding-rules list --filter="region:$REGION" \
    --format="value(name)" 2>/dev/null | while read -r rule; do
    [ -z "$rule" ] && continue
    print_warning "Deleting forwarding rule: $rule"
    gcloud compute forwarding-rules delete "$rule" --region="$REGION" --quiet 2>/dev/null || true
done

print_info "Step 8: Checking for orphaned persistent disks..."
gcloud compute disks list \
    --filter="zone:${REGION}-a OR zone:${REGION}-b OR zone:${REGION}-c" \
    --format="value(name,zone)" 2>/dev/null | while read -r disk zone; do
    [ -z "$disk" ] && continue
    print_warning "Deleting disk: $disk in zone: $zone"
    gcloud compute disks delete "$disk" --zone="$zone" --quiet 2>/dev/null || true
done

# ── Step 9: Cleanup local artifacts ────────────────────────────────────────
print_info "Step 9: Cleaning local artifacts..."
rm -f "$SCRIPT_DIR"/incident_report_*.md
rm -rf "$SCRIPT_DIR/venv"
rm -f "$SCRIPT_DIR/.env"
print_success "Local artifacts cleaned"

# ── Done ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}  Cleanup complete!${NC}"
echo ""
print_info "Cost Check:"
echo "  Visit: https://console.cloud.google.com/billing"
echo "  Verify no resources are still running"
echo ""

#!/bin/bash
#
# Quick Start Setup Script for GKE Self-Healing Agent
# This script automates the entire setup process
#
# Usage: ./quick-start.sh
#

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_step() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}📍 STEP $1: $2${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}

# Check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Banner
echo -e "${GREEN}"
cat << "EOF"
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   🤖 GKE Self-Healing Agent Setup                            ║
║   Powered by Google Antigravity & Gemini 3                   ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}\n"

# ============================================================================
# STEP 0: Pre-flight Checks
# ============================================================================
print_step "0" "Pre-flight Checks"

print_info "Checking required tools..."

# Check gcloud
if ! command_exists gcloud; then
    print_error "gcloud CLI not found. Please install: https://cloud.google.com/sdk/docs/install"
    exit 1
fi
print_success "gcloud CLI found"

# Check kubectl
if ! command_exists kubectl; then
    print_error "kubectl not found. Please install: https://kubernetes.io/docs/tasks/tools/"
    exit 1
fi
print_success "kubectl found"

# Check python3
if ! command_exists python3; then
    print_error "python3 not found. Please install Python 3.8+"
    exit 1
fi
print_success "python3 found ($(python3 --version))"

# Check pip
if ! command_exists pip3; then
    print_error "pip3 not found. Please install pip"
    exit 1
fi
print_success "pip3 found"

# ============================================================================
# STEP 1: Configuration
# ============================================================================
print_step "1" "Configuration"

# Try to get current project from gcloud
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null)

# Prompt for configuration
if [ -n "$CURRENT_PROJECT" ]; then
    read -p "Enter your GCP Project ID (default: $CURRENT_PROJECT): " PROJECT_ID
    PROJECT_ID=${PROJECT_ID:-$CURRENT_PROJECT}
else
    read -p "Enter your GCP Project ID: " PROJECT_ID
fi

if [ -z "$PROJECT_ID" ]; then
    print_error "Project ID cannot be empty"
    exit 1
fi

read -p "Enter GCP Region (default: us-central1): " REGION
REGION=${REGION:-us-central1}

read -p "Enter GKE Cluster Name (default: cloud-aittt2026): " CLUSTER_NAME
CLUSTER_NAME=${CLUSTER_NAME:-cloud-aittt2026}

echo ""
print_info "Configuration:"
echo "  Project ID:    $PROJECT_ID"
echo "  Region:        $REGION"
echo "  Cluster Name:  $CLUSTER_NAME"
echo ""

read -p "Continue with these settings? (y/n): " CONFIRM
if [[ ! $CONFIRM =~ ^[Yy]$ ]]; then
    print_warning "Setup cancelled by user"
    exit 0
fi

# ============================================================================
# STEP 2: GCP Authentication & Project Setup
# ============================================================================
print_step "2" "GCP Authentication & Project Setup"

print_info "Setting GCP project..."
gcloud config set project "$PROJECT_ID"
print_success "Project set to $PROJECT_ID"

print_info "Adding 'environment=testing' label to project..."
gcloud projects add-labels "$PROJECT_ID" --labels=environment=testing || print_warning "Could not add label. Usage might be restricted."
print_success "Project labeled as 'testing'"

print_info "Enabling required GCP APIs (this may take 2-3 minutes)..."
gcloud services enable \
    container.googleapis.com \
    compute.googleapis.com \
    monitoring.googleapis.com \
    logging.googleapis.com \
    cloudresourcemanager.googleapis.com \
    aiplatform.googleapis.com \
    artifactregistry.googleapis.com \
    --quiet

print_success "All required APIs enabled"

# ============================================================================
# STEP 3: Create GKE Cluster
# ============================================================================
print_step "3" "Creating GKE Cluster"

# Use a specific zone to save IP quota (Zonal cluster = 1/3 IP usage of Regional)
ZONE="${REGION}-b"

# Check if cluster already exists (Zonal check first)
if gcloud container clusters describe "$CLUSTER_NAME" --zone="$ZONE" &>/dev/null; then
    print_warning "Cluster $CLUSTER_NAME (Zonal) already exists in $ZONE"
    print_info "Using existing cluster"
elif gcloud container clusters describe "$CLUSTER_NAME" --region="$REGION" &>/dev/null; then
    print_warning "Cluster $CLUSTER_NAME (Regional) already exists in $REGION"
    print_warning "Regional clusters use 3x IP quota. If you hit quota limits, delete this cluster and re-run."
    print_info "Using existing cluster"
    # Set ZONE to empty so we don't use it in get-credentials later for regional
    ZONE="" 
else
    print_info "Creating ZONAL GKE cluster in $ZONE to save IP quota..."
    print_warning "☕ Perfect time for a coffee break! (5-8 mins)"
    
    gcloud container clusters create "$CLUSTER_NAME" \
        --zone="$ZONE" \
        --num-nodes=1 \
        --machine-type=e2-standard-2 \
        --logging=SYSTEM,WORKLOAD \
        --monitoring=SYSTEM \
        --enable-autoscaling \
        --min-nodes=1 \
        --max-nodes=3 \
        --disk-size=30 \
        --quiet
    
    print_success "GKE cluster created successfully"
fi

# Get cluster credentials
print_info "Getting cluster credentials..."
if [ -n "$ZONE" ]; then
    gcloud container clusters get-credentials "$CLUSTER_NAME" --zone="$ZONE"
else
    gcloud container clusters get-credentials "$CLUSTER_NAME" --region="$REGION"
fi
print_success "Credentials configured"

# Verify cluster access
print_info "Verifying cluster access..."
kubectl get nodes
print_success "Cluster is accessible"

# ============================================================================
# STEP 4: Create Service Account
# ============================================================================
print_step "4" "Creating Service Account"

SA_NAME="antigravity-agent"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Check if service account exists
if gcloud iam service-accounts describe "$SA_EMAIL" &>/dev/null; then
    print_warning "Service account $SA_NAME already exists"
else
    print_info "Creating service account..."
    gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Antigravity Self-Healing Agent" \
        --quiet
    print_success "Service account created"
fi

print_info "Granting IAM permissions..."

# Grant necessary roles
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/container.developer" \
    --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/monitoring.viewer" \
    --quiet

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/logging.viewer" \
    --quiet

print_success "IAM permissions granted"

# Create service account key
KEY_FILE="$HOME/antigravity-sa-key.json"
if [ -f "$KEY_FILE" ]; then
    print_warning "Key file already exists at $KEY_FILE"
    read -p "Overwrite existing key? (y/n): " OVERWRITE
    if [[ $OVERWRITE =~ ^[Yy]$ ]]; then
        rm "$KEY_FILE"
    else
        print_info "Using existing key file"
    fi
fi

if [ ! -f "$KEY_FILE" ]; then
    print_info "Creating service account key..."
    gcloud iam service-accounts keys create "$KEY_FILE" \
        --iam-account="$SA_EMAIL" \
        --quiet
    print_success "Service account key created at $KEY_FILE"
fi

# ============================================================================
# STEP 5: Deploy Demo Applications
# ============================================================================
print_step "5" "Deploying Demo Applications"

if [ -f "demo-app.yaml" ]; then
    print_info "Deploying demo applications..."
    kubectl apply -f demo-app.yaml
    print_success "Demo applications deployed"
    
    print_info "Waiting for pods to be ready (30 seconds)..."
    sleep 30
    
    print_info "Checking pod status..."
    kubectl get pods -n cloud-aittt2026
else
    print_warning "demo-app.yaml not found in current directory"
    print_info "Please ensure demo-app.yaml is in the project directory"
fi

# ============================================================================
# STEP 6: Python Environment Setup
# ============================================================================
print_step "6" "Setting Up Python Environment"

# Check if virtual environment exists
if [ -d "venv" ]; then
    print_warning "Virtual environment already exists"
    read -p "Recreate virtual environment? (y/n): " RECREATE
    if [[ $RECREATE =~ ^[Yy]$ ]]; then
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    print_info "Creating virtual environment..."
    python3 -m venv venv
    print_success "Virtual environment created"
fi

print_info "Activating virtual environment..."
source venv/bin/activate

print_info "Installing Python dependencies..."
if [ -f "requirements.txt" ]; then
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    print_success "Dependencies installed"
else
    print_warning "requirements.txt not found"
fi

# ============================================================================
# STEP 7: Configure Agent
# ============================================================================
print_step "7" "Configuring Agent"

if [ -f "agent_config.py" ]; then
    print_info "Updating agent_config.py with your settings..."
    
    # Update configuration (simple sed replacement)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/your-gcp-project-id/$PROJECT_ID/g" agent_config.py
        sed -i '' "s/demo-gke-cluster/$CLUSTER_NAME/g" agent_config.py
        sed -i '' "s/us-central1/$REGION/g" agent_config.py
    else
        # Linux
        sed -i "s/your-gcp-project-id/$PROJECT_ID/g" agent_config.py
        sed -i "s/demo-gke-cluster/$CLUSTER_NAME/g" agent_config.py
        sed -i "s/us-central1/$REGION/g" agent_config.py
    fi
    
    print_success "Agent configuration updated"
else
    print_warning "agent_config.py not found"
fi

# Set environment variable
export GOOGLE_APPLICATION_CREDENTIALS="$KEY_FILE"
print_success "Environment variables set"

# ============================================================================
# STEP 8: Verification
# ============================================================================
print_step "8" "Verification"

print_info "Verifying setup..."

# Check cluster
if kubectl get nodes &>/dev/null; then
    print_success "GKE cluster is accessible"
else
    print_error "Cannot access GKE cluster"
    exit 1
fi

# Check demo apps
if kubectl get namespace demo-app &>/dev/null; then
    print_success "Demo namespace exists"
    PODS=$(kubectl get pods -n demo-app --no-headers | wc -l | tr -d ' ')
    print_info "Found $PODS pod(s) in demo-app namespace"
else
    print_warning "Demo namespace not found"
fi

# Check credentials
if [ -f "$KEY_FILE" ]; then
    print_success "Service account key exists"
else
    print_error "Service account key not found"
    exit 1
fi

# Check Python environment
if [ -d "venv" ]; then
    print_success "Virtual environment exists"
else
    print_warning "Virtual environment not found"
fi

# ============================================================================
# COMPLETION
# ============================================================================
echo ""
echo -e "${GREEN}"
cat << "EOF"
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   ✅ Setup Complete!                                          ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

print_success "Your GKE Self-Healing Agent is ready!"
echo ""
print_info "Next steps:"
echo "  1. Run the agent:       python agent_workflow.py"
echo "  2. Open the dashboard:  http://localhost:8080"
echo "  3. Trigger scenarios:   ./demo-scenarios.sh"
echo "  4. Watch the magic happen!"
echo ""
print_info "Useful commands:"
echo "  • Watch pods:       kubectl get pods -n demo-app -w"
echo "  • View logs:        kubectl logs -f -n demo-app <pod-name>"
echo "  • Run scenarios:    ./demo-scenarios.sh"
echo "  • Dry-run mode:     DRY_RUN=true python agent_workflow.py"
echo "  • Clean up:         ./cleanup.sh"
echo ""
print_warning "Don't forget to cleanup resources when done to avoid charges!"
echo "  Run: ./cleanup.sh"
echo ""

# Save configuration for cleanup script
cat > .env << EOF
PROJECT_ID=$PROJECT_ID
REGION=$REGION
CLUSTER_NAME=$CLUSTER_NAME
SA_EMAIL=$SA_EMAIL
KEY_FILE=$KEY_FILE
EOF

print_success "Configuration saved to .env file"
echo ""
print_info "Estimated cost: ~\$0.13/hour (~\$3.10/day)"
echo ""

# Ask if user wants to run the agent now
read -p "Do you want to start the agent now? (y/n): " START_AGENT
if [[ $START_AGENT =~ ^[Yy]$ ]]; then
    print_info "Starting agent..."
    if [ -f "agent_workflow.py" ]; then
        python agent_workflow.py
    else
        print_error "agent_workflow.py not found"
        print_info "Please ensure all Python files are in the project directory"
    fi
fi

print_success "Setup script completed successfully!"
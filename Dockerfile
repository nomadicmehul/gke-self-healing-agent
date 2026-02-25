FROM python:3.11-slim

LABEL maintainer="Mehul Patel <mehul.patel@buildingminds.com>"
LABEL description="GKE Self-Healing Agent — autonomous Kubernetes remediation"

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose dashboard port
EXPOSE 8080

# Health check for the dashboard
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Run the agent
ENTRYPOINT ["python", "agent_workflow.py"]

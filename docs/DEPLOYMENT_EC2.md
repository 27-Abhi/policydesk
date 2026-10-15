# PolicyDesk AWS EC2 Free Tier Deployment Guide
**Target Architecture:** AWS EC2 `t3.micro` (or `t2.micro`) • 1 vCPU / 2 vCPUs • 1,024 MB RAM • 30 GB gp3 EBS  
**Cost:** $0.00 / month (100% AWS Free Tier Eligible)

---

## 1. AWS EC2 Instance Provisioning

1. Open the **AWS EC2 Management Console** and click **Launch Instance**.
2. **Name:** `policydesk-production`
3. **OS Image (AMI):** `Ubuntu Server 24.04 LTS` (or `Amazon Linux 2023`).
4. **Instance Type:**
   - **Recommended:** `t3.micro` (2 vCPUs, 1 GB RAM — Free tier eligible in most accounts).
   - **Alternative:** `t2.micro` (1 vCPU, 1 GB RAM).
   - **Credit Specification:** Leave as **Standard** (ensures no accidental burst charges).
5. **Key Pair:** Select your `.pem` key pair for SSH access.
6. **Network & Security Group Rules:**
   - **SSH (Port 22):** Source: `My IP` (Restricted).
   - **Customer Portal (Port 8000):** Source: `0.0.0.0/0` (Public internet).
   - **Compliance Portal (Port 8001):** Source: `My IP` (Restricted compliance operator access).
7. **Storage (Volume):** `30 GiB` `gp3` (AWS Free Tier provides up to 30 GB EBS).
8. Click **Launch Instance**.

---

## 2. Server Hardening & 2GB Swap Space

Connect to your EC2 instance via SSH:
```bash
ssh -i your-key.pem ubuntu@<ec2-public-ip>
```

### Configure 2GB Emergency Swap Space (Critical for 1 GB RAM Instances)
Because `t3.micro` has 1,024 MB RAM, running Docker + PostgreSQL + FastAPI under peak load can occasionally touch 1.1 GB. Swap memory prevents the Linux kernel from killing processes:

```bash
# 1. Allocate 2GB swapfile
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 2. Persist across reboots
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. Verify swap is active
free -h
```

---

## 3. Install Docker & Docker Compose

```bash
# Update packages
sudo apt-get update && sudo apt-get install -y ca-certificates curl

# Install Docker engine
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Allow non-root user to run Docker
sudo usermod -aG docker $USER
newgrp docker
```

---

## 4. Deploy PolicyDesk via Docker Compose

```bash
# Clone the repository
git clone https://github.com/<your-username>/policydesk.git
cd policydesk

# Configure production environment
cp .env.example .env
nano .env
```

Ensure `.env` contains:
```env
# Database Credentials
POSTGRES_PASSWORD=your_super_secret_production_password

# LLM Provider (Choose one):
# 1. OpenAI:
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini

# Or 2. Anthropic:
# ANTHROPIC_API_KEY=sk-ant-...

# Or 3. Fallback to zero-cost deterministic mock (leave keys blank)
```

### Launch the Production Stack:
```bash
# Build and start services in detached mode
docker compose up -d --build

# Inspect running containers
docker compose ps
```

Expected output:
```text
NAME             IMAGE                STATUS                    PORTS
policydesk_app   policydesk-app       Up (healthy)              0.0.0.0:8000->8000/tcp, 0.0.0.0:8001->8001/tcp
policydesk_db    postgres:16-alpine   Up (healthy)              5432/tcp (Internal only)
```

---

## 5. Verification & Live Health Checks

### Check Container Logs:
```bash
docker compose logs -f app
```

### Verify Endpoints:
```bash
# 1. Customer Health (Port 8000)
curl http://localhost:8000/health

# 2. Compliance Health (Port 8001)
curl http://localhost:8001/health

# 3. Test OpenAI Chat Completion
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "What is the domestic wire transfer fee?"}]}'
```

---

## 6. Maintenance & Operational Runbook

### Inspect Database Data:
```bash
# Connect to PostgreSQL container directly
docker compose exec db psql -U policydesk -d policydesk

# Check active accounts
SELECT account_number, customer_name, balance, is_locked FROM accounts;

# Check disputes
SELECT dispute_id, thread_id, transaction_id, status FROM disputes;
```

### Graceful Restart:
```bash
docker compose restart
```

### Update Deployment:
```bash
git pull origin main
docker compose up -d --build
```

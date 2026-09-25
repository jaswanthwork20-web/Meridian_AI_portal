# Meridian Enterprise AI Copilot & Employee Portal

End-to-End Enterprise Self-Help RAG Chatbot powered by **AWS Bedrock (ap-south-1 Mumbai)**, **Amazon OpenSearch Serverless**, and **Atlassian Jira / Confluence**.

---

## 🚀 Quick Setup & Run

### 1. Install Dependencies
```bash
pip install -r backend/requirements.txt
```
Use environment variables for credentials; never commit them to the repository:
```env
JIRA_INSTANCE_URL="https://your-instance.atlassian.net"
JIRA_EMAIL="your-email@example.com"
JIRA_API_TOKEN="your-jira-api-token"
JIRA_PROJECT_KEY=KAN
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=ap-south-1
AWS_S3_BUCKET_NAME=your-bucket
AWS_BEDROCK_KB_ID=your-knowledge-base-id
```

### 3. Start the Backend
Run this command from the repository root (`meridian_ai_portal_testing_latest`):
```bash
python -m uvicorn backend.backend_main:app --reload --port 8001
```

### 4. Start the Frontend
In a second terminal, from the repository root:
```bash
python -m http.server 8080 --directory frontend

```

Open **http://localhost:8080/login.html**. The frontend calls the backend at `http://localhost:8001` by default.

## CI/CD Deployment

The workflow is [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml). It runs syntax, lint, unused-code, type, test, security, secret, and dependency checks; builds both images; scans them; generates SBOMs; pushes them to ECR; deploys through AWS SSM; verifies health; and rolls back to the previous image tag when verification fails.

Configure these GitHub repository or environment variables:

```text
AWS_ACCOUNT_ID
AWS_REGION                 # defaults to ap-south-1
AWS_ROLE_ARN
EC2_INSTANCE_ID
ECR_FRONTEND_REPOSITORY   # defaults to meridian-frontend
ECR_BACKEND_REPOSITORY     # defaults to meridian-backend
PUBLIC_API_BASE            # public browser URL for the backend, e.g. https://api.example.com:8001
```

The EC2 instance must have Docker Compose, AWS CLI, SSM access, and an instance role with ECR pull permissions. Check out this repository at `/home/ssm-user/meridian-ai-portal`, keep the production `.env` there, and start from `docker-compose.yml`. The deployment sets `IMAGE_TAG` to the Git commit SHA.

For a manual deployment on EC2:

```bash
IMAGE_TAG=<commit-sha> docker compose pull
IMAGE_TAG=<commit-sha> docker compose up -d
```

---

## 👤 Login Credentials
* **Employee:** `maya.sharma@meridian.com` (or `maya`) &nbsp;|&nbsp; Password: `password123`
* **Customer:** `customer@meridian.com` (or `customer`) &nbsp;|&nbsp; Password: `password123`

---

## 💬 Testing the AWS Bedrock Chatbot

1. Log in as **Maya Sharma** and click the **Support & Knowledge Copilot** (or bottom-right floating **"Need Help?"** button).
2. Try asking:
   * *"My Zoom application keeps crashing when I open it, how do I fix this?"*
   * *"My microphone and camera are blocked in Windows 11, how do I enable permissions?"*
   * *"What is the annual leave and sick leave policy for employees?"*
3. **Verify:**
   * Live spinning wheel loader appears while generating response.
   * AI answer provides step-by-step guidance synthesized via AWS Bedrock (Claude 3 Haiku).
   * Verified Confluence sources with clickable **`View in Confluence ↗`** citation links.
   * Click **`Not Resolved (Route to Support)`** to automatically create a live Jira ticket in project `KAN`.

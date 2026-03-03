#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# llmch App Runner deployment script
#
# Prerequisites:
#   - AWS CLI configured (aws sts get-caller-identity)
#   - Docker running
#   - OPENAI_API_KEY set in environment
#
# Usage:
#   set -a && source .env && set +a
#   ./infra/deploy.sh
# ──────────────────────────────────────────────────────────────────────

STACK_NAME="${LLMCH_STACK_NAME:-llmch-demo}"
REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="${LLMCH_ECR_REPO:-llmch}"
IMAGE_TAG="${LLMCH_IMAGE_TAG:-latest}"
SSM_PREFIX="/${STACK_NAME}"

# ── Preflight: require OPENAI_API_KEY ────────────────────────────────

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "  ✘  OPENAI_API_KEY is not set." >&2
  echo "     Run: set -a && source .env && set +a" >&2
  exit 1
fi

echo ""
echo "  ── llmch deploy ──────────────────────────────────────────────"
echo ""
echo "  Stack:      ${STACK_NAME}"
echo "  Region:     ${REGION}"
echo "  ECR:        ${ECR_REPO}:${IMAGE_TAG}"
echo "  SSM prefix: ${SSM_PREFIX}"
echo ""

# ── 1. Store secrets in SSM Parameter Store ──────────────────────────

echo "  [1/5] Storing secrets in SSM Parameter Store..."

aws ssm put-parameter \
  --name "${SSM_PREFIX}/openai-api-key" \
  --value "${OPENAI_API_KEY}" \
  --type SecureString \
  --overwrite \
  --region "${REGION}" >/dev/null

# GitHub token — store DISABLED if empty (SSM requires non-empty value)
_TOKEN="${LLMCH_DEMO_REMOTE_TOKEN:-DISABLED}"
aws ssm put-parameter \
  --name "${SSM_PREFIX}/demo-remote-token" \
  --value "${_TOKEN}" \
  --type SecureString \
  --overwrite \
  --region "${REGION}" >/dev/null

echo "  ✓  SSM parameters stored (values not printed)"

# ── 2. Get AWS account ID and ECR login ──────────────────────────────

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"

echo "  [2/5] Logging in to ECR..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Create ECR repo if it doesn't exist
aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${ECR_REPO}" --region "${REGION}" >/dev/null

# ── 3. Build and push Docker image ──────────────────────────────────

echo "  [3/5] Building Docker image (linux/amd64)..."
docker build --platform=linux/amd64 -t "${ECR_REPO}:${IMAGE_TAG}" .

echo "  [4/5] Pushing to ECR..."
docker tag "${ECR_REPO}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:${IMAGE_TAG}"

# ── 4. Deploy CloudFormation stack ───────────────────────────────────
# No secrets on the command line — only the SSM prefix and non-secret config.

echo "  [5/5] Deploying CloudFormation stack..."
aws cloudformation deploy \
  --template-file infra/apprunner.cfn.yaml \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "ImageUri=${ECR_URI}:${IMAGE_TAG}" \
    "SsmPrefix=${SSM_PREFIX}" \
    "DemoRemoteUrl=${LLMCH_DEMO_REMOTE_URL:-https://github.com/teerev/llmch-demo.git}" \
    "RateLimitPerIp=${LLMCH_RATE_LIMIT_PER_IP:-3}" \
    "RateLimitGlobal=${LLMCH_RATE_LIMIT_GLOBAL:-100}"

# ── 5. Print service URL ────────────────────────────────────────────

SERVICE_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" \
  --output text)

echo ""
echo "  ✔  Deployed!"
echo "  URL: ${SERVICE_URL}"
echo ""

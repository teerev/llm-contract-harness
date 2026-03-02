#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# llmch App Runner deployment script
#
# Prerequisites:
#   - AWS CLI configured (aws sts get-caller-identity)
#   - Docker running
#   - ECR repository created (see below)
#
# Usage:
#   export OPENAI_API_KEY="sk-..."
#   export LLMCH_DEMO_REMOTE_TOKEN="ghp_..."   # optional
#   ./infra/deploy.sh
# ──────────────────────────────────────────────────────────────────────

STACK_NAME="${LLMCH_STACK_NAME:-llmch-demo}"
REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="${LLMCH_ECR_REPO:-llmch}"
IMAGE_TAG="${LLMCH_IMAGE_TAG:-latest}"

echo ""
echo "  ── llmch deploy ──────────────────────────────────────────────"
echo ""
echo "  Stack:  ${STACK_NAME}"
echo "  Region: ${REGION}"
echo "  ECR:    ${ECR_REPO}:${IMAGE_TAG}"
echo ""

# ── 1. Get AWS account ID and ECR login ──────────────────────────────

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"

echo "  [1/4] Logging in to ECR..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Create ECR repo if it doesn't exist
aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${ECR_REPO}" --region "${REGION}" >/dev/null

# ── 2. Build and push Docker image ──────────────────────────────────

echo "  [2/4] Building Docker image..."
docker build -t "${ECR_REPO}:${IMAGE_TAG}" .

echo "  [3/4] Pushing to ECR..."
docker tag "${ECR_REPO}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:${IMAGE_TAG}"

# ── 3. Deploy CloudFormation stack ───────────────────────────────────

echo "  [4/4] Deploying CloudFormation stack..."
aws cloudformation deploy \
  --template-file infra/apprunner.cfn.yaml \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "ImageUri=${ECR_URI}:${IMAGE_TAG}" \
    "OpenAIApiKey=${OPENAI_API_KEY}" \
    "DemoRemoteUrl=${LLMCH_DEMO_REMOTE_URL:-https://github.com/teerev/llmch-demo.git}" \
    "DemoRemoteToken=${LLMCH_DEMO_REMOTE_TOKEN:-}" \
    "RateLimitPerIp=${LLMCH_RATE_LIMIT_PER_IP:-3}" \
    "RateLimitGlobal=${LLMCH_RATE_LIMIT_GLOBAL:-100}"

# ── 4. Print service URL ────────────────────────────────────────────

SERVICE_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" \
  --output text)

echo ""
echo "  ✔  Deployed!"
echo "  URL: ${SERVICE_URL}"
echo ""

#!/usr/bin/env bash
set -euo pipefail

# Usage: ./run_work_orders.sh --wo-dir DIR --target-repo DIR --artifacts-dir DIR [options]
#
# Required:
#   --wo-dir          Directory containing WO-*.json files
#   --target-repo     Product repo path
#   --artifacts-dir   Artifact / output directory
#
# Optional:
#   --model           LLM model name           (default: gpt-5.2)
#   --max-attempts    Max attempts per WO      (default: 5)
#   --no-init         Skip repo wipe/init (use existing repo as-is)

# --- Defaults ---
WO_DIR=""
TARGET_REPO=""
ARTIFACTS_DIR=""
MODEL="gpt-5.2"
MAX_ATTEMPTS=5
INIT_REPO=true

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wo-dir)        WO_DIR="$2";       shift 2 ;;
    --target-repo)   TARGET_REPO="$2";  shift 2 ;;
    --artifacts-dir) ARTIFACTS_DIR="$2"; shift 2 ;;
    --model)         MODEL="$2";         shift 2 ;;
    --max-attempts)  MAX_ATTEMPTS="$2";  shift 2 ;;
    --no-init)       INIT_REPO=false;    shift   ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      echo "ERROR: Unexpected argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ -z "$WO_DIR" ] || [ -z "$TARGET_REPO" ] || [ -z "$ARTIFACTS_DIR" ]; then
  echo "Usage: $0 --wo-dir DIR --target-repo DIR --artifacts-dir DIR [--model NAME] [--max-attempts N] [--no-init]" >&2
  exit 1
fi

if [ ! -d "$WO_DIR" ]; then
  echo "ERROR: Work-order directory does not exist: $WO_DIR" >&2
  exit 1
fi

# Collect and sort WO files
WO_FILES=()
# Bash 3.2 (macOS default) does not support `mapfile`, so build the array manually.
shopt -s nullglob
while IFS= read -r wo; do
  WO_FILES+=("$wo")
done < <(printf '%s\n' "$WO_DIR"/WO-*.json | sort)
shopt -u nullglob

if [ ${#WO_FILES[@]} -eq 0 ]; then
  echo "ERROR: No WO-*.json files found in $WO_DIR" >&2
  exit 1
fi

echo "Found ${#WO_FILES[@]} work order(s) in $WO_DIR"
echo "Target repo:  $TARGET_REPO"
echo "Artifacts:    $ARTIFACTS_DIR"
echo "Model:        $MODEL"
echo "Max attempts: $MAX_ATTEMPTS"
echo ""

# Init the product repo (wipe and recreate to guarantee clean state)
if [ "$INIT_REPO" = true ]; then
  if [ -d "$TARGET_REPO" ]; then
    echo "Removing existing repo at $TARGET_REPO..."
    rm -rf "$TARGET_REPO"
  fi
  mkdir -p "$TARGET_REPO"
  cd "$TARGET_REPO"
  git init
  # Set local identity so commits work in a fresh repo without global config
  git config user.email "factory@aos.local"
  git config user.name "AOS Factory"
  # Seed with a trivial file + .gitignore so the repo has a real initial commit
  cat > README.md <<'SEED'
# Product Repo

This repository was initialized by `run_work_orders.sh`.
SEED
  cat > .gitignore <<'SEED'
__pycache__/
.pytest_cache/
*.pyc
SEED
  git add -A
  git commit -m "init: seed repo with README and .gitignore"
  cd -
  echo "Repo initialized at $TARGET_REPO"
  echo ""
fi

# Generate a stable session branch name (one branch for all WOs in this batch)
SESSION_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(head -c4 /dev/urandom | xxd -p)"
SESSION_BRANCH="factory/batch/${SESSION_ID}"
echo "Session branch: $SESSION_BRANCH"
echo ""

PASSED=0
FAILED=0

for WO in "${WO_FILES[@]}"; do
  WO_NAME="$(basename "$WO" .json)"
  echo ""
  echo "========================================"
  echo "  Running $WO_NAME"
  echo "========================================"
  echo ""

  if python -m factory run \
    --repo "$TARGET_REPO" \
    --work-order "$WO" \
    --artifacts-dir "$ARTIFACTS_DIR" \
    --llm-model "$MODEL" \
    --max-attempts "$MAX_ATTEMPTS" \
    --branch "$SESSION_BRANCH" \
    --no-push \
    --allow-verify-exempt; then

    echo ""
    echo "$WO_NAME PASSED"
    PASSED=$((PASSED + 1))
  else
    echo ""
    echo "$WO_NAME FAILED — stopping."
    FAILED=$((FAILED + 1))
    break
  fi
done

echo ""
echo "========================================"
echo "  Results: $PASSED passed, $FAILED failed (${#WO_FILES[@]} total)"
echo "  Branch:  $SESSION_BRANCH"
echo "========================================"

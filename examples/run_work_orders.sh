#!/usr/bin/env bash
set -euo pipefail

# Usage: ./run_work_orders.sh <work-order-dir> [options]
#   <work-order-dir>  Directory containing WO-*.json files (required)
#   --repo            Product repo path       (default: /tmp/product_repo)
#   --out             Artifact output dir      (default: ./artifacts)
#   --model           LLM model name           (default: gpt-4o)
#   --max-attempts    Max attempts per WO      (default: 5)
#   --no-init         Skip repo wipe/init (use existing repo as-is)

# --- Defaults ---
REPO="/Users/user/repos/worldsim5"
OUT_DIR="/Users/user/repos/aos/examples/artifacts"
MODEL="gpt-4o"
MAX_ATTEMPTS=5
INIT_REPO=true

# --- Parse arguments ---
WO_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)         REPO="$2";         shift 2 ;;
    --out)          OUT_DIR="$2";      shift 2 ;;
    --model)        MODEL="$2";        shift 2 ;;
    --max-attempts) MAX_ATTEMPTS="$2"; shift 2 ;;
    --no-init)      INIT_REPO=false;   shift   ;;
    -*)
      echo "ERROR: Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [ -z "$WO_DIR" ]; then
        WO_DIR="$1"; shift
      else
        echo "ERROR: Unexpected argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

if [ -z "$WO_DIR" ]; then
  echo "Usage: $0 <work-order-dir> [--repo DIR] [--out DIR] [--model NAME] [--max-attempts N] [--no-init]" >&2
  exit 1
fi

if [ ! -d "$WO_DIR" ]; then
  echo "ERROR: Work-order directory does not exist: $WO_DIR" >&2
  exit 1
fi

# Collect and sort WO files
mapfile -t WO_FILES < <(find "$WO_DIR" -maxdepth 1 -name 'WO-*.json' | sort)

if [ ${#WO_FILES[@]} -eq 0 ]; then
  echo "ERROR: No WO-*.json files found in $WO_DIR" >&2
  exit 1
fi

echo "Found ${#WO_FILES[@]} work order(s) in $WO_DIR"
echo "Repo:         $REPO"
echo "Output:       $OUT_DIR"
echo "Model:        $MODEL"
echo "Max attempts: $MAX_ATTEMPTS"
echo ""

# Init the product repo (wipe and recreate to guarantee clean state)
if [ "$INIT_REPO" = true ]; then
  if [ -d "$REPO" ]; then
    rm -rf "$REPO"
  fi
  mkdir -p "$REPO"
  cd "$REPO"
  git init
  echo '__pycache__/' > .gitignore
  echo '.pytest_cache/' >> .gitignore
  git add -A
  git commit --allow-empty -m "init"
  cd -
fi

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
    --repo "$REPO" \
    --work-order "$WO" \
    --out "$OUT_DIR" \
    --llm-model "$MODEL" \
    --max-attempts "$MAX_ATTEMPTS"; then

    echo ""
    echo "$WO_NAME PASSED — committing..."
    cd "$REPO"
    git add -A
    git commit -m "$WO_NAME"
    cd -
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
echo "========================================"

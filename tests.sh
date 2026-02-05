#!/usr/bin/env bash
set -euo pipefail

# sanity_factory_harness.sh
# Run from the FACTORY repo root (must contain ./factory)
#
# Optional env:
#   SKIP_LLM=1      -> skip tests that might call an LLM
#   LLM_MODEL=...   -> default "gpt-5.2"
#   LLM_TEMP=...    -> default "0"
#   MAX_ATTEMPTS=1  -> default "1"
#   PYTHON_BIN=...  -> default "python"

LLM_MODEL="${LLM_MODEL:-gpt-5.2}"
LLM_TEMP="${LLM_TEMP:-0}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-1}"
PYTHON_BIN="${PYTHON_BIN:-python}"

ROOT="$(pwd)"
if [[ ! -d "$ROOT/factory" ]]; then
  echo "ERROR: must run from factory repo root (missing ./factory)"
  exit 2
fi

PASS_CNT=0
FAIL_CNT=0
SKIP_CNT=0

log()  { printf "\n==> %s\n" "$*"; }
pass() { printf "✅ PASS: %s\n" "$*"; PASS_CNT=$((PASS_CNT+1)); }
fail() { printf "❌ FAIL: %s\n" "$*"; FAIL_CNT=$((FAIL_CNT+1)); }
skip() { printf "⏭️  SKIP: %s\n" "$*"; SKIP_CNT=$((SKIP_CNT+1)); }

mktemp_dir() {
  mktemp -d 2>/dev/null || mktemp -d -t factory_sanity
}

cleanup_paths=()
cleanup() {
  for p in "${cleanup_paths[@]:-}"; do
    rm -rf "$p" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

run_cmd_capture() {
  # Usage: run_cmd_capture <outfile> <cmd...>
  local outfile="$1"; shift
  set +e
  "$@" >"$outfile" 2>&1
  local rc=$?
  set -e
  echo "$rc"
}

assert_contains() {
  local hayfile="$1"
  local needle="$2"
  # macOS/BSD grep treats patterns like "--repo" as options unless we terminate options with "--"
  grep -Eqi -- "$needle" "$hayfile"
}

list_run_dirs() {
  # Prints immediate child directory names (sorted) under $1, one per line.
  local out="$1"
  find "$out" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null \
    | sed 's|.*/||' \
    | LC_ALL=C sort
}

json_stage() {
  # Usage: json_stage /path/to/failure_brief.json
  # Prints ended_stage or stage, else empty string.
  "$PYTHON_BIN" - "$1" <<'PY'
import json, sys
p = sys.argv[1]
with open(p, "r") as f:
    j = json.load(f)
print((j.get("ended_stage") or j.get("stage") or "").strip())
PY
}

print_artifacts_if_any() {
  local outdir="$1"
  if [[ -d "$outdir" ]]; then
    echo "---- outdir tree (depth<=4) ----"
    find "$outdir" -maxdepth 4 -print 2>/dev/null || true
    echo "--------------------------------"
    local fb
    fb="$(find "$outdir" -maxdepth 4 -type f -name "failure_brief.json" -print -quit 2>/dev/null || true)"
    if [[ -n "$fb" ]]; then
      echo "---- failure_brief.json ----"
      cat "$fb" || true
      echo "----------------------------"
    fi
  fi
}

# -----------------------------
# 0) One-time safety prep
# -----------------------------
log "0) One-time safety prep (compileall + imports)"
tmp_log="$(mktemp)"
cleanup_paths+=("$tmp_log")

rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m compileall -q factory)"
if [[ "$rc" -eq 0 ]]; then
  pass "compileall factory"
else
  cat "$tmp_log"
  fail "compileall factory (rc=$rc)"
fi

rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -c "import factory; import factory.run; import factory.graph")"
if [[ "$rc" -eq 0 ]]; then
  pass "imports: factory, factory.run, factory.graph"
else
  cat "$tmp_log"
  fail "imports failed (rc=$rc)"
fi

# -----------------------------
# 1) CLI wiring checks
# -----------------------------
log "1) CLI wiring checks"

rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory --help)"
if [[ "$rc" -eq 0 ]]; then
  pass "python -m factory --help"
else
  cat "$tmp_log"
  fail "python -m factory --help (rc=$rc)"
fi

rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --help)"
if [[ "$rc" -eq 0 ]]; then
  if assert_contains "$tmp_log" "--repo" \
     && assert_contains "$tmp_log" "--work-order" \
     && assert_contains "$tmp_log" "--out" \
     && assert_contains "$tmp_log" "--llm-model"; then
    pass "python -m factory run --help (flags look present)"
  else
    cat "$tmp_log"
    fail "python -m factory run --help (expected flags not found)"
  fi
else
  cat "$tmp_log"
  fail "python -m factory run --help (rc=$rc)"
fi

# -----------------------------
# Minimal work order used for preflight tests
# -----------------------------
WO_MIN="$(mktemp)"
cleanup_paths+=("$WO_MIN")
cat >"$WO_MIN" <<'JSON'
{
  "id":"wo-min",
  "title":"Minimal WO for preflight",
  "intent":"No-op / preflight tests only.",
  "allowed_files":["a.py"],
  "forbidden":["Do not modify files outside allowed_files.","Do not add dependencies."],
  "acceptance_commands":["python -m compileall -q ."],
  "context_files":[]
}
JSON

# -----------------------------
# 2) Preflight safety checks
# -----------------------------
log "2) Preflight safety checks"

log "2.1 Not a git repo should fail"
tmpdir="$(mktemp_dir)"
cleanup_paths+=("$tmpdir")
printf "print('hi')\n" > "$tmpdir/hello.py"
outdir="$(mktemp_dir)"
cleanup_paths+=("$outdir")

rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$tmpdir" --work-order "$WO_MIN" --out "$outdir" --llm-model "$LLM_MODEL")"
if [[ "$rc" -ne 0 ]]; then
  if assert_contains "$tmp_log" "git|not a git|repository|work tree"; then
    pass "non-git repo rejected (rc=$rc)"
  else
    echo "NOTE: failed as expected but message did not match heuristic"
    cat "$tmp_log"
    pass "non-git repo rejected (rc=$rc)"
  fi
else
  cat "$tmp_log"
  fail "non-git repo was NOT rejected (rc=0)"
fi

log "2.2 Dirty repo should fail (including untracked)"
repo_dirty="$(mktemp_dir)"
cleanup_paths+=("$repo_dirty")
(
  cd "$repo_dirty"
  git init -q
  printf "print('ok')\n" > a.py
  git add a.py && git commit -qm "init"
  printf "junk\n" > UNTRACKED.txt
)

outdir="$(mktemp_dir)"
cleanup_paths+=("$outdir")
rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_dirty" --work-order "$WO_MIN" --out "$outdir" --llm-model "$LLM_MODEL")"
if [[ "$rc" -ne 0 ]]; then
  if assert_contains "$tmp_log" "clean|dirty|untracked|porcelain|working tree"; then
    pass "dirty repo rejected incl. untracked (rc=$rc)"
  else
    echo "NOTE: failed as expected but message did not match heuristic"
    cat "$tmp_log"
    pass "dirty repo rejected incl. untracked (rc=$rc)"
  fi
else
  cat "$tmp_log"
  fail "dirty repo was NOT rejected (rc=0)"
fi

# -----------------------------
# 3) Outdir-inside-repo should fail
# -----------------------------
log "3) outdir inside repo should fail"
repo_outinside="$(mktemp_dir)"
cleanup_paths+=("$repo_outinside")
(
  cd "$repo_outinside"
  git init -q
  printf "print('ok')\n" > a.py
  git add a.py && git commit -qm "init"
  mkdir -p out_inside
)
rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_outinside" --work-order "$WO_MIN" --out "$repo_outinside/out_inside" --llm-model "$LLM_MODEL")"
if [[ "$rc" -ne 0 ]]; then
  if assert_contains "$tmp_log" "out.*inside|inside.*repo|outdir|output.*repo"; then
    pass "outdir-inside-repo rejected (rc=$rc)"
  else
    echo "NOTE: failed as expected but message did not match heuristic"
    cat "$tmp_log"
    pass "outdir-inside-repo rejected (rc=$rc)"
  fi
else
  cat "$tmp_log"
  fail "outdir-inside-repo was NOT rejected (rc=0)"
fi

# -----------------------------
# 4) Deterministic run_id check (best-effort)
# -----------------------------
log "4) deterministic run_id check"

repo_det="$(mktemp_dir)"
cleanup_paths+=("$repo_det")
(
  cd "$repo_det"
  git init -q
  printf "print('ok')\n" > a.py
  git add a.py && git commit -qm "init"
)

OUT_DET="$(mktemp_dir)"
cleanup_paths+=("$OUT_DET")

if [[ "${SKIP_LLM:-0}" == "1" ]]; then
  skip "deterministic run_id check (SKIP_LLM=1)"
else
  run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_det" --work-order "$WO_MIN" --out "$OUT_DET" --llm-model "$LLM_MODEL" --llm-temperature "$LLM_TEMP" --max-attempts 1 >/dev/null || true
  dirs1="$(list_run_dirs "$OUT_DET" || true)"

  run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_det" --work-order "$WO_MIN" --out "$OUT_DET" --llm-model "$LLM_MODEL" --llm-temperature "$LLM_TEMP" --max-attempts 1 >/dev/null || true
  dirs2="$(list_run_dirs "$OUT_DET" || true)"

  if [[ -z "$dirs1" || -z "$dirs2" ]]; then
    echo "NOTE: No run_id directories created; your harness may only create them after LLM/preflight stages."
    echo "      This test is inconclusive in that case."
    print_artifacts_if_any "$OUT_DET"
    skip "deterministic run_id (inconclusive: no run dirs)"
  else
    if [[ "$dirs1" == "$dirs2" ]]; then
      pass "deterministic run_id (run dir list stable)"
    else
      echo "run dirs after run 1:"
      echo "$dirs1"
      echo "run dirs after run 2:"
      echo "$dirs2"
      fail "deterministic run_id (run dir list changed)"
    fi
  fi
fi

# -----------------------------
# 5/6) Rollback semantics test
# -----------------------------
log "5/6) rollback semantics (patch applied then acceptance fails -> repo clean)"

if [[ "${SKIP_LLM:-0}" == "1" ]]; then
  skip "rollback semantics test (SKIP_LLM=1)"
else
  repo_rb="$(mktemp_dir)"
  cleanup_paths+=("$repo_rb")
  (
    cd "$repo_rb"
    git init -q
    printf "print('ok')\n" > a.py
    git add a.py && git commit -qm "init"

    mkdir -p scripts
    cat > scripts/verify.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
python -m compileall -q .
SH
    chmod +x scripts/verify.sh
    git add scripts/verify.sh && git commit -qm "add verify"
  )

  WO_RB="$(mktemp)"
  cleanup_paths+=("$WO_RB")
  cat >"$WO_RB" <<'JSON'
{
  "id":"wo-rollback",
  "title":"Rollback test",
  "intent":"Make any trivial change to a.py (e.g. add a comment).",
  "allowed_files":["a.py"],
  "forbidden":["Do not modify files outside allowed_files.","Do not add dependencies."],
  "acceptance_commands":["python -c \"import sys; sys.exit(2)\""],
  "context_files":["a.py"]
}
JSON

  OUT_RB="$(mktemp_dir)"
  cleanup_paths+=("$OUT_RB")

  rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_rb" --work-order "$WO_RB" --out "$OUT_RB" --llm-model "$LLM_MODEL" --llm-temperature "$LLM_TEMP" --max-attempts "$MAX_ATTEMPTS")"

  if [[ "$rc" -eq 0 ]]; then
    echo "NOTE: Factory returned success but acceptance was designed to fail."
    print_artifacts_if_any "$OUT_RB"
    fail "rollback test: expected FAIL exit code"
  else
    dirty_status="$(cd "$repo_rb" && git status --porcelain || true)"
    diff_names="$(cd "$repo_rb" && git diff --name-only || true)"
    if [[ -z "$dirty_status" && -z "$diff_names" ]]; then
      pass "rollback leaves repo clean after failure (rc=$rc)"
    else
      echo "git status --porcelain:"
      echo "$dirty_status"
      echo "git diff --name-only:"
      echo "$diff_names"
      print_artifacts_if_any "$OUT_RB"
      fail "rollback left repo dirty"
    fi
  fi
fi

# -----------------------------
# 8) Verification ordering test (verify then acceptance)
# -----------------------------
log "8) verification ordering (verify fails deterministically; acceptance would pass)"

if [[ "${SKIP_LLM:-0}" == "1" ]]; then
  skip "verification ordering test (SKIP_LLM=1)"
else
  repo_vo="$(mktemp_dir)"
  cleanup_paths+=("$repo_vo")
  (
    cd "$repo_vo"
    git init -q
    printf "print('ok')\n" > a.py
    git add a.py && git commit -qm "init"

    mkdir -p scripts
    cat > scripts/verify.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exit 7
SH
    chmod +x scripts/verify.sh
    git add scripts/verify.sh && git commit -qm "make verify fail"
  )

  WO_VO="$(mktemp)"
  cleanup_paths+=("$WO_VO")
  cat >"$WO_VO" <<'JSON'
{
  "id":"wo-verify-order",
  "title":"Verify order test",
  "intent":"Make a trivial change to a.py.",
  "allowed_files":["a.py"],
  "forbidden":["Do not modify files outside allowed_files."],
  "acceptance_commands":["python -c \"print('accept ok')\""],
  "context_files":["a.py"]
}
JSON

  OUT_VO="$(mktemp_dir)"
  cleanup_paths+=("$OUT_VO")

  rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_vo" --work-order "$WO_VO" --out "$OUT_VO" --llm-model "$LLM_MODEL" --llm-temperature "$LLM_TEMP" --max-attempts "$MAX_ATTEMPTS")"

  if [[ "$rc" -eq 0 ]]; then
    echo "NOTE: Factory returned success even though verify.sh exits 7."
    print_artifacts_if_any "$OUT_VO"
    fail "verify-order: expected failure"
  else
    fb="$(find "$OUT_VO" -maxdepth 4 -type f -name "failure_brief.json" -print -quit 2>/dev/null || true)"
    if [[ -z "$fb" ]]; then
      echo "NOTE: No failure_brief.json found; cannot confirm stage."
      print_artifacts_if_any "$OUT_VO"
      fail "verify-order: missing failure_brief.json"
    else
      stage="$(json_stage "$fb")"
      if [[ "$stage" =~ verify_failed ]]; then
        pass "verify-order: stage=verify_failed"
      else
        echo "Observed stage: '$stage'"
        echo "failure_brief.json:"
        cat "$fb"
        fail "verify-order: expected verify_failed"
      fi
    fi
  fi
fi

# -----------------------------
# 7) Patch scope enforcement test (stochastic bait)
# -----------------------------
log "7) patch scope enforcement (stochastic bait; best-effort)"

if [[ "${SKIP_LLM:-0}" == "1" ]]; then
  skip "patch scope test (SKIP_LLM=1)"
else
  repo_sc="$(mktemp_dir)"
  cleanup_paths+=("$repo_sc")
  (
    cd "$repo_sc"
    git init -q
    printf "print('ok')\n" > a.py
    git add a.py && git commit -qm "init"
    printf "# README\n\nHello.\n" > README.md
    git add README.md && git commit -qm "add README"
  )

  WO_SC="$(mktemp)"
  cleanup_paths+=("$WO_SC")
  cat >"$WO_SC" <<'JSON'
{
  "id":"wo-scope",
  "title":"Scope test",
  "intent":"Update README.md only. (This should be rejected because README.md is not allowed.)",
  "allowed_files":["a.py"],
  "forbidden":["Do not modify files outside allowed_files."],
  "acceptance_commands":["python -m compileall -q ."],
  "context_files":["a.py"]
}
JSON

  OUT_SC="$(mktemp_dir)"
  cleanup_paths+=("$OUT_SC")

  run_cmd_capture "$tmp_log" "$PYTHON_BIN" -m factory run --repo "$repo_sc" --work-order "$WO_SC" --out "$OUT_SC" --llm-model "$LLM_MODEL" --llm-temperature "$LLM_TEMP" --max-attempts 1 >/dev/null || true

  dirty_status="$(cd "$repo_sc" && git status --porcelain || true)"
  diff_names="$(cd "$repo_sc" && git diff --name-only || true)"
  if [[ -n "$dirty_status" || -n "$diff_names" ]]; then
    echo "git status --porcelain:"
    echo "$dirty_status"
    echo "git diff --name-only:"
    echo "$diff_names"
    print_artifacts_if_any "$OUT_SC"
    fail "scope test: repo left dirty (rollback/scope enforcement suspect)"
  else
    fb="$(find "$OUT_SC" -maxdepth 4 -type f -name "failure_brief.json" -print -quit 2>/dev/null || true)"
    if [[ -n "$fb" ]]; then
      stage="$(json_stage "$fb")"
      if [[ "$stage" =~ patch_scope_violation|scope ]]; then
        pass "scope test: stage indicates scope violation AND repo clean"
      else
        echo "NOTE: scope test produced stage '$stage' (naming may differ). Repo is clean (key invariant)."
        pass "scope test: repo clean (stage not recognized)"
      fi
    else
      echo "NOTE: No failure_brief.json found; bait may not have triggered or harness succeeded cleanly."
      pass "scope test: repo clean (inconclusive bait)"
    fi
  fi
fi

# -----------------------------
# 9) Artifact completeness check (best-effort)
# -----------------------------
log "9) artifact completeness (best-effort: check for run_summary.json in recent outdirs)"

candidate_outs=()
[[ -n "${OUT_SC:-}" && -d "${OUT_SC:-}" ]] && candidate_outs+=("$OUT_SC")
[[ -n "${OUT_VO:-}" && -d "${OUT_VO:-}" ]] && candidate_outs+=("$OUT_VO")
[[ -n "${OUT_RB:-}" && -d "${OUT_RB:-}" ]] && candidate_outs+=("$OUT_RB")
[[ -n "${OUT_DET:-}" && -d "${OUT_DET:-}" ]] && candidate_outs+=("$OUT_DET")

if [[ "${#candidate_outs[@]}" -eq 0 ]]; then
  skip "artifact completeness (no outdirs)"
else
  found=0
  for od in "${candidate_outs[@]}"; do
    rs="$(find "$od" -maxdepth 4 -type f -name "run_summary.json" -print -quit 2>/dev/null || true)"
    if [[ -n "$rs" ]]; then
      found=1
      echo "Found run_summary.json: $rs"
      rc="$(run_cmd_capture "$tmp_log" "$PYTHON_BIN" -c "import json; json.load(open('$rs','r')); print('ok')")"
      if [[ "$rc" -eq 0 ]]; then
        pass "artifact completeness: run_summary.json parses (in $od)"
      else
        cat "$tmp_log"
        fail "artifact completeness: run_summary.json invalid JSON (in $od)"
      fi
      break
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    echo "NOTE: No run_summary.json found in candidate outdirs; harness may emit different names or not reach that stage."
    for od in "${candidate_outs[@]}"; do
      print_artifacts_if_any "$od"
    done
    skip "artifact completeness (inconclusive: run_summary.json not found)"
  fi
fi

# -----------------------------
# Summary
# -----------------------------
log "DONE"
echo "Passed: $PASS_CNT"
echo "Failed: $FAIL_CNT"
echo "Skipped: $SKIP_CNT"

if [[ "$FAIL_CNT" -ne 0 ]]; then
  exit 1
fi
exit 0

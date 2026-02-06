#!/usr/bin/env python3
"""Deterministic work-order plan-quality scorer.

Reads work-order JSON files from configured directories, computes metrics,
and prints a human-readable terminal summary followed by a machine-readable
JSON blob separated by a ---JSON--- delimiter.
"""

import json
import math
import os
import re
import statistics
from typing import Any

# ── Configuration ──────────────────────────────────────────────────────────
WO_DIRS: list[str] = ["./wo", "./wo2", "./wo3", "./wo4"]

# ── Layer classification ──────────────────────────────────────────────────

def classify_layer(path: str) -> str:
    p = path.lower()
    base = os.path.basename(p)
    if "cli" in p or base == "cli.py":
        return "cli"
    if "types" in p or base == "types.py":
        return "types"
    if any(k in p for k in ("world", "sim", "engine", "core")):
        return "engine"
    if "rule" in p or "rules" in p:
        return "rules"
    if any(k in p for k in ("render", "viz", "view")):
        return "render"
    if any(k in p for k in ("web", "server", "http", "websocket", "ws", "socket", "canvas")):
        return "web"
    if any(k in p for k in ("io", "load", "save", "checkpoint", "persist", "serialize")):
        return "io"
    if "test" in p or "tests" in p:
        return "tests"
    if "readme" in p or "docs" in p:
        return "docs"
    return "misc"

# ── Helpers ───────────────────────────────────────────────────────────────

def _sort_key(wo: dict) -> tuple:
    """Sort by numeric suffix of id then filename."""
    wo_id = wo.get("id", "")
    m = re.search(r"(\d+)$", wo_id.split("-")[-1] if "-" in wo_id else wo_id)
    num = int(m.group(1)) if m else 10**9
    return (num, wo.get("_filename", ""))


def _lower_join(*fields: str) -> str:
    return " ".join((f or "") for f in fields).lower()


def _is_existence_only_assert(cmd: str) -> bool:
    """Check if a command containing 'assert' is existence-only."""
    cl = cmd.lower()
    if "assert" not in cl:
        return False
    existence_markers = ("isfile", "exists", "not created", "created")
    non_trivial = ("==", "!=", "json", "csv", "image", "split(", "len(", "sum(", "tobytes")
    if any(m in cl for m in existence_markers) and not any(m in cl for m in non_trivial):
        return True
    return False


def existence_only_acceptance(commands: list[str]) -> bool:
    """True if commands exist but none contain 'assert', or all asserts are existence-only."""
    if not commands:
        return False
    has_any_assert = any("assert" in c.lower() for c in commands)
    if not has_any_assert:
        return True
    # Check if ALL asserts are existence-only
    for c in commands:
        if "assert" in c.lower() and not _is_existence_only_assert(c):
            return False
    return True


# ── Per-WO metrics ────────────────────────────────────────────────────────

def acceptance_strength(commands: list[str]) -> int:
    score = 0
    joined = " ".join(commands).lower()
    if any(k in joined for k in ("verify.sh", "pytest", "python -m unittest", "ruff", "mypy")):
        score += 4
    if any(k in joined for k in ("hash", "sha", "md5", "tobytes", "determin", "seed", "same output")):
        score += 4
    # +4 if any command includes "assert" AND is not purely existence check
    for c in commands:
        cl = c.lower()
        if "assert" in cl and not _is_existence_only_assert(c):
            score += 4
            break
    if any(k in joined for k in ("invariant", "unique", "collision", "conservation")):
        score += 2
    if any(k in joined for k in ("golden", "snapshot", "expected", "compare")):
        score += 2
    return min(score, 10)


def surface_score(context_files: list[str]) -> int:
    s = len(context_files)
    layers = [classify_layer(f) for f in context_files]
    if "cli" in layers:
        s += 3
    if "types" in layers:
        s += 3
    if any(os.path.basename(f) == "__init__.py" for f in context_files):
        s += 2
    return s


def ambiguity_score(title: str, intent: str, notes: str, acc_str: int, exist_only: bool) -> int:
    s = 0
    if exist_only:
        s += 6
    if acc_str < 4:
        s += 3
    text = _lower_join(title, intent, notes)
    vague = [
        "robust", "nice", "clean", "serious", "good", "reasonable",
        "fast", "efficient", "well-architected", "simple", "lightweight",
    ]
    vague_count = 0
    for w in vague:
        if w in text:
            vague_count += 1
    s += min(vague_count, 5)
    return s


def novelty_score(title: str, intent: str, notes: str) -> int:
    text = _lower_join(title, intent, notes)
    s = 0
    groups = {
        "web": ["web", "browser", "http", "server", "websocket", "ws", "canvas"],
        "video": ["video", "ffmpeg", "encode", "mp4", "gif"],
        "persistence": ["checkpoint", "save", "load", "serialize", "persistence"],
        "gui": ["gui", "interactive", "controls", "scrub", "timeline"],
        "plugin_dsl": ["plugin", "rule set", "dsl", "grammar"],
    }
    for _gname, keywords in sorted(groups.items()):
        if any(k in text for k in keywords):
            s += 4
    s = min(s, 12)
    if any(k in text for k in ("pip install", "dependency", "dependencies")):
        s += 2
    return s


def coupling_score(
    wo: dict,
    prev_context_files: list[str] | None,
    layers_touched: list[str],
) -> int:
    s = 0
    if len(layers_touched) > 1:
        s += 1
    # Check forbidden references to allowed_files
    forbidden = wo.get("forbidden", [])
    allowed = wo.get("allowed_files", [])
    allowed_basenames = {os.path.basename(a): a for a in allowed}
    allowed_set = set(allowed)
    for f_str in forbidden:
        fl = f_str.lower()
        if "do not modify" in fl:
            # Extract the path after "do not modify"
            after = f_str.split("do not modify", 1)[-1] if "do not modify" in f_str.lower() else ""
            # Case-insensitive split
            idx = fl.find("do not modify")
            after = f_str[idx + len("do not modify"):].strip()
            for a in allowed:
                if a in after or os.path.basename(a) in after:
                    s += 2
                    break
            else:
                continue
            break
    # Overlap with previous WO context_files
    if prev_context_files is not None:
        current_cf = set(wo.get("context_files", []))
        if current_cf & set(prev_context_files):
            s += 1
    return s


def constraint_strength(wo: dict) -> int:
    s = 0
    forbidden = wo.get("forbidden", [])
    joined = " ".join(forbidden).lower()
    if "no external dependencies" in joined or "no new dependencies" in joined:
        s += 2
    if len(wo.get("context_files", [])) <= 3:
        s += 2
    return min(s, 5)


# ── Score a single WO ────────────────────────────────────────────────────

def score_wo(wo: dict, prev_context_files: list[str] | None) -> dict:
    ctx = wo.get("context_files", [])
    layers = sorted(set(classify_layer(f) for f in ctx))
    acc_cmds = wo.get("acceptance_commands", [])

    a_str = acceptance_strength(acc_cmds)
    exist_only = existence_only_acceptance(acc_cmds)
    s_score = surface_score(ctx)
    amb = ambiguity_score(
        wo.get("title", ""), wo.get("intent", ""), wo.get("notes", ""), a_str, exist_only,
    )
    nov = novelty_score(wo.get("title", ""), wo.get("intent", ""), wo.get("notes", ""))
    coup = coupling_score(wo, prev_context_files, layers)
    c_str = constraint_strength(wo)

    e_step = round(s_score + nov + amb + coup - a_str - c_str, 2)

    return {
        "id": wo.get("id", "?"),
        "layers_touched": layers,
        "acceptance_strength": a_str,
        "existence_only_acceptance": exist_only,
        "surface_score": s_score,
        "ambiguity_score": amb,
        "novelty_score": nov,
        "coupling_score": coup,
        "constraint_strength": c_str,
        "e_step": e_step,
    }


# ── Directory-level metrics ──────────────────────────────────────────────

def compute_directory_metrics(wo_list: list[dict], scored: list[dict]) -> dict:
    total = len(wo_list)
    if total == 0:
        return {}

    e_steps = [s["e_step"] for s in scored]
    median_e = round(statistics.median(e_steps), 2)
    max_e_idx = max(range(total), key=lambda i: (e_steps[i], scored[i]["id"]))
    max_e = e_steps[max_e_idx]
    max_e_id = scored[max_e_idx]["id"]

    # Layer violations
    layer_violations = sum(1 for s in scored if len(s["layers_touched"]) > 1)
    layer_score = round(1.0 - min(1.0, layer_violations / total), 4)

    # Meaningful acceptance rate
    meaningful = sum(1 for s in scored if s["acceptance_strength"] >= 6)
    meaningful_rate = round(meaningful / total, 4)

    # Core seam files + stability
    core_seam_files: set[str] = set()
    for wo in wo_list:
        for f in wo.get("context_files", []):
            if classify_layer(f) in ("types", "cli"):
                core_seam_files.add(f)

    # Freeze point: first WO index where ALL core seam files appeared in allowed_files at least once
    seen_seam: set[str] = set()
    freeze_point = 0
    if core_seam_files:
        all_found = False
        for i, wo in enumerate(wo_list):
            for f in wo.get("allowed_files", []):
                if f in core_seam_files:
                    seen_seam.add(f)
            if seen_seam >= core_seam_files and not all_found:
                freeze_point = i
                all_found = True
                break
        if not all_found:
            freeze_point = 0

    # CoreSeamChurn: WOs after freeze point that include any core seam file in context_files
    core_seam_churn = 0
    for i, wo in enumerate(wo_list):
        if i > freeze_point:
            ctx = set(wo.get("context_files", []))
            if ctx & core_seam_files:
                core_seam_churn += 1

    seam_stability = round(1.0 - min(1.0, core_seam_churn / max(1, len(core_seam_files))), 4)

    c_global = round(0.45 * seam_stability + 0.35 * layer_score + 0.20 * meaningful_rate, 4)

    # WO_expected
    unique_files: set[str] = set()
    all_layers: set[str] = set()
    for wo in wo_list:
        unique_files.update(wo.get("allowed_files", []))
        for f in wo.get("context_files", []):
            all_layers.add(classify_layer(f))
    wo_expected = round(0.8 * len(unique_files) + 2.5 * len(all_layers))

    # Verdict
    if median_e <= 8 and c_global >= 0.75:
        verdict = "good"
    elif median_e <= 10 and c_global >= 0.60:
        verdict = "borderline"
    else:
        verdict = "risky"

    # Warnings (deterministic, max 5)
    warnings: list[str] = []

    # High entropy WOs (descending E_step, first 3)
    high_entropy = sorted(
        [(s["e_step"], s["id"]) for s in scored if s["e_step"] > 12],
        key=lambda x: (-x[0], x[1]),
    )
    for e, wid in high_entropy[:3]:
        warnings.append(f"HighEntropyWO:{wid}")

    if meaningful_rate < 0.70:
        warnings.append("LowAcceptanceRate")
    if core_seam_churn > 0:
        warnings.append("SeamChurn")
    if layer_violations > 0:
        warnings.append("LayerViolations")

    # Existence-only acceptance (first 2 in WO order)
    exist_count = 0
    for s in scored:
        if s["existence_only_acceptance"] and exist_count < 2:
            warnings.append(f"ExistenceOnlyAcceptance:{s['id']}")
            exist_count += 1

    warnings = warnings[:5]

    return {
        "total_wos": total,
        "median_e_step": median_e,
        "max_e_step": max_e,
        "max_e_step_id": max_e_id,
        "c_global": c_global,
        "seam_stability": seam_stability,
        "layer_score": layer_score,
        "meaningful_acceptance_rate": meaningful_rate,
        "core_seam_churn": core_seam_churn,
        "core_seam_files_count": len(core_seam_files),
        "layer_violations": layer_violations,
        "wo_expected": wo_expected,
        "unique_files": len(unique_files),
        "subsystems": len(all_layers),
        "verdict": verdict,
        "warnings": warnings,
    }


# ── Load work orders from a directory ────────────────────────────────────

def load_work_orders(directory: str) -> tuple[list[dict], list[str]]:
    """Return (list_of_wo_dicts, list_of_errors)."""
    wos: list[dict] = []
    errors: list[str] = []
    if not os.path.isdir(directory):
        return wos, errors
    filenames = sorted(f for f in os.listdir(directory) if f.endswith(".json"))
    for fn in filenames:
        path = os.path.join(directory, fn)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                errors.append(f"{path}: not a JSON object")
                continue
            data["_filename"] = fn
            wos.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path}: {exc}")
    # Sort deterministically
    wos.sort(key=_sort_key)
    return wos, errors


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    all_scored_flat: list[dict] = []
    all_wo_flat: list[dict] = []
    report: dict[str, Any] = {"per_directory": {}, "overall": {}}
    lines: list[str] = []

    for d in sorted(WO_DIRS):
        if not os.path.isdir(d):
            lines.append(f"=== {d} === MISSING (skipped)")
            lines.append("")
            continue

        wos, errors = load_work_orders(d)
        if not wos:
            lines.append(f"=== {d} === no JSON work orders found")
            if errors:
                for e in errors:
                    lines.append(f"  ERROR: {e}")
            lines.append("")
            continue

        scored: list[dict] = []
        prev_cf: list[str] | None = None
        for wo in wos:
            s = score_wo(wo, prev_cf)
            scored.append(s)
            prev_cf = wo.get("context_files", [])

        metrics = compute_directory_metrics(wos, scored)

        all_scored_flat.extend(scored)
        all_wo_flat.extend(wos)

        lines.append(f"=== {d} === ({metrics['total_wos']} work orders)")
        lines.append(f"  Median E_step : {metrics['median_e_step']}")
        lines.append(f"  Max E_step    : {metrics['max_e_step']}  (WO {metrics['max_e_step_id']})")
        lines.append(f"  C_global      : {metrics['c_global']}")
        lines.append(f"  SeamStability : {metrics['seam_stability']}")
        lines.append(f"  LayerScore    : {metrics['layer_score']}")
        lines.append(f"  MeanAcceptRate: {metrics['meaningful_acceptance_rate']}")
        lines.append(f"  CoreSeamChurn : {metrics['core_seam_churn']}")
        lines.append(f"  LayerViolation: {metrics['layer_violations']}")
        lines.append(f"  WO_expected   : {metrics['wo_expected']}")
        lines.append(f"  Verdict       : {metrics['verdict']}")
        if metrics["warnings"]:
            lines.append(f"  Warnings      : {', '.join(metrics['warnings'])}")

        if errors:
            lines.append(f"  Parse errors  : {len(errors)}")
            for e in errors:
                lines.append(f"    {e}")

        lines.append("")

        # Per-WO breakdown
        lines.append(f"  {'ID':<10} {'E_step':>7} {'Surf':>5} {'Nov':>4} {'Amb':>4} {'Coup':>5} {'Acc':>4} {'Con':>4} {'Layers'}")
        lines.append(f"  {'-'*10} {'-'*7} {'-'*5} {'-'*4} {'-'*4} {'-'*5} {'-'*4} {'-'*4} {'-'*20}")
        for s in scored:
            layers_str = ",".join(s["layers_touched"]) if s["layers_touched"] else "-"
            lines.append(
                f"  {s['id']:<10} {s['e_step']:>7.2f} {s['surface_score']:>5} {s['novelty_score']:>4}"
                f" {s['ambiguity_score']:>4} {s['coupling_score']:>5} {s['acceptance_strength']:>4}"
                f" {s['constraint_strength']:>4} {layers_str}"
            )
        lines.append("")

        # Build JSON per-directory entry
        report["per_directory"][d] = {
            "metrics": {k: v for k, v in metrics.items()},
            "work_orders": [
                {
                    "id": s["id"],
                    "e_step": s["e_step"],
                    "surface_score": s["surface_score"],
                    "novelty_score": s["novelty_score"],
                    "ambiguity_score": s["ambiguity_score"],
                    "coupling_score": s["coupling_score"],
                    "acceptance_strength": s["acceptance_strength"],
                    "constraint_strength": s["constraint_strength"],
                    "existence_only_acceptance": s["existence_only_acceptance"],
                    "layers_touched": s["layers_touched"],
                }
                for s in scored
            ],
            "errors": errors,
        }

    # ── Overall ───────────────────────────────────────────────────────────
    if all_wo_flat:
        # Re-score in concatenated order for coupling (cross-directory)
        overall_scored: list[dict] = []
        prev_cf = None
        for wo in all_wo_flat:
            s = score_wo(wo, prev_cf)
            overall_scored.append(s)
            prev_cf = wo.get("context_files", [])

        overall_metrics = compute_directory_metrics(all_wo_flat, overall_scored)

        lines.append("=== OVERALL ===")
        lines.append(f"  Total WOs     : {overall_metrics['total_wos']}")
        lines.append(f"  Median E_step : {overall_metrics['median_e_step']}")
        lines.append(f"  Max E_step    : {overall_metrics['max_e_step']}  (WO {overall_metrics['max_e_step_id']})")
        lines.append(f"  C_global      : {overall_metrics['c_global']}")
        lines.append(f"  SeamStability : {overall_metrics['seam_stability']}")
        lines.append(f"  LayerScore    : {overall_metrics['layer_score']}")
        lines.append(f"  MeanAcceptRate: {overall_metrics['meaningful_acceptance_rate']}")
        lines.append(f"  CoreSeamChurn : {overall_metrics['core_seam_churn']}")
        lines.append(f"  LayerViolation: {overall_metrics['layer_violations']}")
        lines.append(f"  WO_expected   : {overall_metrics['wo_expected']}")
        lines.append(f"  Verdict       : {overall_metrics['verdict']}")
        if overall_metrics["warnings"]:
            lines.append(f"  Warnings      : {', '.join(overall_metrics['warnings'])}")
        lines.append("")

        report["overall"] = overall_metrics
    else:
        lines.append("=== OVERALL === no work orders found across any directory")
        lines.append("")

    # ── Print ─────────────────────────────────────────────────────────────
    print("\n".join(lines))
    print("---JSON---")
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

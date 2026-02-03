#!/usr/bin/env python3
"""
AOS CLI - Simple command-line interface for submitting work orders.

Usage:
    aos submit task.md           # Submit and return immediately
    aos submit task.md --wait    # Submit and wait for completion
    aos status <run-id>          # Check run status
    aos logs <run-id>            # View run events

All configuration is in the work order file - no flags needed.
"""

import argparse
import sys
import time
from pathlib import Path

import requests


DEFAULT_API_URL = "http://localhost:8000"


def get_api_url():
    """Get API URL from env or default."""
    import os
    return os.environ.get("AOS_API_URL", DEFAULT_API_URL)


def cmd_submit(args):
    """Submit a work order."""
    api = get_api_url()
    
    # Read the work order file
    wo_path = Path(args.work_order)
    if not wo_path.exists():
        print(f"Error: File not found: {wo_path}", file=sys.stderr)
        sys.exit(1)
    
    work_order_md = wo_path.read_text()
    
    # Submit - work order contains all configuration
    try:
        resp = requests.post(
            f"{api}/runs/submit",
            data={"work_order_md": work_order_md}
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    result = resp.json()
    run_id = result["run_id"]
    
    print(f"Submitted: {run_id}")
    print(f"Status:    {result['status']}")
    print(f"Track:     aos status {run_id}")
    
    if args.wait:
        print("\nWaiting for completion...")
        cmd_wait(run_id, args.timeout)


def cmd_status(args):
    """Get run status."""
    api = get_api_url()
    
    try:
        resp = requests.get(f"{api}/runs/{args.run_id}")
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    run = resp.json()
    
    print(f"Run:       {run['run_id']}")
    print(f"Status:    {run['status']}")
    print(f"Repo:      {run['repo_url']} @ {run['repo_ref']}")
    print(f"Iteration: {run['iteration']}")
    if run.get("result_summary"):
        print(f"Result:    {run['result_summary']}")
    if run.get("error"):
        print(f"Error:     {run['error']}")


def cmd_logs(args):
    """Get run events/logs."""
    api = get_api_url()
    
    try:
        resp = requests.get(f"{api}/runs/{args.run_id}/events")
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    events = resp.json()
    
    for e in events:
        ts = e["ts"][:19]  # Trim microseconds
        level = e["level"]
        kind = e["kind"]
        iteration = e.get("iteration", "-")
        payload = e.get("payload", {})
        
        # Format payload nicely
        details = ""
        if kind == "SE_OUTPUT":
            details = f"writes={payload.get('writes_count', 0)}"
        elif kind == "TR_APPLY":
            details = f"applied={payload.get('applied_count', 0)} ok={payload.get('commands_ok')}"
        elif kind == "PO_RESULT":
            details = f"decision={payload.get('decision')}"
        elif kind == "ERROR_EXCEPTION":
            details = payload.get("error", "")[:60]
        
        print(f"[{ts}] [{level:5}] {kind:20} iter={iteration} {details}")


def cmd_wait(run_id: str, timeout: int):
    """Wait for run to complete, showing progress."""
    api = get_api_url()
    start = time.time()
    last_event_count = 0
    
    while time.time() - start < timeout:
        resp = requests.get(f"{api}/runs/{run_id}")
        run = resp.json()
        status = run["status"]
        
        if status in ("SUCCEEDED", "FAILED", "CANCELED"):
            print(f"Completed: {status}")
            if run.get("result_summary"):
                print(f"Result: {run['result_summary']}")
            return status == "SUCCEEDED"
        
        # Fetch events to show progress
        try:
            events_resp = requests.get(f"{api}/runs/{run_id}/events")
            events = events_resp.json()
            
            # Show new events since last check
            new_events = events[last_event_count:]
            for e in new_events:
                _print_wait_event(e)
            last_event_count = len(events)
        except Exception:
            # Fallback to simple status
            print(f"  {status}...")
        
        time.sleep(2)
    
    print(f"Timeout after {timeout}s")
    return False


def _print_wait_event(event: dict):
    """Print a single event during wait, with concise formatting."""
    kind = event["kind"]
    iteration = event.get("iteration")
    payload = event.get("payload", {})
    
    # Skip less important events
    if kind in ("RUN_CREATED", "RUN_START", "STEP_START", "STEP_END"):
        return
    
    prefix = f"  [iter {iteration}]" if iteration else "  "
    
    if kind == "ITERATION_START":
        max_iter = payload.get("max_iterations", "?")
        print(f"{prefix} Starting iteration {iteration}/{max_iter}")
    elif kind == "SE_OUTPUT":
        writes = payload.get("writes_count", 0)
        print(f"{prefix} SE: {writes} file write(s) planned")
    elif kind == "TR_APPLY":
        applied = payload.get("applied_count", 0)
        ok = payload.get("commands_ok", False)
        inv_ok = payload.get("invariants_ok", True)
        status = "OK" if (ok and inv_ok) else "issues"
        print(f"{prefix} TR: {applied} write(s) applied, {status}")
    elif kind == "PO_RESULT":
        decision = payload.get("decision", "?")
        fixes = payload.get("fixes_count", 0)
        reasons = payload.get("reasons_count", 0)
        if decision == "PASS":
            print(f"{prefix} PO: PASS")
        else:
            print(f"{prefix} PO: {decision} ({reasons} reason(s), {fixes} fix(es) requested)")
    elif kind == "RUN_END":
        status = payload.get("status", "?")
        print(f"  Run ended: {status}")
    elif kind == "ERROR_EXCEPTION":
        error_type = payload.get("error_type", "Error")
        error_msg = payload.get("error_message", "")[:60]
        print(f"{prefix} ERROR: {error_type}: {error_msg}")


def main():
    parser = argparse.ArgumentParser(
        prog="aos",
        description="AOS CLI - Agent Orchestration Service",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # submit
    p_submit = subparsers.add_parser(
        "submit", 
        help="Submit a work order",
        description="Submit a work order. All configuration is in the .md file.",
    )
    p_submit.add_argument("work_order", help="Path to work order .md file")
    p_submit.add_argument("--wait", action="store_true", help="Wait for completion")
    p_submit.add_argument("--timeout", type=int, default=300, help="Wait timeout in seconds")
    p_submit.set_defaults(func=cmd_submit)
    
    # status
    p_status = subparsers.add_parser("status", help="Get run status")
    p_status.add_argument("run_id", help="Run ID")
    p_status.set_defaults(func=cmd_status)
    
    # logs
    p_logs = subparsers.add_parser("logs", help="Get run events/logs")
    p_logs.add_argument("run_id", help="Run ID")
    p_logs.set_defaults(func=cmd_logs)
    
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

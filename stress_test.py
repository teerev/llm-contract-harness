#!/usr/bin/env python3
"""
AOS Stress Test: Build a CLI Task Manager through 20 sequential work orders.

Usage:
    python stress_test.py

This script submits work orders one at a time, waiting for each to complete
before submitting the next. The result is a fully functional task manager CLI.

After completion, pull the branch and run:
    python -m taskman --help
"""

import json
import os
import subprocess
import time
import sys
import requests
import yaml

API_URL = "http://localhost:8000"
REPO_URL = "https://github.com/teerev/dft-orch"
BASE_BRANCH = "main"  # Starting point
DEV_BRANCH = "aos/taskman-dev"  # All work orders push to this single branch

# Track whether we've created the dev branch yet
branch_created = False


def work_order_to_markdown(wo: dict) -> str:
    """
    Convert a work order dict to markdown format with YAML frontmatter.
    
    This is the canonical format that both the factory CLI and AOS API expect.
    """
    frontmatter = wo["work_order"]
    body = wo["work_order_body"].strip()
    
    yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    
    return f"---\n{yaml_str}---\n{body}\n"


def delete_remote_branch():
    """Delete the dev branch from GitHub if it exists."""
    print(f"Checking for existing branch: {DEV_BRANCH}")
    
    # Use GITHUB_TOKEN if available for auth
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("  Warning: GITHUB_TOKEN not set, branch deletion may fail for private repos")
    
    # Extract owner/repo from URL
    # https://github.com/teerev/dft-orch -> teerev/dft-orch
    repo_path = REPO_URL.replace("https://github.com/", "").replace(".git", "")
    
    # Use GitHub API to delete branch
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    url = f"https://api.github.com/repos/{repo_path}/git/refs/heads/{DEV_BRANCH}"
    resp = requests.delete(url, headers=headers)
    
    if resp.status_code == 204:
        print(f"  Deleted existing branch: {DEV_BRANCH}")
    elif resp.status_code == 404:
        print(f"  Branch does not exist (clean start)")
    else:
        print(f"  Warning: Could not delete branch (status {resp.status_code}): {resp.text}")


def submit_work_order(wo: dict) -> str:
    """Submit a work order and return the run_id."""
    global branch_created
    
    # First work order: base off main, push to dev branch
    # Subsequent work orders: base off dev branch, push to same dev branch
    ref = DEV_BRANCH if branch_created else BASE_BRANCH
    
    # Convert to markdown format (single source of truth for work order parsing)
    work_order_md = work_order_to_markdown(wo)
    
    payload = {
        "repo_url": REPO_URL,
        "ref": ref,
        "work_order_md": work_order_md,
        "params": {"max_iterations": 5},
        "writeback": {
            "mode": "push_branch",
            "branch_name": DEV_BRANCH,
        },
    }
    
    resp = requests.post(f"{API_URL}/runs", json=payload)
    resp.raise_for_status()
    return resp.json()["run_id"]


def wait_for_completion(run_id: str, timeout: int = 300) -> dict:
    """Poll until run completes or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{API_URL}/runs/{run_id}")
        resp.raise_for_status()
        data = resp.json()
        
        status = data["status"]
        if status in ("SUCCEEDED", "FAILED", "CANCELED"):
            return data
        
        print(f"  Status: {status}, iteration: {data.get('iteration', 0)}", end="\r")
        time.sleep(2)
    
    raise TimeoutError(f"Run {run_id} did not complete within {timeout}s")


def run_work_order(index: int, wo: dict) -> bool:
    """Run a single work order. Returns True on success."""
    global branch_created
    
    title = wo["work_order"]["title"]
    
    print(f"\n{'='*60}")
    print(f"Step {index}/20: {title}")
    print(f"{'='*60}")
    
    run_id = submit_work_order(wo)
    print(f"Run ID: {run_id}")
    print(f"Building on: {DEV_BRANCH if branch_created else BASE_BRANCH}")
    
    result = wait_for_completion(run_id)
    print(f"\nResult: {result['status']}")
    print(f"Summary: {result.get('result_summary', 'N/A')}")
    
    if result["status"] == "SUCCEEDED":
        branch_created = True  # Dev branch now exists for next work order
        return True
    else:
        print(f"ERROR: {result.get('error', 'Unknown error')}")
        # Fetch and show events
        events = requests.get(f"{API_URL}/runs/{run_id}/events").json()
        for e in events[-5:]:  # Last 5 events
            print(f"  [{e['kind']}] {e.get('payload', {})}")
        return False


# ============================================================
# The 20 Work Orders
# ============================================================

WORK_ORDERS = [
    # 1. Project structure
    {
        "work_order": {
            "title": "Create taskman package structure",
            "acceptance_commands": [
                "python -c \"import taskman\""
            ],
            "allowed_paths": ["taskman/**", "setup.py", "pyproject.toml"],
        },
        "work_order_body": """
Create the basic package structure for a CLI task manager called 'taskman'.

Create:
1. taskman/__init__.py - with __version__ = "0.1.0"
2. taskman/models.py - empty file for now
3. taskman/cli.py - empty file for now
4. taskman/store.py - empty file for now

The package should be importable as 'import taskman'.
""",
    },
    
    # 2. Task model
    {
        "work_order": {
            "title": "Implement Task model",
            "acceptance_commands": [
                "python -c \"from taskman.models import Task; t = Task(title='Test'); assert t.title == 'Test'\""
            ],
            "allowed_paths": ["taskman/**"],
        },
        "work_order_body": """
Implement the Task model in taskman/models.py using dataclasses.

Task should have:
- id: str (UUID, auto-generated)
- title: str (required)
- description: str (default empty)
- completed: bool (default False)
- created_at: datetime (auto-set to now)
- completed_at: datetime | None (default None)

Use dataclasses and the uuid and datetime modules from stdlib.
Include a to_dict() method and a from_dict() classmethod for serialization.
""",
    },
    
    # 3. In-memory store
    {
        "work_order": {
            "title": "Implement TaskStore with in-memory storage",
            "acceptance_commands": [
                "python -c \"from taskman.store import TaskStore; from taskman.models import Task; s = TaskStore(); t = s.add('Test'); assert s.get(t.id).title == 'Test'\""
            ],
            "allowed_paths": ["taskman/**"],
        },
        "work_order_body": """
Implement TaskStore in taskman/store.py for managing tasks.

TaskStore should have methods:
- add(title: str, description: str = "") -> Task: Create and store a new task
- get(task_id: str) -> Task | None: Get a task by ID
- list_all() -> list[Task]: Get all tasks
- update(task: Task) -> None: Update an existing task
- delete(task_id: str) -> bool: Delete a task, return True if found

Store tasks in a dict keyed by task ID.
""",
    },
    
    # 4. JSON persistence
    {
        "work_order": {
            "title": "Add JSON file persistence to TaskStore",
            "acceptance_commands": [
                "python -c \"import os; from taskman.store import TaskStore; s = TaskStore('/tmp/test_tasks.json'); s.add('Test'); s2 = TaskStore('/tmp/test_tasks.json'); assert len(s2.list_all()) >= 1; os.remove('/tmp/test_tasks.json')\""
            ],
            "allowed_paths": ["taskman/**"],
        },
        "work_order_body": """
Modify TaskStore to persist tasks to a JSON file.

Changes:
1. __init__ should accept an optional file_path parameter (default: ~/.taskman/tasks.json)
2. Create the directory if it doesn't exist
3. Load existing tasks from file on init (if file exists)
4. Save to file after every add/update/delete operation
5. Use Task.to_dict() and Task.from_dict() for serialization

Handle file not found gracefully (start with empty store).
""",
    },
    
    # 5. Basic CLI with argparse
    {
        "work_order": {
            "title": "Create basic CLI with argparse",
            "acceptance_commands": [
                "python -m taskman --help"
            ],
            "allowed_paths": ["taskman/**"],
        },
        "work_order_body": """
Create the CLI entry point in taskman/cli.py using argparse.

1. Create a main() function that sets up argparse
2. Add subcommand structure (but don't implement commands yet):
   - add
   - list
   - complete
   - delete
3. Create taskman/__main__.py that calls cli.main()

The --help should show available commands.
""",
    },
    
    # 6. Add command
    {
        "work_order": {
            "title": "Implement 'add' command",
            "acceptance_commands": [
                "python -c \"from taskman.store import TaskStore; from taskman.cli import main; main(['add', 'Test']); s = TaskStore(); tasks = s.list_all(); assert any('Test' in t.title for t in tasks), f'Task not found in {[t.title for t in tasks]}'\""
            ],
            "allowed_paths": ["taskman/cli.py", "taskman/__main__.py"],
        },
        "work_order_body": """
Implement the 'add' and 'list' commands in taskman/cli.py.

IMPORTANT: Do NOT modify taskman/store.py or taskman/models.py - they are already complete.

First, check the existing TaskStore API by looking at store.py. Use whatever methods exist there.
Common methods are likely: add(title) -> Task, list_all() -> list[Task], get(task_id) -> Task | None

Implement:
1. 'add' subcommand: taskman add TITLE
   - Create a TaskStore instance
   - Call store.add(title) - check store.py for exact signature
   - Print "Added task: {task.id} - {task.title}" (task.id may be int or str)

2. 'list' subcommand: taskman list
   - Create a TaskStore instance  
   - Call store.list_all() and print each task's title

The TaskStore automatically persists to ~/.taskman/tasks.json.
""",
    },
    
    # 7. List command with formatting
    {
        "work_order": {
            "title": "Implement formatted 'list' command",
            "acceptance_commands": [
                {"cmd": "python -m taskman list --all", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py"],
        },
        "work_order_body": """
Improve the 'list' command with better formatting.

IMPORTANT: Check taskman/store.py for the exact method names. Use whatever list method exists (likely list_all()).
Do NOT invent new method names - use what's already in store.py.

Usage: taskman list [--all]

Default behavior: show only incomplete tasks (if Task has a 'completed' field)
With --all: show all tasks
If Task has no 'completed' field, just show all tasks always.

Output format for each task:
[x] or [ ] followed by task id followed by title

Example:
[ ] 1  Buy groceries
[x] 2  Call mom

The acceptance test just checks that 'python -m taskman list --all' runs without error.
""",
    },
    
    # 8. Complete command
    {
        "work_order": {
            "title": "Implement 'complete' command",
            "acceptance_commands": [
                {"cmd": "python -m taskman complete --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add a 'complete' subcommand to mark a task as done.

IMPORTANT: First read taskman/store.py and taskman/models.py to understand:
- What fields Task has (may or may not have 'completed')
- What methods TaskStore has for updating tasks

If Task doesn't have a 'completed' field, add it to models.py.
If TaskStore doesn't have an update method, add one to store.py.

Usage: taskman complete TASK_ID

The command should:
1. Look up the task by ID using the store
2. Mark it as completed (set completed=True or similar)
3. Save/update the task
4. Print confirmation

The acceptance test just verifies --help works (command exists).
""",
    },
    
    # 9. Delete command
    {
        "work_order": {
            "title": "Implement 'delete' command",
            "acceptance_commands": [
                {"cmd": "python -m taskman delete --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add a 'delete' subcommand to remove a task.

IMPORTANT: First read taskman/store.py to see what delete method exists.

Usage: taskman delete TASK_ID

The command should:
1. Look up the task by ID
2. Delete it from the store
3. Print confirmation

The acceptance test just verifies --help works (command exists).
""",
    },
    
    # 10. Priority support
    {
        "work_order": {
            "title": "Add priority support to tasks",
            "acceptance_commands": [
                {"cmd": "python -m taskman add --help | grep -i priority", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py", "taskman/models.py", "taskman/store.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add priority support to tasks.

IMPORTANT: Read existing models.py and store.py first to understand current structure.

1. Add a 'priority' field to Task (values: "low", "medium", "high", default "medium")
2. Update Task.to_dict() and Task.from_dict() to include priority
3. Add --priority option to the 'add' command
4. Update 'list' output to show priority

The acceptance test verifies 'add --help' mentions priority.
""",
    },
    
    # 11. Due dates
    {
        "work_order": {
            "title": "Add due date support",
            "acceptance_commands": [
                {"cmd": "python -m taskman add --help | grep -i due", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py", "taskman/models.py", "taskman/store.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add due date support to tasks.

IMPORTANT: Read existing models.py first to understand current Task structure.

1. Add a 'due_date' field to Task (string in YYYY-MM-DD format, or None)
2. Update Task.to_dict() and Task.from_dict() to include due_date
3. Add --due option to the 'add' command (accepts YYYY-MM-DD)
4. Update 'list' output to show due date when present

The acceptance test verifies 'add --help' mentions due.
""",
    },
    
    # 12. Tags support
    {
        "work_order": {
            "title": "Add tags/labels support",
            "acceptance_commands": [
                {"cmd": "python -m taskman add --help | grep -i tag", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py", "taskman/models.py", "taskman/store.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add tags support to tasks.

IMPORTANT: Read existing models.py first to understand current Task structure.

1. Add a 'tags' field to Task (list of strings, default empty list)
2. Update Task.to_dict() and Task.from_dict() to include tags
3. Add --tags option to the 'add' command (comma-separated: --tags work,urgent)
4. Update 'list' output to show tags

The acceptance test verifies 'add --help' mentions tags.
""",
    },
    
    # 13. Search command
    {
        "work_order": {
            "title": "Add search functionality",
            "acceptance_commands": [
                {"cmd": "python -m taskman search --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add a 'search' subcommand to find tasks.

IMPORTANT: Read existing store.py and cli.py to understand current structure.

Usage: taskman search QUERY

Search for tasks where the query appears in title (case-insensitive).
Display matching tasks in the same format as 'list'.

The acceptance test just verifies --help works.
""",
    },
    
    # 14. Show command for details
    {
        "work_order": {
            "title": "Add 'show' command for task details",
            "acceptance_commands": [
                {"cmd": "python -m taskman show --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add a 'show' subcommand to display full details of a single task.

IMPORTANT: Read existing models.py to see what fields Task has.

Usage: taskman show TASK_ID

Display all fields of the task in a readable format with labels, e.g.:
  ID: 123
  Title: Buy groceries
  Priority: high
  ...

The acceptance test just verifies --help works.
""",
    },
    
    # 15. Edit command
    {
        "work_order": {
            "title": "Add 'edit' command",
            "acceptance_commands": [
                {"cmd": "python -m taskman edit --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py", "taskman/store.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add an 'edit' subcommand to modify an existing task.

IMPORTANT: Read existing store.py to see what update method exists.

Usage: taskman edit TASK_ID [--title TITLE] [--priority PRIORITY] [--due DATE] [--tags TAGS]

Only update the fields that are provided as arguments.
Print "Updated: {title}" on success.

The acceptance test just verifies --help works.
""",
    },
    
    # 16. Export/Import
    {
        "work_order": {
            "title": "Add export and import commands",
            "acceptance_commands": [
                {"cmd": "python -m taskman export --help && python -m taskman import --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add 'export' and 'import' subcommands.

IMPORTANT: Read existing store.py and models.py to understand data structure.

Export (taskman export FILE):
- Get all tasks from store
- Write them to a JSON file as a list of dicts

Import (taskman import FILE):
- Read tasks from JSON file
- Add them to the store
- Print count of imported tasks

The acceptance test just verifies --help works for both commands.
""",
    },
    
    # 17. Statistics command
    {
        "work_order": {
            "title": "Add 'stats' command",
            "acceptance_commands": [
                {"cmd": "python -m taskman stats", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add a 'stats' subcommand to show task statistics.

IMPORTANT: Read existing store.py and models.py to see what data is available.

Usage: taskman stats

Display statistics such as:
- Total tasks
- Completed tasks (if 'completed' field exists)
- Pending tasks

Print at least "Total: N" so the output contains the word "Total".

The acceptance test verifies the command runs without error.
""",
    },
    
    # 18. Clear completed
    {
        "work_order": {
            "title": "Add 'clear' command for completed tasks",
            "acceptance_commands": [
                {"cmd": "python -m taskman clear --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add a 'clear' subcommand to remove completed tasks.

IMPORTANT: Read existing store.py to see what delete method exists.

Usage: taskman clear [--force]

- Find all tasks where completed=True
- Without --force: print count and ask "Delete N completed tasks? [y/N]"
- With --force: delete without asking
- Print "Cleared N completed tasks"

The acceptance test just verifies --help works.
""",
    },
    
    # 19. Uncomplete command
    {
        "work_order": {
            "title": "Add 'uncomplete' command",
            "acceptance_commands": [
                {"cmd": "python -m taskman uncomplete --help", "shell": True}
            ],
            "allowed_paths": ["taskman/cli.py"],
            "context_files": ["taskman/store.py", "taskman/models.py", "taskman/cli.py"],
        },
        "work_order_body": """
Add an 'uncomplete' subcommand to mark a task as not completed.

IMPORTANT: Read existing models.py to see if Task has a 'completed' field.

Usage: taskman uncomplete TASK_ID

Set the task's completed status back to False.
Print "Reopened: {title}"

The acceptance test just verifies --help works.
""",
    },
    
    # 20. README and final polish
    {
        "work_order": {
            "title": "Add README and final polish",
            "acceptance_commands": [
                {"cmd": "test -f taskman/README.md && python -m taskman --help", "shell": True}
            ],
            "allowed_paths": ["taskman/README.md", "taskman/cli.py", "taskman/__init__.py"],
            "context_files": ["taskman/cli.py", "taskman/__init__.py"],
        },
        "work_order_body": """
Add documentation and final polish.

1. Create taskman/README.md with:
   - Project description: "A command-line task manager"
   - Basic usage examples for: add, list, complete, delete

2. Add --version flag to the main CLI parser that prints the version

3. Ensure 'python -m taskman --help' shows all available commands

The acceptance test verifies README.md exists and --help works.
""",
    },
]


def main():
    print("AOS Stress Test: Building CLI Task Manager")
    print(f"Target repo: {REPO_URL}")
    print(f"Total work orders: {len(WORK_ORDERS)}")
    print()
    
    # Check API is running
    try:
        requests.get(f"{API_URL}/healthz").raise_for_status()
    except Exception as e:
        print(f"ERROR: Cannot reach API at {API_URL}")
        print(f"Make sure uvicorn is running: uvicorn src.aos.api.app:app --reload")
        sys.exit(1)
    
    # Clean up any existing branch from previous runs
    delete_remote_branch()
    print()
    
    succeeded = 0
    failed = 0
    
    for i, wo in enumerate(WORK_ORDERS, 1):
        if run_work_order(i, wo):
            succeeded += 1
        else:
            failed += 1
            print(f"\nStopping due to failure at step {i}")
            break
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")
    
    if branch_created:
        print(f"\nAll changes pushed to: {DEV_BRANCH}")
        print(f"\nTo use the result:")
        print(f"  cd /path/to/dft-orch")
        print(f"  git fetch origin")
        print(f"  git checkout {DEV_BRANCH}")
        print(f"  python -m taskman --help")


if __name__ == "__main__":
    main()

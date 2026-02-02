"""
Security validators and sanitizers for AOS.

Provides:
- sanitize_error_message(): Strip tokens from error strings
- validate_repo_url(): Validate GitHub URL format
- validate_branch_name(): Validate branch name format
- validate_work_order(): Validate work order for security issues
"""

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Token Sanitization
# =============================================================================

def sanitize_error_message(message: str) -> str:
    """
    Remove sensitive tokens from error messages.
    
    This prevents GITHUB_TOKEN and other secrets from leaking into
    logs, error responses, or exception messages.
    
    Args:
        message: The raw error message that may contain tokens
    
    Returns:
        Sanitized message with tokens replaced by [REDACTED]
    """
    if not message:
        return message
    
    sanitized = message
    
    # Remove GITHUB_TOKEN if present in environment
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token and github_token in sanitized:
        sanitized = sanitized.replace(github_token, "[REDACTED]")
    
    # Generic patterns for tokens in URLs
    # Matches: https://x-access-token:TOKEN@github.com/...
    sanitized = re.sub(
        r"(https?://[^:]+:)[^@]+(@)",
        r"\1[REDACTED]\2",
        sanitized
    )
    
    # Matches: Bearer TOKEN, token=TOKEN, etc.
    sanitized = re.sub(
        r"(Bearer\s+|token[=:]\s*)[a-zA-Z0-9_-]+",
        r"\1[REDACTED]",
        sanitized,
        flags=re.IGNORECASE
    )
    
    # Matches: ghp_*, gho_*, github_pat_* (GitHub token prefixes)
    sanitized = re.sub(
        r"\b(ghp_|gho_|github_pat_)[a-zA-Z0-9_]+\b",
        "[REDACTED]",
        sanitized
    )
    
    return sanitized


class SanitizedError(Exception):
    """
    Exception wrapper that sanitizes the error message.
    
    Use this to wrap subprocess errors before re-raising,
    to prevent token leakage.
    """
    def __init__(self, original_error: Exception):
        self.original_error = original_error
        sanitized_msg = sanitize_error_message(str(original_error))
        super().__init__(sanitized_msg)


# =============================================================================
# URL and Name Validation
# =============================================================================

# Valid GitHub HTTPS URL pattern
GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+(?:\.git)?$"
)

# Valid git branch name pattern (restrictive for safety)
# Allows: alphanumeric, /, -, _, .
# Forbids: shell metacharacters, spaces, control chars
BRANCH_NAME_PATTERN = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9/_.-]{0,99}$"
)

# Characters that are dangerous in shell contexts
SHELL_DANGEROUS_CHARS = set(";|&$`(){}[]<>\\!\"'*?\n\r")


def validate_repo_url(url: str) -> str:
    """
    Validate that a URL is a valid GitHub HTTPS URL.
    
    Args:
        url: The URL to validate
    
    Returns:
        The validated URL (unchanged)
    
    Raises:
        ValueError: If the URL is invalid
    """
    if not url:
        raise ValueError("Repository URL cannot be empty")
    
    if not GITHUB_URL_PATTERN.match(url):
        raise ValueError(
            f"Invalid repository URL: must be a GitHub HTTPS URL "
            f"(https://github.com/owner/repo), got: {url}"
        )
    
    return url


def validate_branch_name(name: str) -> str:
    """
    Validate that a branch name is safe for git operations.
    
    Args:
        name: The branch name to validate
    
    Returns:
        The validated branch name (unchanged)
    
    Raises:
        ValueError: If the branch name is invalid
    """
    if not name:
        raise ValueError("Branch name cannot be empty")
    
    if not BRANCH_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid branch name: must contain only alphanumeric characters, "
            f"slashes, hyphens, underscores, and dots. Max 100 chars. Got: {name}"
        )
    
    # Additional git-specific checks
    if name.endswith("/") or name.endswith("."):
        raise ValueError(f"Branch name cannot end with '/' or '.': {name}")
    
    if ".." in name:
        raise ValueError(f"Branch name cannot contain '..': {name}")
    
    if name.startswith("-"):
        raise ValueError(f"Branch name cannot start with '-': {name}")
    
    return name


def validate_ref(ref: str) -> str:
    """
    Validate a git ref (branch name or commit SHA).
    
    Args:
        ref: The ref to validate
    
    Returns:
        The validated ref (unchanged)
    
    Raises:
        ValueError: If the ref is invalid
    """
    if not ref:
        raise ValueError("Git ref cannot be empty")
    
    # Check if it's a valid SHA (40 hex chars) or short SHA (7+ hex chars)
    if re.match(r"^[a-f0-9]{7,40}$", ref):
        return ref
    
    # Otherwise validate as branch name
    return validate_branch_name(ref)


# =============================================================================
# Work Order Validation
# =============================================================================

# Patterns that are dangerous in shell commands
DANGEROUS_COMMAND_PATTERNS = [
    r"rm\s+-rf\s+/",           # rm -rf /
    r">\s*/dev/",              # redirect to /dev/
    r"curl\s+.*\|\s*sh",       # curl | sh
    r"wget\s+.*\|\s*sh",       # wget | sh
    r"eval\s+",                # eval
    r"\$\(",                   # command substitution
    r"`[^`]+`",                # backtick command substitution
]


def validate_work_order(work_order: dict[str, Any]) -> dict[str, Any]:
    """
    Validate a work order for security issues.
    
    Logs warnings for potentially dangerous patterns but does not
    reject the work order (to maintain flexibility).
    
    Args:
        work_order: The parsed work order dict
    
    Returns:
        The work order (unchanged, but with warnings logged)
    """
    acceptance_commands = work_order.get("acceptance_commands", [])
    
    for i, cmd in enumerate(acceptance_commands):
        # Handle both string commands and CommandSpec dicts
        if isinstance(cmd, dict):
            cmd_str = cmd.get("cmd", "")
            uses_shell = cmd.get("shell", False)
        else:
            cmd_str = str(cmd)
            uses_shell = False  # Default is no shell
        
        if uses_shell:
            # Check for dangerous patterns in shell commands
            for pattern in DANGEROUS_COMMAND_PATTERNS:
                if re.search(pattern, cmd_str, re.IGNORECASE):
                    logger.warning(
                        f"Potentially dangerous command pattern in acceptance_commands[{i}]: "
                        f"'{pattern}' detected in '{cmd_str[:50]}...'"
                    )
            
            # Check for shell metacharacters that might indicate injection
            dangerous_found = [c for c in cmd_str if c in SHELL_DANGEROUS_CHARS]
            if len(dangerous_found) > 5:  # Allow some normal shell usage
                logger.warning(
                    f"acceptance_commands[{i}] uses shell=true with many special characters. "
                    f"Review for potential injection: {cmd_str[:80]}..."
                )
        else:
            # For non-shell commands, warn if shell metacharacters are present
            # (they won't work as expected)
            if any(c in cmd_str for c in ";|&"):
                logger.warning(
                    f"acceptance_commands[{i}] contains shell operators but shell=false. "
                    f"Use {{'cmd': '...', 'shell': true}} for shell features."
                )
    
    return work_order

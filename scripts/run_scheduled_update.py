#!/usr/bin/env python3
"""Run the scheduled dashboard refresh, then publish dashboard file changes."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "update_from_yahoo.py"
DEFAULT_HTML = PROJECT_ROOT / "index.html"
DEFAULT_COMPANIES_JSON = PROJECT_ROOT / "custom_companies.json"
PYTHON = "/usr/bin/python3"
DASHBOARD_PATHS = ("index.html", "custom_companies.json")
SAFE_CWD = Path.home()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--update-script", type=Path, default=DEFAULT_UPDATE_SCRIPT)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--companies-json", type=Path, default=DEFAULT_COMPANIES_JSON)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--commit-message", default="Update Taiwan revenue dashboard")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="")
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--quiet-update", action="store_true")
    return parser.parse_args()


def run(command: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result


def git_command(repo: Path, args: list[str]) -> list[str]:
    return ["git", f"--git-dir={repo / '.git'}", f"--work-tree={repo}", *args]


def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return run(git_command(repo, args), SAFE_CWD, check=check)


def git_output(repo: Path, args: list[str]) -> str:
    result = subprocess.run(git_command(repo, args), cwd=SAFE_CWD, text=True, capture_output=True)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, git_command(repo, args), result.stdout, result.stderr)
    return result.stdout.strip()


def repo_is_clean(repo: Path) -> bool:
    return not git_output(repo, ["status", "--porcelain"])


def dashboard_has_changes(repo: Path) -> bool:
    return bool(git_output(repo, ["status", "--porcelain", "--", *DASHBOARD_PATHS]))


def current_branch(repo: Path, configured: str) -> str:
    if configured:
        return configured
    branch = git_output(repo, ["branch", "--show-current"])
    if not branch:
        raise RuntimeError("cannot determine current git branch")
    return branch


def pull_if_clean(repo: Path, remote: str, branch: str, disabled: bool) -> None:
    if disabled:
        return
    if not repo_is_clean(repo):
        print("Skipping pre-update git pull because the repository has local changes.")
        return
    run_git(repo, ["pull", "--ff-only", remote, branch])


def run_dashboard_update(args: argparse.Namespace) -> None:
    command = [
        PYTHON,
        str(args.update_script),
        "--html",
        str(args.html),
        "--companies-json",
        str(args.companies_json),
        "--workers",
        str(args.workers),
        "--timeout",
        str(args.timeout),
        "--retries",
        str(args.retries),
    ]
    if args.quiet_update:
        command.append("--quiet")
    run(command, args.html.parent)


def publish_dashboard_changes(repo: Path, remote: str, branch: str, message: str, disabled: bool) -> bool:
    if not dashboard_has_changes(repo):
        print("No dashboard changes to publish.")
        return False

    run_git(repo, ["add", "--", *DASHBOARD_PATHS])
    staged = run_git(repo, ["diff", "--cached", "--quiet", "--", *DASHBOARD_PATHS], check=False)
    if staged.returncode == 0:
        print("No staged dashboard changes to publish.")
        return False

    run_git(
        repo,
        [
            "-c",
            "user.name=Taiwan Revenue Bot",
            "-c",
            "user.email=taiwan-revenue-bot@local",
            "commit",
            "-m",
            message,
        ],
    )
    if disabled:
        print("Skipping git push because --no-push was set.")
        return True
    run_git(repo, ["push", remote, branch])
    return True


def main() -> int:
    args = parse_args()
    args.repo = args.repo.expanduser().resolve()
    args.update_script = args.update_script.expanduser().resolve()
    args.html = args.html.expanduser().resolve()
    args.companies_json = args.companies_json.expanduser().resolve()
    branch = current_branch(args.repo, args.branch)
    pull_if_clean(args.repo, args.remote, branch, args.no_pull)
    run_dashboard_update(args)
    published = publish_dashboard_changes(args.repo, args.remote, branch, args.commit_message, args.no_push)
    print(f"Scheduled update complete. Published: {published}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"run_scheduled_update.py: command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
    except Exception as exc:
        print(f"run_scheduled_update.py: {exc}", file=sys.stderr)
        raise SystemExit(1)

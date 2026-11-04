"""Isolated git worktrees for supervised sessions."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .utils import get_rdc_home

logger = logging.getLogger(__name__)


class WorktreeManager:
    """Manages isolated git worktrees outside active user checkouts."""

    def __init__(self, rdc_home: Optional[Path] = None):
        self.rdc_home = rdc_home or get_rdc_home()
        self.worktrees_dir = self.rdc_home / "worktrees"

    def is_git_repo(self, path: Path) -> bool:
        """Check if path is inside a git work tree."""
        if not path or not path.is_dir():
            return False
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"

    def get_current_branch(self, path: Path) -> str:
        """Get the current checked out branch name or HEAD commit."""
        if not self.is_git_repo(path):
            return ""
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return rev.stdout.strip() if rev.returncode == 0 else "HEAD"

    def list_branches(self, path: Path) -> list[str]:
        """List local branch names for the project."""
        if not self.is_git_repo(path):
            return []
        res = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if res.returncode != 0:
            return []
        branches = [b.strip() for b in res.stdout.splitlines() if b.strip()]
        return branches

    def list_worktrees(self, path: Path) -> list[dict[str, str]]:
        """List active worktrees for the project."""
        if not self.is_git_repo(path):
            return []
        res = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if res.returncode != 0:
            return []
        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[len("worktree "):].strip()}
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):].strip().replace("refs/heads/", "")
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):].strip()
            elif line == "bare":
                current["bare"] = "true"
        if current:
            worktrees.append(current)
        return worktrees

    def get_worktree_path(self, session_id: str, project_slug: Optional[str] = None) -> Optional[Path]:
        """Return the active worktree path if it exists on disk."""
        if project_slug:
            candidate = self.worktrees_dir / project_slug / session_id
            if candidate.is_dir():
                return candidate
        if self.worktrees_dir.is_dir():
            for proj_dir in self.worktrees_dir.iterdir():
                if proj_dir.is_dir():
                    candidate = proj_dir / session_id
                    if candidate.is_dir():
                        return candidate
        return None

    def create_worktree(
        self,
        project_path: Path,
        session_id: str,
        branch_name: Optional[str] = None,
        base_branch: Optional[str] = None,
    ) -> tuple[Optional[Path], Optional[str]]:
        """Create ~/.rdc/worktrees/{project}/{session_id} on branch rdc/{session_id}.

        Return (None, None) when the project has no .git.
        """
        if not self.is_git_repo(project_path):
            return None, None

        project_slug = project_path.name
        target_path = (self.worktrees_dir / project_slug / session_id).resolve()

        target_branch = branch_name.strip() if branch_name and branch_name.strip() else f"rdc/{session_id}"

        # If worktree already exists, verify and return
        if target_path.is_dir():
            active_branch = self.get_current_branch(target_path) or target_branch
            return target_path, active_branch

        target_path.parent.mkdir(parents=True, exist_ok=True)

        existing_branches = set(self.list_branches(project_path))
        active_worktrees = {wt.get("branch"): wt.get("path") for wt in self.list_worktrees(project_path)}

        if target_branch in existing_branches:
            if target_branch in active_worktrees:
                # Branch already checked out in another worktree (e.g. main repo checkout)
                if target_branch == f"rdc/{session_id}":
                    cmd = ["git", "worktree", "add", str(target_path), target_branch]
                else:
                    # Create dedicated sub-branch off that branch to allow worktree
                    target_branch = f"rdc/{session_id}-{target_branch}"
                    cmd = ["git", "worktree", "add", "-b", target_branch, str(target_path), branch_name]
            else:
                cmd = ["git", "worktree", "add", str(target_path), target_branch]
        else:
            cmd = ["git", "worktree", "add", "-b", target_branch, str(target_path)]
            if base_branch:
                cmd.append(base_branch)

        res = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if res.returncode != 0:
            logger.warning("git worktree add failed: %s (stderr: %s)", res.stdout, res.stderr)
            fallback_branch = f"rdc/{session_id}"
            if target_branch != fallback_branch:
                fallback_cmd = ["git", "worktree", "add", "-b", fallback_branch, str(target_path)]
                res2 = subprocess.run(fallback_cmd, cwd=project_path, capture_output=True, text=True, check=False, timeout=30)
                if res2.returncode == 0:
                    return target_path, fallback_branch
            raise RuntimeError(f"Failed to create git worktree: {res.stderr.strip() or res.stdout.strip()}")

        return target_path, target_branch

    def cleanup_worktree(
        self,
        session_id: str,
        worktree_path: Optional[Path] = None,
        project_path: Optional[Path] = None,
    ) -> bool:
        """Remove the worktree directory.

        Never deletes the branch or reachable commits.
        Refuses to remove if a terminal PTY process is active with cwd in this worktree.
        """
        path = worktree_path or self.get_worktree_path(session_id)
        if not path or not path.exists():
            return False

        # Check if terminal manager has any active sessions in this path
        try:
            from .terminal import get_terminal_manager
            tm = get_terminal_manager()
            for term in tm.list():
                if term.process and term.process.poll() is None:
                    # If process is running, check cwd
                    term_cwd = getattr(term, "cwd", None)
                    if term_cwd and Path(term_cwd).resolve() == path.resolve():
                        logger.warning("Cannot cleanup worktree %s: terminal %s is active", path, term.id)
                        return False
        except Exception:
            pass

        repo_cwd = project_path or path.parent.parent
        res = subprocess.run(
            ["git", "worktree", "remove", str(path)],
            cwd=repo_cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if res.returncode != 0:
            subprocess.run(["git", "worktree", "prune"], cwd=repo_cwd, capture_output=True, text=True, check=False, timeout=20)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        return True


_worktree_manager: Optional[WorktreeManager] = None


def get_worktree_manager() -> WorktreeManager:
    global _worktree_manager
    if _worktree_manager is None:
        _worktree_manager = WorktreeManager()
    return _worktree_manager

import os
import subprocess
from pathlib import Path
from typing import Optional

import yaml

from ..registry import _safe_name


class GitRegistry:
    """Git-backed registry for workflows.

    Layout: <repo_root>/<tenant>/<workflow_name>/<version>.yaml
    Commits: every register/delete produces a git commit. Returns commit SHA.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self._init_repo()

    def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(("git",) + args, cwd=str(self.repo_root), capture_output=True, text=True, check=False)

    def _init_repo(self) -> None:
        # Initialize git if not already
        git_dir = self.repo_root / ".git"
        if not git_dir.exists():
            r = self._run_git("init")
            # ignore errors; best-effort

    def _commit_all(self, message: str, author: Optional[str] = None) -> str:
        self._run_git("add", "--all")
        env = os.environ.copy()
        if author:
            # author format: 'Name <email>'
            env["GIT_AUTHOR_NAME"] = author.split(" <")[0] if "<" in author else author
            # leave email unset unless provided
        # Use subprocess.run with env
        p = subprocess.run(("git", "commit", "-m", message), cwd=str(self.repo_root), capture_output=True, text=True, env=env)
        # Get commit sha
        p2 = self._run_git("rev-parse", "HEAD")
        if p2.returncode == 0:
            return p2.stdout.strip()
        return ""

    def _workflow_path(self, tenant_id: str, name: str, version: str) -> Path:
        return self.repo_root / _safe_name(tenant_id) / _safe_name(name) / f"{_safe_name(version)}.yaml"

    def register_yaml(self, yaml_text: str, tenant_id: str, overwrite: bool = False, author: Optional[str] = None) -> str:
        wf = yaml.safe_load(yaml_text)
        name = wf.get("name")
        version = wf.get("version") or wf.get("metadata", {}).get("version") or "v0"
        if not name:
            raise ValueError("workflow must have a name")
        p = self._workflow_path(tenant_id, name, version)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not overwrite:
            raise FileExistsError(f"{p} exists; pass overwrite=True to replace")
        p.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
        msg = f"register workflow {tenant_id}/{name}@{version}"
        sha = self._commit_all(msg, author=author)
        return sha

    def list(self, tenant_id: Optional[str] = None):
        base = self.repo_root / (_safe_name(tenant_id) if tenant_id else "_global")
        if not base.exists():
            return []
        names = []
        for p in base.iterdir():
            if p.is_dir():
                names.append(p.name)
            elif p.suffix == ".yaml":
                names.append(p.stem)
        return sorted(names)

    def get(self, name: str, tenant_id: str, version: Optional[str] = None) -> dict:
        tenant_dir = self.repo_root / _safe_name(tenant_id)
        if version:
            p = tenant_dir / _safe_name(name) / f"{_safe_name(version)}.yaml"
            if not p.exists():
                raise FileNotFoundError(p)
            return yaml.safe_load(p.read_text(encoding="utf-8"))
        # find latest version by modification time
        wf_dir = tenant_dir / _safe_name(name)
        if wf_dir.exists() and wf_dir.is_dir():
            files = list(wf_dir.glob("*.yaml"))
            if not files:
                raise FileNotFoundError(name)
            latest = max(files, key=lambda p: p.stat().st_mtime)
            return yaml.safe_load(latest.read_text(encoding="utf-8"))
        # fallback to root file
        p = tenant_dir / f"{_safe_name(name)}.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8"))
        raise FileNotFoundError(name)

    def delete(self, name: str, tenant_id: str, version: Optional[str] = None, author: Optional[str] = None) -> str:
        tenant_dir = self.repo_root / _safe_name(tenant_id)
        if version:
            p = tenant_dir / _safe_name(name) / f"{_safe_name(version)}.yaml"
            if p.exists():
                p.unlink()
            else:
                raise FileNotFoundError(p)
        else:
            # remove all versions
            wf_dir = tenant_dir / _safe_name(name)
            if wf_dir.exists() and wf_dir.is_dir():
                for f in wf_dir.glob("*.yaml"):
                    f.unlink()
                try:
                    wf_dir.rmdir()
                except Exception:
                    pass
            p = tenant_dir / f"{_safe_name(name)}.yaml"
            if p.exists():
                p.unlink()
        msg = f"delete workflow {tenant_id}/{name}@{version or 'all'}"
        sha = self._commit_all(msg, author=author)
        return sha

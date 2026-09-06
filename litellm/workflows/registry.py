import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml

from jsonschema import validate as jsonschema_validate, ValidationError

from .backends.git_registry import GitRegistry

SCHEMA_PATH = Path(__file__).with_name("schema.json")
DEFAULT_STORE = Path(os.environ.get("LITELLM_WORKFLOW_STORE", Path.home() / ".litellm" / "workflows"))
DEFAULT_BACKEND = os.environ.get("LITELLM_WORKFLOW_BACKEND", "git")

class WorkflowValidationError(Exception):
    pass


def _safe_name(name: str) -> str:
    # Basic sanitization for filenames; keep it simple
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in name)


class FilesystemRegistry:
    """
    Legacy simple filesystem registry kept for compatibility.
    """
    def __init__(self, store_dir: Optional[Path] = None):
        self.store_dir = Path(store_dir or DEFAULT_STORE)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.schema = json.load(f)

    def _tenant_dir(self, tenant_id: Optional[str]) -> Path:
        tid = tenant_id or "_global"
        p = self.store_dir / _safe_name(tid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def validate_syntax(self, workflow_obj: Dict[str, Any]) -> None:
        try:
            jsonschema_validate(instance=workflow_obj, schema=self.schema)
        except ValidationError as e:
            raise WorkflowValidationError(f"schema validation failed: {e.message}")

    def validate_semantics(self, workflow_obj: Dict[str, Any]) -> None:
        # Ensure entry exists and that all referenced nodes exist
        entry = workflow_obj.get("entry")
        nodes = workflow_obj.get("nodes", {})
        if entry not in nodes:
            raise WorkflowValidationError(f"entry '{entry}' not found in nodes")
        # collect node names
        node_names = set(nodes.keys())
        for name, node in nodes.items():
            for key in ("on_success", "on_failure", "join"):
                v = node.get(key)
                if not v:
                    continue
                targets = [v] if isinstance(v, str) else list(v)
                for t in targets:
                    if t not in node_names:
                        raise WorkflowValidationError(f"node '{name}' references unknown target '{t}' in '{key}'")
            # skill-specific checks
            ntype = node.get("type")
            if ntype == "skill":
                if not node.get("skill"):
                    raise WorkflowValidationError(f"node '{name}' of type 'skill' must have a 'skill' field")
                if "@" in node.get("skill") and node.get("skill").count("@") > 1:
                    raise WorkflowValidationError(f"node '{name}' skill field has invalid format: {node.get('skill')}")
            if ntype == "mcp":
                if not node.get("mcp_server") or not node.get("mcp_tool"):
                    raise WorkflowValidationError(f"node '{name}' of type 'mcp' must have 'mcp_server' and 'mcp_tool' fields")

    def validate(self, workflow_obj: Dict[str, Any]) -> None:
        self.validate_syntax(workflow_obj)
        self.validate_semantics(workflow_obj)

    def _publish_config_change(self, object_type: str = "litellm_workflowstable") -> None:
        # Best-effort: publish config change so other replicas resync. Non-blocking.
        try:
            from litellm.proxy.common_utils.config_sync_pubsub import publish_config_change_for_object_type
        except Exception:
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except Exception:
            try:
                asyncio.run(publish_config_change_for_object_type(object_type))
            except Exception:
                return
        else:
            try:
                loop.create_task(publish_config_change_for_object_type(object_type))
            except Exception:
                return

    def register_yaml(self, yaml_text: str, tenant_id: Optional[str] = None, overwrite: bool = False) -> Path:
        wf = yaml.safe_load(yaml_text)
        if not isinstance(wf, dict):
            raise WorkflowValidationError("workflow YAML must be a mapping/object")
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.schema = json.load(f)
        tid = tenant_id or wf.get("tenant_id")
        if tid is None:
            raise WorkflowValidationError("tenant_id must be provided either in YAML or as an argument")
        if "metadata" not in wf:
            wf["metadata"] = {}
        if "created_at" not in wf["metadata"]:
            from datetime import datetime
            wf["metadata"]["created_at"] = datetime.utcnow().isoformat() + "Z"
        self.validate(wf)
        name = wf.get("name")
        if not name:
            raise WorkflowValidationError("workflow missing 'name' property")
        p = self._tenant_dir(tid) / f"{_safe_name(name)}.yaml"
        if p.exists() and not overwrite:
            raise FileExistsError(f"{p} exists; pass overwrite=True to replace")
        p.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
        try:
            self._publish_config_change()
        except Exception:
            pass
        return p

    def list(self, tenant_id: Optional[str] = None) -> List[str]:
        d = self._tenant_dir(tenant_id)
        return [p.stem for p in sorted(d.glob("*.yaml"))]

    def get(self, name: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        p = self._tenant_dir(tenant_id) / f"{_safe_name(name)}.yaml"
        if not p.exists():
            raise FileNotFoundError(f"{p} not found")
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    def delete(self, name: str, tenant_id: Optional[str] = None) -> None:
        p = self._tenant_dir(tenant_id) / f"{_safe_name(name)}.yaml"
        if p.exists():
            p.unlink()
            try:
                self._publish_config_change()
            except Exception:
                pass
        else:
            raise FileNotFoundError(f"{p} not found")


class WorkflowRegistry:
    """
    Frontend registry that selects an implementation based on environment.
    """

    def __init__(self, store_dir: Optional[Path] = None):
        self.store_dir = Path(store_dir or DEFAULT_STORE)
        backend = DEFAULT_BACKEND.lower()
        if backend == "git":
            # initialize a git repo at store_dir if needed
            self.backend = GitRegistry(self.store_dir)
        else:
            self.backend = FilesystemRegistry(self.store_dir)
        # load schema
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.schema = json.load(f)

    def validate(self, workflow_obj: Dict[str, Any]) -> None:
        try:
            jsonschema_validate(instance=workflow_obj, schema=self.schema)
        except ValidationError as e:
            raise WorkflowValidationError(str(e))
        # semantic checks delegated to backend when registering

    def register_yaml(self, yaml_text: str, tenant_id: Optional[str] = None, overwrite: bool = False, author: Optional[str] = None) -> Any:
        wf = yaml.safe_load(yaml_text)
        if not isinstance(wf, dict):
            raise WorkflowValidationError("workflow YAML must be a mapping/object")
        tid = tenant_id or wf.get("tenant_id")
        if tid is None:
            raise WorkflowValidationError("tenant_id must be provided either in YAML or as an argument")
        # minimal semantic validation here
        entry = wf.get("entry")
        nodes = wf.get("nodes", {})
        if entry not in nodes:
            raise WorkflowValidationError(f"entry '{entry}' not found in nodes")
        # delegate to backend
        if hasattr(self.backend, "register_yaml"):
            if isinstance(self.backend, GitRegistry):
                return self.backend.register_yaml(yaml_text=yaml_text, tenant_id=tid, overwrite=overwrite, author=author)
            return self.backend.register_yaml(yaml_text=yaml_text, tenant_id=tid, overwrite=overwrite)
        raise NotImplementedError("backend does not support register_yaml")

    def list(self, tenant_id: Optional[str] = None) -> List[str]:
        return self.backend.list(tenant_id)

    def get(self, name: str, tenant_id: Optional[str] = None, version: Optional[str] = None) -> Dict[str, Any]:
        if isinstance(self.backend, GitRegistry):
            return self.backend.get(name=name, tenant_id=tenant_id or "_global", version=version)
        return self.backend.get(name=name, tenant_id=tenant_id)

    def delete(self, name: str, tenant_id: Optional[str] = None, version: Optional[str] = None, author: Optional[str] = None) -> Any:
        if isinstance(self.backend, GitRegistry):
            return self.backend.delete(name=name, tenant_id=tenant_id or "_global", version=version, author=author)
        return self.backend.delete(name=name, tenant_id=tenant_id)

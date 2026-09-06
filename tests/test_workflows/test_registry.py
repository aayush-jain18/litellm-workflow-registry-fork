import tempfile
import textwrap
from pathlib import Path
import yaml
import pytest
from litellm.workflows.registry import WorkflowRegistry, WorkflowValidationError

SAMPLE = textwrap.dedent("""
kind: Workflow
name: my-wf
version: 1.0.0
topology: orchestrator
tenant_id: tenant-123
entry: create_network
nodes:
  create_network:
    type: skill
    skill: provision.network_create@1.0.0
    on_success: done
  done:
    type: terminal
    status: succeeded
""")

def test_register_and_get(tmp_path: Path):
    store = tmp_path / "workflows"
    r = WorkflowRegistry(store_dir=store)
    p = r.register_yaml(SAMPLE)
    assert p.exists()
    wf = r.get("my-wf", tenant_id="tenant-123")
    assert wf["name"] == "my-wf"

def test_validation_fails_for_missing_required(tmp_path: Path):
    store = tmp_path / "workflows"
    r = WorkflowRegistry(store_dir=store)
    bad = yaml.safe_dump({"kind": "Workflow", "name": "x", "tenant_id": "t1"})  # missing nodes & entry
    with pytest.raises(WorkflowValidationError):
        r.register_yaml(bad)

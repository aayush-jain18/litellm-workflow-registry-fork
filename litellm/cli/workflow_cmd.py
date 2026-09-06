import click
import yaml
from pathlib import Path
from litellm.workflows.registry import WorkflowRegistry

@click.group("workflow")
def workflow_group():
    """Workflow registry commands."""
    pass

@workflow_group.command("register")
@click.argument("path", type=click.Path(exists=True))
@click.option("--tenant", "tenant_id", default=None, help="Tenant id for the workflow")
@click.option("--overwrite", is_flag=True, help="Overwrite existing workflow")
def register(path, tenant_id, overwrite):
    """Register a workflow YAML file"""
    r = WorkflowRegistry()
    txt = Path(path).read_text(encoding="utf-8")
    p = r.register_yaml(txt, tenant_id=tenant_id, overwrite=overwrite)
    click.echo(f"registered: {p}")

@workflow_group.command("list")
@click.option("--tenant", "tenant_id", default=None, help="Tenant id to list workflows for")
def list_cmd(tenant_id):
    r = WorkflowRegistry()
    for name in r.list(tenant_id):
        click.echo(name)

@workflow_group.command("show")
@click.argument("name")
@click.option("--tenant", "tenant_id", default=None, help="Tenant id")
def show(name, tenant_id):
    r = WorkflowRegistry()
    wf = r.get(name, tenant_id=tenant_id)
    click.echo(yaml.safe_dump(wf, sort_keys=False))

# litellm.workflows

A minimal workflow registry for litellm.

Goals:
- Validate workflow YAML against a JSON Schema.
- Provide a filesystem-backed registry for storing workflows: ~/.litellm/workflows/<tenant>/<name>.yaml
- Publish config-sync events on register/delete so multi-replica deployments can reload.
- Provide a CLI to register/list/show workflows.

Notes:
- This initial implementation is filesystem-backed for ease of review. For production, implement a GitRegistry (commit + audit) or SQL-backed registry and wire RBAC.
- Dependency: jsonschema, PyYAML, click.

Usage:
- Register: litellm workflow register path/to/wf.yaml --tenant tenant_id
- List: litellm workflow list --tenant tenant_id
- Show: litellm workflow show my-workflow --tenant tenant_id

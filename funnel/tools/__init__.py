"""Tool registry. Adding a capability = write a function + one Tool entry."""
from dataclasses import dataclass
from typing import Callable

from . import affinity, pitchbook, specter
from .base import error


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable
    writes: bool = False

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props)}


_DOMAIN = {"type": "string", "description": "Bare domain, e.g. acme.com"}
_ORG = {"type": "integer", "description": "Affinity organization id"}

TOOLS = [
    Tool("pitchbook_lookup", "Company profile, bio and investors from Pitchbook by domain.",
         _obj(domain=_DOMAIN), pitchbook.pitchbook_lookup),
    Tool("specter_lookup", "Company data from Specter by domain. Use when Pitchbook is empty/failing or to cross-check.",
         _obj(domain=_DOMAIN), specter.specter_lookup),
    Tool("affinity_find_company", "Find the company in Affinity by exact domain.",
         _obj(domain=_DOMAIN), affinity.affinity_find_company),
    Tool("affinity_pipeline_status", "Is this Affinity company already in the deal pipeline list?",
         _obj(organization_id=_ORG), affinity.affinity_pipeline_status),
    Tool("affinity_create_company", "Create a company in Affinity. Only when affinity_find_company returned empty.",
         _obj(name={"type": "string"}, domain=_DOMAIN), affinity.affinity_create_company, writes=True),
    Tool("affinity_add_to_pipeline", "Add a company to the deal pipeline. Safe to call if already present (no-op).",
         _obj(organization_id=_ORG), affinity.affinity_add_to_pipeline, writes=True),
    Tool("affinity_find_person", "Find a person in Affinity by email.",
         _obj(email={"type": "string"}), affinity.affinity_find_person),
    Tool("affinity_link_person", "Attach a contact (by email) to the company. Creates them only if first_name and last_name are given.",
         {"type": "object", "properties": {"email": {"type": "string"}, "organization_id": _ORG,
          "first_name": {"type": "string"}, "last_name": {"type": "string"}},
          "required": ["email", "organization_id"]}, affinity.affinity_link_person, writes=True),
    Tool("affinity_create_note", "Save a note on the company (duplicate-safe). kind: 'slack_post' for the original message, 'pitchbook_summary' for the company summary.",
         _obj(organization_id=_ORG, content={"type": "string"},
              kind={"type": "string", "enum": ["slack_post", "pitchbook_summary"]}),
         affinity.affinity_create_note, writes=True),
    Tool("affinity_get_list_fields", "Pipeline columns (Stage, Owners, ...) and the valid dropdown options for each.",
         {"type": "object", "properties": {}}, affinity.affinity_get_list_fields),
    Tool("affinity_set_list_entry_field", "Set a pipeline column on a list entry. Dropdown columns (Stage) take the option text; person columns (Owners) take an Affinity person id. mode 'replace' for Stage, 'add' for Owners.",
         {"type": "object", "properties": {"list_entry_id": {"type": "integer"}, "organization_id": _ORG,
          "field_name": {"type": "string"}, "value": {"type": ["string", "integer"]},
          "mode": {"type": "string", "enum": ["replace", "add"]}},
          "required": ["list_entry_id", "organization_id", "field_name", "value"]},
         affinity.affinity_set_list_entry_field, writes=True),
    Tool("affinity_get_notes", "All Affinity notes on the company, newest first.",
         _obj(organization_id=_ORG), affinity.affinity_get_notes),
    Tool("affinity_get_interactions", "Meetings, calls or emails logged against the company (metadata only), newest first.",
         {"type": "object", "properties": {
             "organization_id": _ORG,
             "kind": {"type": "string", "enum": ["meetings", "calls", "emails"]},
             "since_days": {"type": "integer", "description": "Lookback window, default 730"}},
          "required": ["organization_id", "kind"]}, affinity.affinity_get_interactions),
]
BY_NAME = {t.name: t for t in TOOLS}


def execute(cfg, name: str, args: dict) -> dict:
    tool = BY_NAME.get(name)
    if not tool:
        return error("request_error", f"Unknown tool {name}")
    try:
        return tool.fn(cfg, **args)
    except TypeError as exc:
        return error("request_error", f"Bad arguments for {name}: {exc}")
    except Exception as exc:  # a tool bug must not kill the whole run
        return error("tool_exception", f"{name} crashed: {exc!r}")

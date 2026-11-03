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
    needs_ctx: bool = False  # receives the inbound item (attachments) as `ctx`

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _obj(required=None, **props) -> dict:
    return {"type": "object", "properties": props, "required": list(props) if required is None else required}


_DOMAIN = {"type": "string", "description": "Bare domain, e.g. acme.com"}
_ORG = {"type": "integer", "description": "Affinity organization id"}
_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOLS = [
    # Task 1 - research (web search is added as a server-side tool in agent.py)
    Tool("pitchbook_lookup",
         "Pitchbook by domain: investors and deals (use for lead investors, total raised, last round date). "
         "Billed per section, so only request 'bio' if truly needed.",
         _obj(required=["domain"], domain=_DOMAIN,
              sections={"type": "array", "items": {"enum": ["investors", "deals", "bio"]}}),
         pitchbook.pitchbook_lookup),
    Tool("specter_lookup",
         "Specter company record by domain (momentum, headcount/hiring trends). Billed per call and cached: "
         "call at most once per company.", _obj(domain=_DOMAIN), specter.specter_lookup),
    # Task 2 - CRM history
    Tool("affinity_find_company", "Find the company in Affinity by exact domain.",
         _obj(domain=_DOMAIN), affinity.affinity_find_company),
    Tool("affinity_company_people", "People Affinity has at the company, with emails. Use to find the CEO/founders.",
         _obj(organization_id=_ORG), affinity.affinity_company_people),
    Tool("affinity_get_notes", "All Affinity notes on the company, newest first (meeting notes live here).",
         _obj(organization_id=_ORG), affinity.affinity_get_notes),
    Tool("affinity_get_interactions",
         "Meetings, calls or emails logged against the company (metadata only), newest first. "
         "Pass person_id to scope to one person such as the CEO.",
         _obj(required=["organization_id", "kind"], organization_id=_ORG,
              kind={"type": "string", "enum": ["meetings", "calls", "emails"]},
              since_days=_INT, person_id=_INT),
         affinity.affinity_get_interactions),
    Tool("affinity_list_files", "Files (decks, memos, models) attached to the company in Affinity.",
         _obj(organization_id=_ORG), affinity.affinity_list_files),
    Tool("affinity_read_file", "Read the text of an Affinity file (PDF/text). Use to find what the company "
         "projected previously.", _obj(required=["file_id"], file_id=_INT, name=_STR), affinity.affinity_read_file),
    # Task 3 - pipeline + notes
    Tool("affinity_pipeline_status", "Is this Affinity company already in the deal pipeline list?",
         _obj(organization_id=_ORG), affinity.affinity_pipeline_status),
    Tool("affinity_create_company", "Create a company in Affinity. Only when affinity_find_company returned empty.",
         _obj(name=_STR, domain=_DOMAIN), affinity.affinity_create_company, writes=True),
    Tool("affinity_add_to_pipeline", "Add a company to the deal pipeline. Safe to call if already present (no-op).",
         _obj(organization_id=_ORG), affinity.affinity_add_to_pipeline, writes=True),
    Tool("affinity_get_list_fields", "Pipeline columns (Stage, Owners, ...) and valid dropdown options.",
         {"type": "object", "properties": {}}, affinity.affinity_get_list_fields),
    Tool("affinity_set_list_entry_field",
         "Set a pipeline column on a list entry. Dropdowns take the option text; person columns take an Affinity "
         "person id. mode 'replace' for Stage, 'add' for Owners.",
         _obj(required=["list_entry_id", "organization_id", "field_name", "value"], list_entry_id=_INT,
              organization_id=_ORG, field_name=_STR, value={"type": ["string", "integer"]},
              mode={"type": "string", "enum": ["replace", "add"]}),
         affinity.affinity_set_list_entry_field, writes=True),
    Tool("affinity_find_internal_user", "Resolve a teammate's name (e.g. a partner mentioned in the message) to "
         "an Affinity person id for Owners.", _obj(name=_STR), affinity.affinity_find_internal_user),
    Tool("affinity_find_person", "Find a person in Affinity by email (e.g. the poster).",
         _obj(email=_STR), affinity.affinity_find_person),
    Tool("affinity_link_person", "Attach a contact (by email) to the company. Creates them only if first_name "
         "and last_name are given.",
         _obj(required=["email", "organization_id"], email=_STR, organization_id=_ORG,
              first_name=_STR, last_name=_STR), affinity.affinity_link_person, writes=True),
    Tool("affinity_create_note", "Save a note on the company (duplicate-safe). kind 'inbound' = the original "
         "message/email; 'research' = the research summary.",
         _obj(organization_id=_ORG, content=_STR, kind={"type": "string", "enum": ["inbound", "research"]}),
         affinity.affinity_create_note, writes=True),
    Tool("affinity_attach_inbound_file", "File an attachment from the inbound message/email (by its [index]) on "
         "the company in Affinity. Safe to repeat.", _obj(organization_id=_ORG, attachment_index=_INT),
         affinity.affinity_attach_inbound_file, writes=True, needs_ctx=True),
]
BY_NAME = {t.name: t for t in TOOLS}


def execute(cfg, name: str, args: dict, ctx=None) -> dict:
    tool = BY_NAME.get(name)
    if not tool:
        return error("request_error", f"Unknown tool {name}")
    kwargs = {**args, "ctx": ctx} if tool.needs_ctx else dict(args)
    try:
        return tool.fn(cfg, **kwargs)
    except TypeError as exc:
        return error("request_error", f"Bad arguments for {name}: {exc}")
    except Exception as exc:  # a tool bug must not kill the whole run
        return error("tool_exception", f"{name} crashed: {exc!r}")

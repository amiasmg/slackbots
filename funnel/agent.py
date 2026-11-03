import base64
import json
from dataclasses import dataclass, field

from .config import Config
from .models import Inbound
from .prompts import build_system
from .tools import TOOLS, execute

MAX_TURNS = 20
MAX_RESULT_CHARS = 12000
MAX_PDF_BYTES = 20_000_000


@dataclass
class AgentResult:
    text: str
    tool_calls: list = field(default_factory=list)

    @property
    def silent(self) -> bool:
        return self.text.strip() == "NO_REPLY" or not self.text.strip()


def _serialize(result: dict) -> str:
    text = json.dumps(result, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '..."[truncated]'
    return text


def _first_message(item: Inbound, url_hints, context: str) -> list:
    parts = []
    if context:
        parts.append(f"Thread context:\n{context}")
    if item.source == "email":
        parts.append(f"Forwarded email\nFrom: {item.sender}\nSubject: {item.subject}")
    parts.append(f"Message:\n{item.text}")
    if item.source == "slack" and item.sender:
        parts.append(f"Posted by: {item.sender}")
    if url_hints:
        parts.append("URLs detected (normalised): " + ", ".join(u["url"] for u in url_hints))
    docs, budget = [], MAX_PDF_BYTES
    if item.attachments:
        lines = []
        for i, a in enumerate(item.attachments):
            label = f"[{i}] {a.filename} ({a.content_type or 'unknown'}, {len(a.data) // 1024} KB)"
            if a.is_pdf and len(a.data) <= budget:
                budget -= len(a.data)
                docs.append({"type": "document", "title": a.filename, "source": {
                    "type": "base64", "media_type": "application/pdf",
                    "data": base64.standard_b64encode(a.data).decode()}})
                label += " - PDF attached below, read it"
            else:
                label += " - not readable here (can still be filed in Affinity)"
            lines.append(label)
        parts.append("Attachments:\n" + "\n".join(lines))
    return docs + [{"type": "text", "text": "\n\n".join(parts)}]


def run_agent(client, cfg: Config, item, url_hints=(), context: str = "") -> AgentResult:
    """Tool-calling loop. `client` is an anthropic.Anthropic (or a test double)."""
    if isinstance(item, str):
        item = Inbound(source="slack", text=item)
    messages = [{"role": "user", "content": _first_message(item, url_hints, context)}]
    tools = [t.spec() for t in TOOLS] + [{"type": cfg.web_search_tool, "name": "web_search", "max_uses": 6}]
    system, log = build_system(cfg), []
    pdf_budget = MAX_PDF_BYTES - sum(len(a.data) for a in item.attachments if a.is_pdf)

    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=cfg.model, max_tokens=4096, system=system,
                                      tools=tools, messages=messages)
        if resp.stop_reason == "pause_turn":  # server-side tool (web search) needs another round
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return AgentResult(text=text, tool_calls=log)
        messages.append({"role": "assistant", "content": resp.content})
        results, extras = [], []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            out = execute(cfg, block.name, dict(block.input), item)
            pdf = out.pop("_pdf", None)  # raw bytes never go through JSON
            if pdf is not None:
                if len(pdf) <= pdf_budget:
                    pdf_budget -= len(pdf)
                    extras.append({"type": "document", "title": f"{block.name} result", "source": {
                        "type": "base64", "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf).decode()}})
                else:
                    out = {"status": "unsupported", "message": "Scanned PDF too large to attach"}
            log.append({"tool": block.name, "input": dict(block.input), "status": out.get("status")})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": _serialize(out)})
        # documents ride in the same user turn, after the tool_result blocks
        messages.append({"role": "user", "content": results + extras})
    return AgentResult(text="⚠️ Agent hit its step limit before finishing; partial work may have been done.",
                       tool_calls=log)

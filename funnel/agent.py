import json
from dataclasses import dataclass, field

from .config import Config
from .prompts import SYSTEM
from .tools import TOOLS, execute

MAX_TURNS = 14
MAX_RESULT_CHARS = 12000


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


def run_agent(client, cfg: Config, message: str, url_hints: list = (), context: str = "", poster: str = "") -> AgentResult:
    """Tool-calling loop. `client` is an anthropic.Anthropic (or a test double)."""
    parts = []
    if context:
        parts.append(f"Thread context:\n{context}")
    parts.append(f"Slack message:\n{message}")
    if poster:
        parts.append(f"Posted by: {poster}")
    if url_hints:
        parts.append("URLs detected (normalised): " + ", ".join(u["url"] for u in url_hints))
    messages = [{"role": "user", "content": "\n\n".join(parts)}]
    log = []

    for _ in range(MAX_TURNS):
        resp = client.messages.create(
            model=cfg.model, max_tokens=2048, system=SYSTEM,
            tools=[t.spec() for t in TOOLS], messages=messages)
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return AgentResult(text=text, tool_calls=log)
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            out = execute(cfg, block.name, dict(block.input))
            log.append({"tool": block.name, "input": dict(block.input), "status": out.get("status")})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": _serialize(out)})
        messages.append({"role": "user", "content": results})
    return AgentResult(text="⚠️ Agent hit its step limit before finishing; partial work may have been done.",
                       tool_calls=log)

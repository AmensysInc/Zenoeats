"""The conversation loop: the model asks for tools, we decide what runs.

ONE LOOP, THREE AUDIENCES. The restaurant dashboard, the platform admin and
the storefront each hand this a system prompt and a set of tools. What a tool
does is the caller's business; this file only shuttles messages between the
model (provider.py) and those tools, and stops.

THE MODEL NEVER WRITES. A tool either returns text for the model to read, or
returns a Stop -- an answer to put in front of the person as it is. Every
change on the restaurant side is a Stop carrying a proposal, which is the same
"Yes, do it" button the rule-based assistant has always used. So a model that
misreads a sentence costs a line on a screen, exactly as before.

BOUNDED. ASSISTANT_MAX_STEPS tool rounds, and tool output is cut short before
it is sent: a runaway loop or a 400-item menu is a bill, not an answer.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import settings
from app.services.assistant import provider

log = logging.getLogger(__name__)

# What one tool result may put in front of the model. A whole menu fits; a
# table that does not is cut rather than sent.
MAX_TOOL_CHARS = 12_000
# How much of the conversation is sent back with each turn.
MAX_HISTORY = 12
MAX_HISTORY_CHARS = 2_000


@dataclass
class Tool:
    """One thing the model may ask for. `parameters` is JSON Schema."""

    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Stop:
    """End the turn here and show this, as it is. Used for proposals."""

    text: str
    proposal: dict | None = None
    options: list[str] = field(default_factory=list)
    destructive: bool = False


def history_messages(history: list[dict]) -> list[dict]:
    """The earlier turns, as the browser kept them. Only plain text, only
    the two roles, and only the recent ones: the browser is not trusted to
    hand back anything shaped like a system message or a tool result."""
    out = []
    for turn in history[-MAX_HISTORY:]:
        role = turn.get("role")
        text = turn.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        out.append({"role": role, "content": text[:MAX_HISTORY_CHARS]})
    return out


def run(
    *,
    system: str,
    history: list[dict],
    text: str,
    tools: list[Tool],
    call: Callable[[str, dict], "str | Stop"],
) -> Stop:
    """One turn: the person's sentence in, something to show out.

    Raises provider.AssistantUnavailable when the model cannot be reached,
    which the endpoints turn into a plain "try again".
    """
    messages: list[dict] = [{"role": "system", "content": system}]
    messages += history_messages(history)
    messages.append({"role": "user", "content": text})
    schemas = [tool.schema() for tool in tools]
    known = {tool.name for tool in tools}

    for _ in range(max(1, settings.ASSISTANT_MAX_STEPS)):
        reply = provider.complete(messages, schemas)
        if not reply.tool_calls:
            return Stop(text=reply.text or "I don't have an answer for that.")

        # The model's request goes back in the transcript exactly as it was
        # made, so the results that follow can be matched to it.
        messages.append({
            "role": "assistant",
            "content": reply.text,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in reply.tool_calls
            ],
        })
        for c in reply.tool_calls:
            if c.name not in known:
                result: str | Stop = f"There is no tool called {c.name}."
            else:
                try:
                    result = call(c.name, c.arguments)
                except (ValueError, KeyError, TypeError) as refused:
                    # Bad arguments from the model. Said back to it, so it can
                    # ask the person rather than guess again.
                    result = f"That did not work: {refused}"
            if isinstance(result, Stop):
                return result
            if len(result) > MAX_TOOL_CHARS:
                result = result[:MAX_TOOL_CHARS] + "\n[cut short]"
            messages.append({"role": "tool", "tool_call_id": c.id, "content": result})

    log.warning("assistant stopped after %s tool rounds", settings.ASSISTANT_MAX_STEPS)
    return Stop(text="That took more steps than I'm allowed. Try asking for one thing at a time.")

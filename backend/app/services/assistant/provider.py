"""Talking to the model, and saying plainly when we cannot.

ONE PLACE A PROVIDER IS SPOKEN TO. Everything above this file deals in
messages and tool calls; nothing above it knows whose API answered. The wire
format is OpenAI's chat completions with tool calling, which is also what
Groq, OpenRouter, Together and a local Ollama serve -- so moving to a
cheaper or self-hosted model is a change to two settings rather than to any
code.

WHY IT REFUSES RATHER THAN DEGRADES. There is no key on this deployment yet.
An assistant that answered "I'm not sure" when the real problem is a missing
credential would send an admin hunting through their own menu for a fault
that is not there. So an unconfigured provider raises, the endpoint says it
is ours to fix, and the screen does not offer a box to type into.

WHAT IS NOT SENT. The system prompt and the conversation, and tool results
the server produced. No customer names, addresses or phone numbers are put
in a tool result that does not need them -- see tools.py, which chooses what
each read returns rather than handing back a row.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class AssistantUnavailable(RuntimeError):
    """No provider is configured, or the one configured would not answer."""


@dataclass
class ToolCall:
    """The model asking for one thing to be done."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    """What came back: something to say, something to do, or both."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def configured() -> bool:
    """Whether the assistant can be used at all.

    Read by the screen before it offers a prompt box, so an admin is told
    the feature is off rather than typing a question into something that
    cannot answer.
    """
    return bool(settings.OPENAI_API_KEY and settings.OPENAI_MODEL)


def complete(messages: list[dict], tools: list[dict]) -> Reply:
    """One turn of the conversation.

    Returns what the model said and any tools it wants run. Running them is
    the caller's business -- this file has no access to the database and no
    idea what a menu is, which is what keeps "what the model asked for" and
    "what we are willing to do" separate.
    """
    if not configured():
        raise AssistantUnavailable(
            "The assistant is not configured on this deployment. Set "
            "OPENAI_API_KEY (and OPENAI_MODEL) on the API container."
        )

    payload = {
        "model": settings.OPENAI_MODEL,
        "messages": messages,
        # Deterministic enough to be predictable twice running. This is
        # filling in forms, not writing prose.
        "temperature": 0,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        response = httpx.post(
            f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            timeout=settings.ASSISTANT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        log.error("assistant provider unreachable: %s", type(exc).__name__)
        raise AssistantUnavailable("The assistant did not answer. Try again.") from exc

    if response.status_code >= 400:
        # The provider's own message is for whoever runs the deployment --
        # a spent quota, a revoked key, a model name that no longer exists --
        # and none of it is an admin's to fix or to read.
        log.error(
            "assistant provider refused: %s %s",
            response.status_code, response.text[:300],
        )
        raise AssistantUnavailable("The assistant did not answer. Try again.")

    return _read(response.json())


def _read(body: dict) -> Reply:
    """The one message out of the envelope, defensively.

    Written to survive a provider that adds fields or omits optional ones,
    because four of them serve this format and they do not agree on the
    edges.
    """
    choices = body.get("choices") or []
    if not choices:
        raise AssistantUnavailable("The assistant did not answer. Try again.")
    message = choices[0].get("message") or {}

    calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            # A model that produced unparseable arguments has not asked for
            # anything we can do. Dropped rather than guessed at.
            log.warning("assistant sent unparseable arguments for %s", name)
            continue
        if not isinstance(arguments, dict):
            continue
        calls.append(ToolCall(id=raw.get("id") or name, name=name, arguments=arguments))

    text = message.get("content")
    return Reply(text=text.strip() if isinstance(text, str) and text.strip() else None,
                 tool_calls=calls)

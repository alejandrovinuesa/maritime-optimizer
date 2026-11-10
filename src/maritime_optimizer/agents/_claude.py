"""Shared Claude API settings for the agents."""

from typing import Any, Optional

MODEL = "claude-opus-5-5"
# Server-side refusal fallback: if a safety classifier declines, the API re-runs the same
# request on a fallback model chosen by refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_KWARGS = {"betas": [FALLBACK_BETA], "fallbacks": "default"}


class AgentRefusal(RuntimeError):
    """Raised when the model (and its fallback chain) declined the request."""


def make_client(client: Optional[Any] = None):
    """Return ``client`` or a default Anthropic client (credentials from the environment)."""
    if client is not None:
        return client
    try:
        import anthropic
    except ImportError as exc:      # pragma: no cover - depends on the installed extras
        raise ImportError("The agents need the 'agents' extra: "
                          "pip install 'maritime-optimizer[agents]'") from exc
    return anthropic.Anthropic()


def check_refusal(message: Any) -> None:
    """Raise AgentRefusal if the response ended in a refusal."""
    if getattr(message, "stop_reason", None) == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise AgentRefusal(f"The model declined the request (category: {category}).")


def text_of(message: Any) -> str:
    """Concatenated text blocks of a response."""
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()

"""Throwaway spike: can MCP clients answer questions *during* a tool call?

Three tools ask the same two questions about a game:

* ``ask_with_resolve`` - the SDK's ``Resolve`` + ``Elicit`` (form elicitation). Needs the client's
  ``elicitation`` capability; otherwise the call fails with MISSING_REQUIRED_CLIENT_CAPABILITY.
* ``ask_with_questions`` - no client support needed: returns a structured ``questions`` payload; the host model asks
  the user in chat and calls the tool again with ``answers``.
* ``ask_adaptive`` - elicits when the client declared the capability, otherwise falls back to ``questions``.

Run for a manual client test (see docs/SPIKE_RESOLVE.md):
    uv run python spikes/resolve/server.py            # stdio
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from pydantic import BaseModel, Field

server = MCPServer("resolve-spike")


class GameBasics(BaseModel):
    genre: str = Field(description="Main genre, e.g. 'co-op party game'")
    max_players: int = Field(description="Maximum number of players", ge=1, le=64)


QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "game.genre",
        "question": "What is the main genre of your game?",
        "suggestion": "co-op party game",
        "type": "string",
    },
    {
        "id": "game.max_players",
        "question": "How many players can play together at most?",
        "suggestion": 4,
        "type": "integer",
        "minimum": 1,
        "maximum": 64,
    },
]


def ask_basics() -> Elicit[GameBasics]:
    return Elicit("Two quick questions about your game (spike).", GameBasics)


@server.tool()
def ask_with_resolve(basics: Annotated[GameBasics, Resolve(ask_basics)]) -> str:
    """Spike A. Asks the user two questions through MCP elicitation, then echoes the answers."""
    return f"genre={basics.genre!r}, max_players={basics.max_players}"


@server.tool()
def ask_with_questions(answers: dict[str, str] | None = None) -> dict[str, Any]:
    """Spike B. Without `answers`, returns questions for you (the assistant) to ask the user in chat, each with a
    suggested answer. Call again with `answers` mapping each question id to the user's reply."""
    if not answers:
        return {"status": "needs_input", "questions": QUESTIONS}
    missing = [q["id"] for q in QUESTIONS if q["id"] not in answers]
    if missing:
        return {"status": "needs_input", "questions": [q for q in QUESTIONS if q["id"] in missing]}
    return {"status": "done", "received": answers}


def maybe_ask_basics(ctx: Context) -> Elicit[GameBasics] | None:
    """Elicit only when the client declared form elicitation; otherwise inject None (no error)."""
    caps = ctx.client_capabilities
    if caps is not None and caps.elicitation is not None:
        return Elicit("Two quick questions about your game (spike, adaptive).", GameBasics)
    return None


@server.tool()
def ask_adaptive(basics: Annotated[GameBasics | None, Resolve(maybe_ask_basics)]) -> dict[str, Any]:
    """Spike C. Asks through elicitation when this client supports it, otherwise returns `questions` like spike B."""
    if basics is not None:
        return {"status": "done", "via": "elicitation", "received": basics.model_dump()}
    return {"status": "needs_input", "via": "questions", "questions": QUESTIONS}


if __name__ == "__main__":
    server.run()

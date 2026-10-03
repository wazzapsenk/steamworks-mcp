"""In-memory checks for the Resolve spike: one client with elicitation support, one without.

Run: uv run pytest spikes/resolve -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp_types import ElicitResult

sys.path.insert(0, str(Path(__file__).parent))
from server import server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["auto", "legacy"])
def mode(request: pytest.FixtureRequest) -> str:
    """auto = 2026-07-28 protocol (InputRequiredResult); legacy = initialize handshake (mid-call requests)."""
    return str(request.param)


async def answer(context: Any, params: Any) -> ElicitResult:
    return ElicitResult(action="accept", content={"genre": "co-op party game", "max_players": 4})


async def decline(context: Any, params: Any) -> ElicitResult:
    return ElicitResult(action="decline")


def text_of(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


async def test_resolve_works_when_client_supports_elicitation(mode: str) -> None:
    async with Client(server, elicitation_callback=answer, mode=mode) as client:
        result = await client.call_tool("ask_with_resolve", {})
    assert not result.is_error, text_of(result)
    assert "co-op party game" in text_of(result)


async def test_resolve_fails_without_elicitation_capability(mode: str) -> None:
    # Not a tool error result: the server answers the call with a protocol error (MISSING_REQUIRED_CLIENT_CAPABILITY).
    async with Client(server, mode=mode) as client:
        with pytest.raises(Exception, match="elicitation"):
            await client.call_tool("ask_with_resolve", {})


async def test_resolve_decline_aborts_the_call(mode: str) -> None:
    async with Client(server, elicitation_callback=decline, mode=mode) as client:
        result = await client.call_tool("ask_with_resolve", {})
    assert result.is_error


async def test_questions_round_trip_needs_no_client_support(mode: str) -> None:
    async with Client(server, mode=mode) as client:
        first = await client.call_tool("ask_with_questions", {})
        second = await client.call_tool(
            "ask_with_questions", {"answers": {"game.genre": "co-op party game", "game.max_players": "4"}}
        )
    assert first.structured_content is not None
    assert first.structured_content["status"] == "needs_input"
    assert {q["id"] for q in first.structured_content["questions"]} == {"game.genre", "game.max_players"}
    assert second.structured_content is not None
    assert second.structured_content["status"] == "done"


async def test_adaptive_picks_the_right_path(mode: str) -> None:
    async with Client(server, elicitation_callback=answer, mode=mode) as with_elicit:
        a = await with_elicit.call_tool("ask_adaptive", {})
    async with Client(server, mode=mode) as without:
        b = await without.call_tool("ask_adaptive", {})
    assert a.structured_content is not None and a.structured_content["via"] == "elicitation"
    assert b.structured_content is not None and b.structured_content["via"] == "questions"

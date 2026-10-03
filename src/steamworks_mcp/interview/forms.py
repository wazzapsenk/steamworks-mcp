"""Interview questions as an MCP elicitation form (only for clients that declare form elicitation)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from steamworks_mcp.interview.questions import Question

SEP = "__"
_LITERAL: Any = Literal  # options are only known at runtime


def form_key(question_id: str) -> str:
    return question_id.replace(".", SEP)


def field_id(form_key_: str) -> str:
    return form_key_.replace(SEP, ".")


def _annotation(q: Question) -> Any:
    match q.kind:
        case "bool":
            return bool | None
        case "int":
            return int | None
        case "number":
            return float | None
        case "choice" if q.options:
            return _LITERAL[tuple(q.options)] | None
        case "multi" if q.options:
            return list[_LITERAL[tuple(q.options)]] | None  # type: ignore[misc]
    return str | None


def _default(q: Question) -> Any:
    if q.suggestion is None:
        return None
    if q.kind == "list" and isinstance(q.suggestion, list):
        return ", ".join(str(x) for x in q.suggestion)
    if q.kind in ("text", "long_text", "path", "date") and not isinstance(q.suggestion, str):
        return str(q.suggestion)
    return q.suggestion


def form_model(questions: list[Question]) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for q in questions:
        hint = " (comma-separated)" if q.kind == "list" else " (YYYY-MM-DD)" if q.kind == "date" else ""
        fields[form_key(q.id)] = (_annotation(q), Field(default=_default(q), description=q.question + hint))
    return create_model("InterviewAnswers", **fields)

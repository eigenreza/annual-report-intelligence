"""Rewrite a follow-up question into a standalone one using the conversation so far.

The standalone question drives retrieval. The original wording is still what the
answer responds to.
"""

from __future__ import annotations

from typing import Callable

Completer = Callable[[str, str], str]

REWRITE_SYSTEM = """You rewrite the latest message of a conversation into one self-contained question.

The conversation is between a financial analyst and an assistant that answers from annual reports.
Resolve references such as "it", "they", "that year", "the year before", "the other company" or
"what about ..." using the earlier turns. Keep the company names, years and metrics that the analyst
means. Do not add new requirements, do not answer the question, and do not change its intent.
If the latest message is already self-contained, return it unchanged.

Return only the rewritten question."""

HISTORY_TURNS = 4
ANSWER_PREVIEW_CHARS = 600


def rewrite_question(complete: Completer, question: str, history: list[tuple[str, str]]) -> str:
    """Return a standalone version of `question`. `history` holds (question, answer)
    pairs, oldest first. Without history the question is returned unchanged and no
    model call is made."""
    if not history:
        return question.strip()
    recent = history[-HISTORY_TURNS:]
    lines = []
    for asked, answered in recent:
        preview = " ".join(answered.split())[:ANSWER_PREVIEW_CHARS]
        lines.append(f"Analyst: {asked}\nAssistant: {preview}")
    user = "Conversation so far:\n\n" + "\n\n".join(lines) + f"\n\nLatest message: {question}"
    rewritten = complete(REWRITE_SYSTEM, user).strip().strip('"')
    if not rewritten or len(rewritten) > 3 * len(question) + 200:
        return question.strip()
    return rewritten

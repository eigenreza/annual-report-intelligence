"""Run the benchmark questions through the full pipeline and write eval/results.md.

Each question starts a fresh conversation. Follow-up questions run in the same
conversation as their parent so the rewriting step is exercised. Verdicts come
from string checks against the corpus-verified reference figures in questions.py
and are meant to be read together with the answers, not instead of them.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

from eval.questions import QUESTIONS, Benchmark, FollowUp
from src.config import chat_model
from src.index.retrieve import DEFAULT_K
from src.pipeline import Pipeline, Turn

RESULTS = Path(__file__).with_name("results.md")

REFUSAL = re.compile(
    r"(?:(?:not|aren't|isn't|are not|is not) (?:in|part of|included|available|contained|covered|provided)"
    r"|do(?:es)? not (?:contain|include|cover|provide))",
    re.IGNORECASE,
)

VERDICTS = {
    "correct": "correct and grounded",
    "weak": "correct but weakly cited",
    "wrong": "wrong or missing figure",
    "leak": "leakage",
    "refused": "wrongly refused",
}


def check(text: str, expect, expect_regex, forbid) -> tuple[list[str], list[str]]:
    """Return (missing expectations, leaked figures)."""
    missing = [" / ".join(group) for group in expect if not any(s in text for s in group)]
    missing += [pattern for pattern in expect_regex if not re.search(pattern, text)]
    leaked = [s for s in forbid if s in text]
    return missing, leaked


def verdict(turn: Turn, expect, expect_regex, forbid) -> tuple[str, list[str]]:
    text = turn.answer.text
    missing, leaked = check(text, expect, expect_regex, forbid)
    if leaked:
        return VERDICTS["leak"], [f"leaked: {', '.join(leaked)}"]
    if missing:
        key = "refused" if (expect and not any(any(s in text for s in g) for g in expect) and REFUSAL.search(text)) else "wrong"
        return VERDICTS[key], [f"missing: {m}" for m in missing]
    if not turn.answer.has_citations:
        return VERDICTS["weak"], []
    return VERDICTS["correct"], []


def render_turn(turn: Turn) -> str:
    parts = []
    if turn.was_rewritten:
        parts.append(f"**Standalone question:** {turn.standalone}\n")
    parts.append("**Answer:**\n")
    parts.append(turn.answer.text + "\n")
    label = "**Cited sources:**" if turn.answer.cited else "**No explicit citations. Excerpts consulted:**"
    parts.append(label)
    parts.extend(f"- {line}" for line in turn.answer.sources())
    parts.append("\n<details><summary>Retrieved excerpts</summary>\n")
    for hit in turn.answer.hits:
        parts.append(f"- {hit.chunk.header} score={hit.score:.3f}")
    parts.append("\n</details>\n")
    return "\n".join(parts)


def render_benchmark(item: Benchmark, turn: Turn, result: str, notes: list[str]) -> str:
    lines = [f"## {item.id}. {item.question}\n", f"**Reference:** {item.reference}\n", f"**Verdict:** {result}"]
    if notes:
        lines.append("  (" + "; ".join(notes) + ")")
    lines.append("")
    lines.append(render_turn(turn))
    return "\n".join(lines)


def render_follow_up(item: Benchmark, follow: FollowUp, turn: Turn, result: str, notes: list[str]) -> str:
    rewritten_ok = all(s.lower() in turn.standalone.lower() for s in follow.rewrite_expect)
    lines = [
        f"### {item.id} follow-up: {follow.question}\n",
        f"**Reference:** {follow.reference}\n",
        f"**Rewrite check:** {'passed' if rewritten_ok else 'failed'} (expected mentions of {', '.join(follow.rewrite_expect)})\n",
        f"**Verdict:** {result}",
    ]
    if notes:
        lines.append("  (" + "; ".join(notes) + ")")
    lines.append("")
    lines.append(render_turn(turn))
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pipeline = Pipeline(progress=print)
    summary: list[tuple[str, str, str]] = []
    sections: list[str] = []

    for item in QUESTIONS:
        pipeline.reset()
        print(f"{item.id}: {item.question}")
        turn = pipeline.ask(item.question)
        result, notes = verdict(turn, item.expect, item.expect_regex, item.forbid)
        print(f"     -> {result}" + (f" ({'; '.join(notes)})" if notes else ""))
        summary.append((item.id, item.question, result))
        sections.append(render_benchmark(item, turn, result, notes))
        if item.follow_up:
            follow = item.follow_up
            print(f"{item.id} follow-up: {follow.question}")
            follow_turn = pipeline.ask(follow.question)
            print(f"     standalone: {follow_turn.standalone}")
            result, notes = verdict(follow_turn, follow.expect, follow.expect_regex, ())
            print(f"     -> {result}" + (f" ({'; '.join(notes)})" if notes else ""))
            summary.append((f"{item.id} follow-up", follow.question, result))
            sections.append(render_follow_up(item, follow, follow_turn, result, notes))

    header = [
        "# Benchmark results\n",
        f"Run on {dt.date.today().isoformat()} with model `{chat_model()}`, k = {DEFAULT_K}, "
        f"{len(pipeline.retriever.readable_chunks)} chunks indexed.\n",
        "Verdicts are string checks against corpus-verified reference figures: "
        "correct and grounded, correct but weakly cited, wrong or missing figure, leakage, wrongly refused.\n",
        "| # | Question | Verdict |",
        "|---|---|---|",
    ]
    header += [f"| {qid} | {question} | {result} |" for qid, question, result in summary]
    RESULTS.write_text("\n".join(header) + "\n\n" + "\n\n".join(sections) + "\n", encoding="utf-8")
    print(f"\nwritten: {RESULTS}")
    counts: dict[str, int] = {}
    for _, _, result in summary:
        counts[result] = counts.get(result, 0) + 1
    for result, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:2d}  {result}")


if __name__ == "__main__":
    main()

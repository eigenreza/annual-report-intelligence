"""Terminal REPL for asking questions about the annual reports."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markdown import Markdown

from src.config import chat_model
from src.pipeline import Pipeline, Turn

BANNER = """Annual Report Intelligence
Answers come from the reports in data/ and cite file and page.
Commands:  :sources  excerpts behind the last answer   :reset  clear conversation memory   :quit  exit
"""


def print_turn(console: Console, turn: Turn) -> None:
    if turn.was_rewritten:
        console.print(f"[dim]standalone question: {turn.standalone}[/dim]")
    console.print(Markdown(turn.answer.text))
    console.print()
    label = "Sources" if turn.answer.cited else "Excerpts consulted (no explicit citation in the answer)"
    console.print(f"[bold]{label}[/bold]")
    for line in turn.answer.sources():
        console.print(f"  {line}")
    console.print()


def print_sources(console: Console, turn: Turn | None) -> None:
    if turn is None:
        console.print("No answer yet.")
        return
    for n, hit in enumerate(turn.answer.hits, start=1):
        cited = "cited" if hit in turn.answer.cited else "retrieved"
        console.rule(f"[{n}] {hit.chunk.header}  score={hit.score:.3f}  {cited}")
        console.print(hit.chunk.text)
    console.print()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    console = Console()
    console.print(BANNER)
    try:
        pipeline = Pipeline(progress=console.print)
    except Exception as exc:  # a missing key or an unreadable corpus should read as a plain message
        console.print(f"[red]Could not start:[/red] {exc}")
        sys.exit(1)
    console.print(f"[dim]model: {chat_model()}   chunks indexed: {len(pipeline.retriever.chunks)}[/dim]\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not question:
            continue
        if question in (":quit", ":q", ":exit"):
            break
        if question == ":reset":
            pipeline.reset()
            console.print("Conversation memory cleared.\n")
            continue
        if question == ":sources":
            print_sources(console, pipeline.last)
            continue
        try:
            turn = pipeline.ask(question)
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}:[/red] {exc}\n")
            continue
        print_turn(console, turn)


if __name__ == "__main__":
    main()

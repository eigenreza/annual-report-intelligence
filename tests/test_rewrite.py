from src.answer.rewrite import REWRITE_SYSTEM, rewrite_question


def test_without_history_the_question_is_returned_and_no_call_is_made():
    calls = []

    def complete(system: str, user: str) -> str:
        calls.append(user)
        return "unused"

    assert rewrite_question(complete, " What was Tesla revenue in 2023? ", []) == "What was Tesla revenue in 2023?"
    assert calls == []


def test_follow_up_is_rewritten_with_the_history_in_the_prompt():
    seen = {}

    def complete(system: str, user: str) -> str:
        seen["system"], seen["user"] = system, user
        return "\"What was Tesla's revenue in 2022?\""

    history = [("How much revenue did Tesla generate in 2023?", "Tesla generated 96,773 million USD in 2023.")]
    assert rewrite_question(complete, "and the year before?", history) == "What was Tesla's revenue in 2022?"
    assert seen["system"] == REWRITE_SYSTEM
    assert "How much revenue did Tesla generate in 2023?" in seen["user"]
    assert "Latest message: and the year before?" in seen["user"]


def test_degenerate_model_output_falls_back_to_the_original():
    history = [("q", "a")]
    assert rewrite_question(lambda s, u: "", "and 2021?", history) == "and 2021?"
    assert rewrite_question(lambda s, u: "x" * 5000, "and 2021?", history) == "and 2021?"


def test_only_recent_turns_are_included():
    seen = {}

    def complete(system: str, user: str) -> str:
        seen["user"] = user
        return "ok?"

    history = [(f"question {i}", f"answer {i}") for i in range(10)]
    rewrite_question(complete, "and?", history)
    assert "question 9" in seen["user"]
    assert "question 0" not in seen["user"]

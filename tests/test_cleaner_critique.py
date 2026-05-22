"""Tests for self-critique retry in LLMCleanerAgent.

When the first extraction returns nothing or an empty shell, the cleaner
should retry once with a critique prompt that embeds the previous output
and asks the LLM to re-examine the page.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from src.agents.cleaner_agent import (
    LLMCleanerAgent,
    ParsedProgramData,
    ParsedDeadline,
    ParsedTuition,
)
from src.models.admission import CurrencyCode


def _good_parsed() -> ParsedProgramData:
    return ParsedProgramData(
        tuition=ParsedTuition(amount=Decimal("100000"), currency=CurrencyCode.HKD),
    )


def _empty_shell_parsed() -> ParsedProgramData:
    """A ParsedProgramData with no content fields (no tuition / deadlines /
    requirements). Faculty alone is not enough — that's still an empty shell."""
    return ParsedProgramData(faculty="Faculty of Engineering")


class TestCleanMarkdownWithCritique:
    def test_good_first_attempt_skips_retry(self) -> None:
        """When the first attempt is already good, retry is not invoked."""
        agent = LLMCleanerAgent(router=MagicMock())
        single_pass = MagicMock(return_value=_good_parsed())
        agent._parse_single_pass = single_pass  # type: ignore[method-assign]
        agent._parse_rolling_chunks = MagicMock()  # type: ignore[method-assign]

        result = agent.clean_markdown_with_critique(
            markdown="x",
            source_url="u",
            academic_year=2026,
        )

        assert result is not None
        assert result.tuition is not None
        assert single_pass.call_count == 1

    def test_none_first_attempt_triggers_retry(self) -> None:
        """When the first attempt returns None, a second attempt fires
        with a critique hint in the prompt."""
        agent = LLMCleanerAgent(router=MagicMock())

        call_args_log: list[str] = []

        def fake_parse_single_pass(markdown, source_url, name_hints, academic_year, **kwargs):
            call_args_log.append(markdown)
            if len(call_args_log) == 1:
                return None  # first call: nothing extracted
            return _good_parsed()  # second call: success after critique

        agent._parse_single_pass = fake_parse_single_pass  # type: ignore[method-assign]

        result = agent.clean_markdown_with_critique(
            markdown="page body",
            source_url="https://e.edu",
            academic_year=2026,
        )

        assert result is not None
        assert result.tuition is not None
        assert len(call_args_log) == 2
        # The second call must include a critique block in the markdown
        # passed to the parser.
        assert "previous extraction" in call_args_log[1].lower()

    def test_empty_shell_first_attempt_triggers_retry(self) -> None:
        """When the first attempt returns a shell with no content fields,
        retry is invoked."""
        agent = LLMCleanerAgent(router=MagicMock())

        attempts: list[ParsedProgramData | None] = []

        def fake_parse(markdown, source_url, name_hints, academic_year, **kwargs):
            if not attempts:
                attempts.append(_empty_shell_parsed())
                return attempts[-1]
            attempts.append(_good_parsed())
            return attempts[-1]

        agent._parse_single_pass = fake_parse  # type: ignore[method-assign]

        result = agent.clean_markdown_with_critique(
            markdown="x", source_url="u", academic_year=2026,
        )

        assert result is not None
        assert result.tuition is not None  # the good (retry) result won
        assert len(attempts) == 2

    def test_retry_failure_returns_best_available(self) -> None:
        """If retry also fails to produce content, return the better of the
        two attempts — never escalate to a third try."""
        agent = LLMCleanerAgent(router=MagicMock())

        calls = [0]

        def fake_parse(markdown, source_url, name_hints, academic_year, **kwargs):
            calls[0] += 1
            return _empty_shell_parsed()  # always empty shell

        agent._parse_single_pass = fake_parse  # type: ignore[method-assign]

        result = agent.clean_markdown_with_critique(
            markdown="x", source_url="u", academic_year=2026,
        )

        # Returns the (still bad) result so downstream gate can quarantine.
        assert result is not None
        assert calls[0] == 2  # exactly one retry, no infinite loop

    def test_both_attempts_none_returns_none(self) -> None:
        agent = LLMCleanerAgent(router=MagicMock())
        agent._parse_single_pass = MagicMock(return_value=None)  # type: ignore[method-assign]

        result = agent.clean_markdown_with_critique(
            markdown="x", source_url="u", academic_year=2026,
        )

        assert result is None
        assert agent._parse_single_pass.call_count == 2

    def _make_router_spy(self) -> MagicMock:
        """Build a router spy whose generate() returns a well-formed
        LLMResponse with a serialized ParsedProgramData JSON body."""
        spy = MagicMock()
        response = MagicMock()
        response.text = _good_parsed().model_dump_json()
        spy.return_value = response
        return spy

    def test_name_constraints_emit_must_be_one_of_directive(self) -> None:
        """When name_constraints is non-empty, the prompt sent to the LLM
        must contain a hard constraint (not just a hint), naming the
        allowed values and the explicit null escape valve."""
        agent = LLMCleanerAgent(router=MagicMock())
        spy = self._make_router_spy()
        agent.router.generate = spy  # type: ignore[method-assign]

        agent.clean_markdown_with_critique(
            markdown="page body about a finance program",
            source_url="https://e.edu/finance",
            academic_year=2026,
            name_constraints=["MSc Finance", "MSc Accounting"],
        )

        prompt = spy.call_args[0][0]
        # Hard constraint language must be present.
        assert "MUST be one of" in prompt or "must be one of" in prompt
        # Constraint values must appear verbatim.
        assert "MSc Finance" in prompt
        assert "MSc Accounting" in prompt
        # Null escape valve must be explicit so LLM doesn't fabricate.
        assert "null" in prompt.lower()

    def test_name_constraints_none_behaves_like_today(self) -> None:
        """No constraints supplied → no constraint directive in prompt."""
        agent = LLMCleanerAgent(router=MagicMock())
        spy = self._make_router_spy()
        agent.router.generate = spy  # type: ignore[method-assign]

        agent.clean_markdown_with_critique(
            markdown="page body",
            source_url="https://e.edu",
            academic_year=2026,
        )

        prompt = spy.call_args[0][0]
        assert "MUST be one of" not in prompt
        assert "must be one of" not in prompt

    def test_critique_hint_embeds_previous_output_as_text(self) -> None:
        """The critique prompt must include the previous (failed) output
        as data — not as an assistant message — to avoid LLM cognitive
        consistency bias (sticking with its previous answer)."""
        agent = LLMCleanerAgent(router=MagicMock())

        first_result = ParsedProgramData(faculty="Faculty of Music")
        attempts: list[str] = []

        def fake_parse(markdown, source_url, name_hints, academic_year, **kwargs):
            attempts.append(markdown)
            return first_result if len(attempts) == 1 else _good_parsed()

        agent._parse_single_pass = fake_parse  # type: ignore[method-assign]

        agent.clean_markdown_with_critique(
            markdown="original page",
            source_url="u",
            academic_year=2026,
        )

        critique_prompt = attempts[1]
        # Previous extraction's content must appear in the critique block.
        assert "Faculty of Music" in critique_prompt
        # The escape valve must be explicit.
        assert "null" in critique_prompt.lower() or "do not fabricate" in critique_prompt.lower()

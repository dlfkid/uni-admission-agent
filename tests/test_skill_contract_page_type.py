"""The tool schema the model actually sees must not offer a retired page type.

`auto` was retired everywhere the caller can reach — except here. These two
models are what `build_openai_tools()` turns into the JSON schema the LLM is
given, and both still defaulted to "auto" with the field optional. A model
that omitted it sent "auto" down to analyze_page_links, where
_determine_page_type now raises ValueError inside a background agent turn.
The retirement has to reach the schema, not just the REST boundary.
"""

import json

import pytest
from pydantic import ValidationError

from src.agent_runtime.skills.contracts import (
    AnalyzePageSkillInput,
    BrowserAutomationSkillInput,
)

_INPUTS = (AnalyzePageSkillInput, BrowserAutomationSkillInput)


@pytest.mark.parametrize("model", _INPUTS)
def test_an_omitted_hint_defaults_to_index(model) -> None:
    assert model(url="https://x.edu/i").page_type_hint == "index"


@pytest.mark.parametrize("model", _INPUTS)
@pytest.mark.parametrize("value", ["auto", "zzz", ""])
def test_a_retired_or_unknown_hint_is_rejected(model, value) -> None:
    with pytest.raises(ValidationError):
        model(url="https://x.edu/i", page_type_hint=value)


@pytest.mark.parametrize("model", _INPUTS)
def test_both_concrete_types_are_accepted(model) -> None:
    assert model(url="https://x.edu/i", page_type_hint="index").page_type_hint == "index"
    assert model(url="https://x.edu/d", page_type_hint="detail").page_type_hint == "detail"


@pytest.mark.parametrize("model", _INPUTS)
def test_the_json_schema_the_model_sees_offers_only_index_and_detail(model) -> None:
    schema = json.dumps(model.model_json_schema())
    assert '"auto"' not in schema
    assert "index" in schema and "detail" in schema


def test_no_skill_contract_still_defaults_to_auto() -> None:
    """Nothing else in this module may reintroduce it."""
    from pathlib import Path

    src = Path("src/agent_runtime/skills/contracts.py").read_text(encoding="utf-8")
    assert 'page_type_hint: str = "auto"' not in src

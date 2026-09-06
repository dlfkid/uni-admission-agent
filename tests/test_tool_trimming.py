import pytest

from src.agent_runtime.loop import build_openai_tools
from src.agent_runtime.skills.registry import build_skill_registry


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


_ESSENTIAL_TOOLS = {"browser_automation_skill", "persist_programs_skill", "analyze_page_skill", "paginated_crawl_skill"}


@pytest.fixture()
def registry():
    return build_skill_registry()


def test_no_hint_returns_all_tools(registry):
    """No hint = backward compatible, all tools included."""
    tools = build_openai_tools(registry, page_type_hint=None)
    names = _tool_names(tools)
    # Must include essential skills
    for name in _ESSENTIAL_TOOLS:
        assert name in names
    # Must include more than just essential tools
    assert len(names) > len(_ESSENTIAL_TOOLS)


def test_detail_hint_returns_only_essential_tools(registry):
    """Detail pages get only essential tools (browser + persist + analyze)."""
    tools = build_openai_tools(registry, page_type_hint="detail")
    names = _tool_names(tools)
    assert names == _ESSENTIAL_TOOLS


def test_index_hint_returns_only_essential_tools(registry):
    """Index pages get only essential tools (browser + persist + analyze)."""
    tools = build_openai_tools(registry, page_type_hint="index")
    names = _tool_names(tools)
    assert names == _ESSENTIAL_TOOLS


def test_crawl_has_fewer_tools_than_no_hint(registry):
    """Crawl modes should have significantly fewer tools than unrestricted."""
    crawl = build_openai_tools(registry, page_type_hint="index")
    full = build_openai_tools(registry, page_type_hint=None)
    assert len(crawl) < len(full)
    assert len(crawl) == len(_ESSENTIAL_TOOLS)


def test_include_task_false_with_no_hint(registry):
    """include_task=False excludes the task tool from full tool set."""
    tools_with = build_openai_tools(registry, page_type_hint=None, include_task=True)
    tools_without = build_openai_tools(registry, page_type_hint=None, include_task=False)
    names_with = _tool_names(tools_with)
    names_without = _tool_names(tools_without)
    if "task" in names_with:
        assert "task" not in names_without

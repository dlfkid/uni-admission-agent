import pytest

from src.agent_runtime.loop import build_openai_tools
from src.agent_runtime.skills.registry import build_skill_registry


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


@pytest.fixture()
def registry():
    return build_skill_registry()


def test_no_hint_returns_all_tools(registry):
    """No hint = backward compatible, all tools included."""
    tools = build_openai_tools(registry, page_type_hint=None)
    names = _tool_names(tools)
    # Must include team, protocol, worktree, autonomy
    assert "team_spawn" in names
    assert "protocol_request" in names
    assert "worktree_create" in names
    assert "idle" in names
    assert "task" in names  # subagent tool (include_task defaults True)


def test_detail_hint_excludes_collaboration_tools(registry):
    """Detail pages should NOT have team/subagent/protocol/worktree/autonomy."""
    tools = build_openai_tools(registry, page_type_hint="detail")
    names = _tool_names(tools)
    # Must include core skills
    assert "browser_automation_skill" in names
    assert "persist_programs_skill" in names
    assert "compact" in names
    assert "bg_run" in names
    # Must exclude collaboration tools
    assert "team_spawn" not in names
    assert "team_send" not in names
    assert "team_inbox" not in names
    assert "task" not in names  # subagent
    assert "protocol_request" not in names
    assert "protocol_respond" not in names
    assert "worktree_create" not in names
    assert "idle" not in names
    assert "claim_task" not in names


def test_detail_has_fewer_tools_than_no_hint(registry):
    """Detail mode should have significantly fewer tools than unrestricted."""
    detail = build_openai_tools(registry, page_type_hint="detail")
    full = build_openai_tools(registry, page_type_hint=None)
    # detail drops subagent(1) + team(3) + protocol(3) + worktree(5) + autonomy(2) = 14
    assert len(detail) == len(full) - 14


def test_index_hint_includes_team_but_not_protocol(registry):
    """Index pages get team + subagent, but not protocol/worktree/autonomy."""
    tools = build_openai_tools(registry, page_type_hint="index")
    names = _tool_names(tools)
    # Must include team + subagent
    assert "team_spawn" in names
    assert "team_send" in names
    assert "team_inbox" in names
    assert "task" in names
    # Must exclude protocol/worktree/autonomy
    assert "protocol_request" not in names
    assert "worktree_create" not in names
    assert "idle" not in names


def test_index_has_fewer_tools_than_no_hint(registry):
    """Index mode drops protocol(3) + worktree(5) + autonomy(2) = 10 tools."""
    index = build_openai_tools(registry, page_type_hint="index")
    full = build_openai_tools(registry, page_type_hint=None)
    assert len(index) == len(full) - 10


def test_include_task_false_overrides_index_hint(registry):
    """Subagent loops with index hint still exclude the task tool."""
    tools = build_openai_tools(
        registry, include_task=False, page_type_hint="index"
    )
    names = _tool_names(tools)
    assert "task" not in names
    # But team tools are still there for index
    assert "team_spawn" in names

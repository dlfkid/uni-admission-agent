"""LLM-driven agent loop (s01–s08 patterns).

s01: Core while-loop — User prompt → LLM → Tool → result → LLM (repeat)
s02: Multi-tool dispatch via SkillRegistry (loop body unchanged)
s03: TodoManager — structured planning with nag reminder injection
s04: Subagents — `task` tool spawns a child loop with fresh context
s05: Skills — on-demand knowledge loading via `load_skill` tool
s06: Context compact — three-layer compression to keep conversations within limits
s07: Task system — file-persisted task DAG with dependency tracking
s08: Background tasks — async skill execution with notification injection
s09: Agent teams — persistent teammates with JSONL mailbox communication
s10: Team protocols — generic request-response FSM for structured coordination
s11: Autonomous agents — self-organizing teammates with idle polling + task claiming
s12: Worktree isolation — git worktree per task with lifecycle event logging
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.agent_runtime.base import AgentEvent, EventSink
from src.agent_runtime.background import BackgroundManager
from src.agent_runtime.protocol import ProtocolManager

if TYPE_CHECKING:
    from src.agent_runtime.team import MessageBus, TeammateManager
from src.agent_runtime.context_compact import (
    auto_compact,
    micro_compact,
    should_auto_compact,
)
from src.agent_runtime.skills.registry import SkillDef, SkillRegistry
from src.agent_runtime.task_manager import TaskManager
from src.agent_runtime.skills.skill_loader import SkillLoader
from src.agent_runtime.todo import TodoManager
from src.agent_runtime.worktree import WorktreeManager

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 256
NAG_INTERVAL = 3  # inject reminder after this many iterations without a todo update
LLM_CALL_TIMEOUT = 480    # 8 minutes — single LLM API call
PAGE_TIMEOUT = 3600        # 60 minutes — entire agent_loop for one page


class AgentPageTimeout(Exception):
    """Raised when agent_loop exceeds PAGE_TIMEOUT."""

_SKILL_LOADER = SkillLoader()


def _emit_loop_event(
    event_sink: EventSink | None,
    event_type: str,
    **payload: Any,
) -> None:
    """Safely emit a loop lifecycle event."""
    if event_sink is None:
        return
    event: AgentEvent = {"type": event_type, **payload}
    event_sink(event)


def _emit_loop_done(
    event_sink: EventSink | None,
    *,
    iteration: int,
    reason: str,
    response_preview: str = "",
) -> None:
    """Emit a terminal loop event for any completion path."""
    _emit_loop_event(
        event_sink,
        "agent_done",
        iteration=iteration,
        reason=reason,
        response_preview=response_preview[:300],
    )


def _build_system_prompt() -> str:
    """Build the system prompt — minimal, direct, no distractions."""
    return """\
You are a program crawler.

## For index pages (page_type_hint contains "index" or "auto"):
1. Call browser_automation_skill(url=<given URL>, page_type_hint="index").
   The result contains `extracted_programs` — an array of fully structured program dicts.
2. Call persist_programs_skill ONCE with:
   - univ_slug and year from the user message
   - programs: the `extracted_programs` array AS-IS (do NOT modify or re-extract)
3. Respond with a summary. Do NOT call browser_automation_skill again.

## For detail pages (page_type_hint is "detail"):
1. Call browser_automation_skill(url=<given URL>, page_type_hint="detail").
   The result contains `extracted_programs` with one structured program dict.
2. Call persist_programs_skill with univ_slug, year, and the program from `extracted_programs`.
   Pass the program dict AS-IS — do NOT re-extract fields from HTML.
3. Respond with a summary.

CRITICAL: The `extracted_programs` data is already structured with correct field names
(name_en, faculty, tuition_amount, study_options, deadlines, requirements, etc.).
NEVER re-extract or reformat this data. Pass it directly to persist_programs_skill.

## For index pages with auto-pagination requested:
If the user message mentions auto-pagination, paginate, 翻页, all pages,
or collect all courses:
1. Call paginated_crawl_skill(url=<given URL>, univ_slug, year).
2. The skill handles pagination detection, multi-page fetching,
   quality checks, and extraction internally.
3. If status is "done": call persist_programs_skill ONCE with the
   returned programs array AS-IS.
4. If status is "quality_failed": report the warning to the user.
   Do NOT call persist_programs_skill — let the user decide.
5. If status is "pagination_not_supported": inform the user that
   SPA pagination was detected and auto-pagination is not yet supported.
"""


SYSTEM_PROMPT = _build_system_prompt()

TOOL_DESCRIPTIONS: dict[str, str] = {
    "analyze_page_skill": (
        "Analyze a URL to detect whether it is an index page (list of programs) "
        "or a detail page (single program). Returns page_type and extracted links. "
        "Requires html_content to be provided; use browser_automation_skill first "
        "if you only have a URL."
    ),
    "select_detail_candidates_skill": (
        "Select the top-k detail page URLs from a list of analyzed link candidates. "
        "Input: links array from analyze_page_skill output."
    ),
    "legacy_crawl_batch_skill": (
        "Legacy pipeline: crawl a batch of detail page URLs using the traditional "
        "ingestion pipeline (fetch + LLM parse + DB persist in one shot). "
        "NOT dry-run compatible. Prefer browser_automation_skill + persist_programs_skill "
        "for agent-driven crawls. Requires index_url, selected_urls, univ_slug, and year."
    ),
    "persist_programs_skill": (
        "Persist caller-structured program records to the database. "
        "Use only when you have pre-structured program data to store."
    ),
    "review_patch_skill": (
        "Apply a correction patch to a previously persisted program record."
    ),
    "query_db_skill": (
        "Query stored programs for a given university and optional year."
    ),
    "browser_automation_skill": (
        "Fetch page HTML content via a connected browser client. "
        "For index pages (page_type_hint='index'), returns selected_urls — "
        "a list of detected detail page URLs. "
        "For detail pages (page_type_hint='detail'), returns the page HTML."
    ),
    "paginated_crawl_skill": (
        "Crawl a multi-page or large index page with automatic pagination and "
        "quality checks. Use ONLY when the user explicitly requests auto-pagination "
        "(e.g. '翻页', 'paginate', 'all pages'). Handles URL-parameter pagination "
        "and large single-page indexes. Returns extracted programs from all pages."
    ),
}

# -- Built-in todo tool definition (not in SkillRegistry) ------------------

_TODO_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "todo",
        "description": (
            "Create or update your task plan. Pass the full list of items "
            "each time. Each item needs 'content' (what to do) and 'status' "
            "(pending / in_progress / completed). Only ONE item may be "
            "in_progress at a time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Task description",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Task status",
                            },
                        },
                        "required": ["content", "status"],
                    },
                    "description": "Full task list (replaces previous)",
                },
            },
            "required": ["items"],
        },
    },
}


# -- Built-in load_skill tool definition (s05: knowledge) ------------------

_LOAD_SKILL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": (
            "Load a knowledge skill by name. Returns the full guide content. "
            "Use this when you need detailed instructions for a specific "
            "workflow (e.g., 'crawl-workflow', 'data-quality', 'browser-tips')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the knowledge skill to load",
                    "enum": _SKILL_LOADER.list_names() or None,
                },
            },
            "required": ["name"],
        },
    },
}

# -- Built-in task tool definition (s04: subagent) -------------------------

_TASK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task",
        "description": (
            "Spawn a subagent with fresh context to handle a self-contained "
            "subtask. The subagent has access to all skill tools but cannot "
            "spawn further subagents. Only a text summary is returned — the "
            "subagent's full message history is discarded. Use this to keep "
            "your own context clean when a subtask requires many tool calls."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "A self-contained instruction for the subagent. "
                        "Include all necessary context (URLs, slugs, etc.) "
                        "since the subagent has no access to your conversation."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
}

# -- Built-in compact tool definition (s06: context compression) -----------

_COMPACT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "compact",
        "description": (
            "Manually compress the conversation history. Use when the context "
            "feels bloated or you're getting near the limit. Saves the full "
            "transcript to disk, then replaces the conversation with an LLM "
            "summary. You will lose direct access to previous tool results "
            "but retain a continuity summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

# -- Built-in task_* tool definitions (s07: task system) -------------------

_TASK_CREATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_create",
        "description": (
            "Create a persistent task in the task graph. Use for multi-step "
            "work that should survive context compression. Optionally specify "
            "blocked_by to declare dependencies on other task IDs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Short task title"},
                "description": {"type": "string", "description": "Details (optional)", "default": ""},
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of tasks that must complete before this one",
                    "default": [],
                },
            },
            "required": ["subject"],
        },
    },
}

_TASK_UPDATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_update",
        "description": (
            "Update a task's status or dependencies. Setting status to "
            "'completed' auto-unblocks downstream tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Task ID to update"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed"],
                    "description": "New status",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Additional upstream dependency IDs",
                },
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Additional downstream dependency IDs",
                },
            },
            "required": ["task_id"],
        },
    },
}

_TASK_LIST_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_list",
        "description": "List all tasks with their status and dependencies.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_TASK_GET_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "task_get",
        "description": "Get details of a specific task by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Task ID"},
            },
            "required": ["task_id"],
        },
    },
}

_TASK_GRAPH_TOOLS = [_TASK_CREATE_TOOL, _TASK_UPDATE_TOOL, _TASK_LIST_TOOL, _TASK_GET_TOOL]

# -- Built-in background tools (s08) --------------------------------------

_BG_RUN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bg_run",
        "description": (
            "Run a skill call in the background. Returns immediately with a "
            "task ID. The result will be injected as a notification before "
            "your next turn. Use this for slow operations (e.g., batch crawls) "
            "so you can continue working on other things."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to execute in the background",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments to pass to the skill",
                },
            },
            "required": ["skill_name", "args"],
        },
    },
}

_BG_CHECK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bg_check",
        "description": (
            "Check the status of a background task by ID, or list all "
            "background tasks if no ID is provided."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Background task ID (e.g., 'bg_1'). Omit to list all.",
                },
            },
        },
    },
}

# -- Built-in team tools (s09) ---------------------------------------------

_TEAM_SPAWN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "team_spawn",
        "description": (
            "Spawn a persistent teammate with its own agent loop. The teammate "
            "runs in the background with access to all skill tools. It will "
            "send its results to your inbox ('lead') when done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique teammate name (e.g., 'crawler_hku')"},
                "role": {"type": "string", "description": "Role description (e.g., 'crawl HKU programs')"},
                "prompt": {"type": "string", "description": "Initial task prompt for the teammate"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
}

_TEAM_SEND_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "team_send",
        "description": (
            "Send a message to a teammate's inbox. Use 'lead' to message "
            "the lead agent. The recipient will see it before their next LLM call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient name"},
                "content": {"type": "string", "description": "Message content"},
            },
            "required": ["to", "content"],
        },
    },
}

_TEAM_INBOX_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "team_inbox",
        "description": (
            "Read and drain your inbox. Returns all pending messages and "
            "clears them. Also shows current team roster."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_TEAM_TOOLS = [_TEAM_SPAWN_TOOL, _TEAM_SEND_TOOL, _TEAM_INBOX_TOOL]

# -- Built-in protocol tools (s10) ----------------------------------------

_PROTOCOL_REQUEST_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "protocol_request",
        "description": (
            "Send a structured request to another agent (e.g., shutdown, "
            "plan_approval). Creates a tracked request with a unique ID. "
            "The target agent can approve or reject it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "protocol": {
                    "type": "string",
                    "description": "Protocol type (e.g., 'shutdown', 'plan_approval')",
                },
                "target": {
                    "type": "string",
                    "description": "Target agent name",
                },
                "description": {
                    "type": "string",
                    "description": "What you're requesting and why",
                },
            },
            "required": ["protocol", "target", "description"],
        },
    },
}

_PROTOCOL_RESPOND_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "protocol_respond",
        "description": (
            "Approve or reject a pending protocol request by its request_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "The request ID to respond to",
                },
                "approve": {
                    "type": "boolean",
                    "description": "True to approve, False to reject",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for approval/rejection",
                    "default": "",
                },
            },
            "required": ["request_id", "approve"],
        },
    },
}

_PROTOCOL_STATUS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "protocol_status",
        "description": "List all protocol requests and their status.",
        "parameters": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["pending", "approved", "rejected"],
                    "description": "Filter by status (optional)",
                },
            },
        },
    },
}

_PROTOCOL_TOOLS = [_PROTOCOL_REQUEST_TOOL, _PROTOCOL_RESPOND_TOOL, _PROTOCOL_STATUS_TOOL]

# -- Built-in autonomy tools (s11) ----------------------------------------

_IDLE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "idle",
        "description": (
            "Enter idle mode. Call this when your current work is done and "
            "you want to wait for new tasks or messages. The system will "
            "poll for inbox messages and unclaimed tasks on the board."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_CLAIM_TASK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "claim_task",
        "description": (
            "Claim an unclaimed task from the task board. Sets you as owner "
            "and moves it to in_progress."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "Task ID to claim"},
            },
            "required": ["task_id"],
        },
    },
}

_AUTONOMY_TOOLS = [_IDLE_TOOL, _CLAIM_TASK_TOOL]

# -- Built-in worktree tools (s12) ----------------------------------------

_WORKTREE_CREATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "worktree_create",
        "description": (
            "Create a new git worktree with its own branch. Optionally bind "
            "it to a task ID so the task moves to in_progress automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique worktree name (e.g., 'crawl-hku')"},
                "task_id": {
                    "type": "integer",
                    "description": "Task ID to bind (optional)",
                },
            },
            "required": ["name"],
        },
    },
}

_WORKTREE_RUN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "worktree_run",
        "description": (
            "Execute a shell command inside a worktree directory. "
            "Use this to run tests, scripts, or git commands in isolation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Worktree name"},
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 300)",
                    "default": 300,
                },
            },
            "required": ["name", "command"],
        },
    },
}

_WORKTREE_LIST_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "worktree_list",
        "description": "List all worktrees and their status.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_WORKTREE_KEEP_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "worktree_keep",
        "description": "Mark a worktree as kept (preserved for future use).",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Worktree name"},
            },
            "required": ["name"],
        },
    },
}

_WORKTREE_REMOVE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "worktree_remove",
        "description": (
            "Remove a worktree. Optionally complete the bound task and/or "
            "force-remove if there are uncommitted changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Worktree name"},
                "complete_task": {
                    "type": "boolean",
                    "description": "Also mark the bound task as completed",
                    "default": False,
                },
                "force": {
                    "type": "boolean",
                    "description": "Force removal even with uncommitted changes",
                    "default": False,
                },
            },
            "required": ["name"],
        },
    },
}

_WORKTREE_TOOLS = [
    _WORKTREE_CREATE_TOOL,
    _WORKTREE_RUN_TOOL,
    _WORKTREE_LIST_TOOL,
    _WORKTREE_KEEP_TOOL,
    _WORKTREE_REMOVE_TOOL,
]

SUBAGENT_SYSTEM_PROMPT = """\
You are a subagent handling a delegated task. You have access to skill tools \
to accomplish your goal. Work step by step, then respond with a concise \
summary of what you did and what the results were.\
"""

SUBAGENT_MAX_ITERATIONS = 15


def _skill_to_openai_tool(skill: SkillDef) -> dict[str, Any]:
    """Convert a SkillDef to an OpenAI function-calling tool definition."""
    params_schema = skill.input_model.model_json_schema()
    params_schema.pop("$defs", None)
    params_schema.pop("definitions", None)
    params_schema.setdefault("type", "object")

    return {
        "type": "function",
        "function": {
            "name": skill.name,
            "description": TOOL_DESCRIPTIONS.get(
                skill.name, f"Execute the {skill.name} skill."
            ),
            "parameters": params_schema,
        },
    }


def build_openai_tools(
    registry: SkillRegistry,
    *,
    include_task: bool = True,
    page_type_hint: str | None = None,
    auto_paginate: bool = False,
) -> list[dict[str, Any]]:
    """Build OpenAI tool definitions from all registered skills + built-ins.

    Args:
        include_task: When *True* (default, parent agent) the ``task`` tool is
            included so the LLM can spawn subagents.  Set to *False* for
            subagent loops to prevent recursive spawning.
        page_type_hint: Controls which tool categories are included.
            ``"detail"`` — minimal: browser + persist only.
            ``"index"`` or ``"auto"`` — minimal: browser + persist only.
            ``None`` — all tools (backward compatible, e.g. chat mode).
        auto_paginate: When *True*, replace browser_automation_skill with
            paginated_crawl_skill to force the LLM to use pagination.
    """
    # For crawl tasks (index/detail/auto), give ONLY essential tools
    # to prevent the LLM from wasting iterations on planning/skills/teams.
    _ESSENTIAL_SKILL_NAMES = {
        "browser_automation_skill",
        "persist_programs_skill",
        "analyze_page_skill",
        "paginated_crawl_skill",
    }

    # When auto_paginate is requested, exclude browser_automation_skill
    # so the LLM is forced to use paginated_crawl_skill instead.
    if auto_paginate:
        _ESSENTIAL_SKILL_NAMES = {
            "paginated_crawl_skill",
            "persist_programs_skill",
        }

    if page_type_hint in ("index", "detail", "auto"):
        tools: list[dict[str, Any]] = []
        for name in registry:
            if name in _ESSENTIAL_SKILL_NAMES:
                skill = registry._skills[name]  # noqa: SLF001
                tools.append(_skill_to_openai_tool(skill))
        return tools

    # Unrestricted mode (chat, etc.) — all tools
    tools = [
        _TODO_TOOL, _LOAD_SKILL_TOOL, _COMPACT_TOOL,
        *_TASK_GRAPH_TOOLS,
        _BG_RUN_TOOL, _BG_CHECK_TOOL,
    ]

    tools.extend(_TEAM_TOOLS)
    if include_task:
        tools.append(_TASK_TOOL)

    tools.extend(_PROTOCOL_TOOLS)
    tools.extend(_AUTONOMY_TOOLS)
    tools.extend(_WORKTREE_TOOLS)

    # All skill tools
    for name in registry:
        skill = registry._skills[name]  # noqa: SLF001
        tools.append(_skill_to_openai_tool(skill))

    return tools


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

_PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL_NAME",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    "custom": {
        "api_key_env": "CUSTOM_LLM_API_KEY",
        "base_url_env": "CUSTOM_LLM_BASE_URL",
        "model_env": "CUSTOM_LLM_MODEL_NAME",
        "default_base_url": "",
        "default_model": "gpt-4o-mini",
    },
    "volcengine": {
        "api_key_env": "VOLC_API_KEY",
        "base_url_env": "VOLC_BASE_URL",
        "model_env": "VOLC_MODEL_ID",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",
    },
}


def resolve_openai_client() -> tuple[Any, str]:
    """Pick the first available OpenAI-compatible provider and return (AsyncOpenAI, model).

    Follows the same ``LLM_PRIORITY_LIST`` env var as the existing RouterAgent.
    Gemini is skipped because it is not OpenAI-compatible.
    """
    from openai import AsyncOpenAI

    priority_str = os.environ.get("LLM_PRIORITY_LIST", "deepseek,gemini")
    priority_names = [n.strip().lower() for n in priority_str.split(",")]

    for name in priority_names:
        cfg = _PROVIDER_CONFIGS.get(name)
        if cfg is None:
            continue

        api_key = os.environ.get(cfg["api_key_env"], "")
        base_url = os.environ.get(cfg["base_url_env"], "") or cfg["default_base_url"]
        model = os.environ.get(cfg["model_env"], "") or cfg["default_model"]

        if not api_key or not base_url or not model:
            continue

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        logger.info(
            "[AgentLoop] Using provider '%s' (model=%s) for agent loop",
            name,
            model,
        )
        return client, model

    raise RuntimeError(
        "No OpenAI-compatible LLM provider configured for agent loop. "
        "Configure DEEPSEEK_API_KEY, CUSTOM_LLM_BASE_URL, or VOLC_API_KEY."
    )


# ---------------------------------------------------------------------------
# Streaming LLM helper
# ---------------------------------------------------------------------------


async def _streaming_llm_call(
    client: Any,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    *,
    event_sink: EventSink | None = None,
    iteration: int = 0,
) -> Any:
    """Call LLM with streaming, emitting thinking deltas in real time.

    Collects the full response and returns a ChatCompletion-like object
    compatible with the non-streaming path.
    """
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools or None,
        max_tokens=32768,
        stream=True,
        stream_options={"include_usage": True},
    )

    # Accumulators
    content_parts: list[str] = []
    tool_calls_acc: dict[int, dict] = {}  # index → {id, type, function: {name, arguments}}
    finish_reason = None
    role = "assistant"
    usage_data: dict[str, int] = {}

    async for chunk in stream:
        # Capture usage from the final chunk (OpenAI/volcengine include it there)
        if hasattr(chunk, "usage") and chunk.usage is not None:
            u = chunk.usage
            usage_data = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                "total_tokens": getattr(u, "total_tokens", 0) or 0,
            }

        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        if chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason

        # Stream text content
        if delta.content:
            content_parts.append(delta.content)
            _emit_loop_event(
                event_sink,
                "agent_thinking_delta",
                iteration=iteration,
                text=delta.content,
            )

        # Accumulate tool call deltas
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": tc_delta.id or "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                acc = tool_calls_acc[idx]
                if tc_delta.id:
                    acc["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        acc["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        acc["function"]["arguments"] += tc_delta.function.arguments

    # Emit token usage if available
    if usage_data:
        _emit_loop_event(
            event_sink,
            "token_usage",
            iteration=iteration,
            **usage_data,
        )

    # Build a minimal response object that matches the non-streaming shape
    full_content = "".join(content_parts) or None
    tool_calls_list = None
    if tool_calls_acc:
        tool_calls_list = []
        for idx in sorted(tool_calls_acc):
            tc = tool_calls_acc[idx]
            tool_calls_list.append(
                type("ToolCall", (), {
                    "id": tc["id"],
                    "type": tc["type"],
                    "function": type("Function", (), {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    })(),
                })()
            )

    message = type("Message", (), {
        "role": role,
        "content": full_content,
        "tool_calls": tool_calls_list,
    })()

    choice = type("Choice", (), {
        "message": message,
        "finish_reason": finish_reason,
    })()

    return type("Response", (), {"choices": [choice]})()


# ---------------------------------------------------------------------------
# Auto-persist helper (extracted to reduce nesting in the main loop)
# ---------------------------------------------------------------------------


async def _auto_persist_browser_programs(
    result_str: str,
    univ_slug: str,
    year: int,
    dry_run: bool,
    registry: "SkillRegistry",
    collected_programs: list[dict[str, Any]],
    event_sink: "EventSink | None",
    iteration: int,
) -> str:
    """Persist extracted programs from a browser result, return updated result_str."""
    try:
        browser_result = json.loads(result_str)
        extracted = browser_result.get("extracted_programs") or []
        if not (extracted and univ_slug and year):
            return result_str

        persist_payload = json.dumps({
            "univ_slug": univ_slug,
            "year": year,
            "programs": extracted,
            "dry_run": dry_run,
        }, ensure_ascii=False, default=str)
        persist_result_str = await _handle_skill_call(
            "persist_programs_skill", persist_payload, registry
        )
        persist_result = json.loads(persist_result_str)

        # Accumulate into collected_programs
        for prog in persist_result.get("parsed_programs", []):
            if isinstance(prog, dict):
                collected_programs.append(prog)
        n_upserted = (
            persist_result.get("imported_count", 0)
            + persist_result.get("updated_count", 0)
        )
        if n_upserted > 0 and not persist_result.get("parsed_programs"):
            for prog in extracted:
                if isinstance(prog, dict):
                    collected_programs.append(prog)

        # Emit events for extension counters
        n_persisted = len(collected_programs)
        for _ in range(n_persisted):
            _emit_loop_event(
                event_sink,
                "tool_call_finished",
                iteration=iteration,
                tool="persist_programs_skill",
                tool_call_id="auto_persist",
            )
        _emit_loop_event(
            event_sink,
            "token_usage",
            iteration=iteration,
            total_tokens=0,  # extraction tokens already counted
        )

        # Replace extracted_programs with summary for LLM
        browser_result.pop("extracted_programs", None)
        browser_result["auto_persisted"] = {
            "count": n_persisted,
            "univ_slug": univ_slug,
            "year": year,
            "dry_run": dry_run,
        }
        result_str = json.dumps(
            browser_result, ensure_ascii=False, default=str
        )

        logger.info(
            "[AgentLoop] Auto-persisted %d programs for %s/%d (dry_run=%s)",
            n_persisted, univ_slug, year, dry_run,
        )
    except Exception as exc:
        logger.warning("[AgentLoop] Auto-persist failed: %s", exc)
    return result_str


def _accumulate_persist_results(
    result_str: str,
    fn_args_raw: str,
    collected_programs: list[dict[str, Any]],
) -> None:
    """Extract persisted program data from a persist_programs_skill result."""
    try:
        skill_result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return

    # In non-dry-run mode, parsed_programs is empty but
    # imported/updated counts reflect actual DB writes.
    for prog in skill_result.get("parsed_programs", []):
        if isinstance(prog, dict):
            collected_programs.append(prog)

    # Also count successful upserts as collected programs
    upserted = (
        skill_result.get("imported_count", 0)
        + skill_result.get("updated_count", 0)
    )
    if upserted > 0 and not skill_result.get("parsed_programs"):
        # Reconstruct minimal entries from the tool call args
        try:
            call_args = json.loads(fn_args_raw)
            for prog in call_args.get("programs", []):
                if isinstance(prog, dict):
                    collected_programs.append(prog)
        except (json.JSONDecodeError, TypeError):
            # Fallback: add a placeholder so guard knows we persisted
            collected_programs.append({"_persisted": True})


# ---------------------------------------------------------------------------
# The Loop
# ---------------------------------------------------------------------------


async def agent_loop(
    *,
    user_message: str,
    registry: SkillRegistry,
    system_prompt: str = SYSTEM_PROMPT,
    max_iterations: int = MAX_ITERATIONS,
    _is_subagent: bool = False,
    _teammate_name: str | None = None,
    _message_bus: MessageBus | None = None,
    page_type_hint: str | None = None,
    event_sink: EventSink | None = None,
    univ_slug: str = "",
    year: int = 0,
    dry_run: bool = False,
    auto_paginate: bool = False,
) -> dict[str, Any]:
    """Run the LLM-driven agent loop.

    The loop continues until the model stops calling tools or
    ``max_iterations`` is reached.

    Args:
        _is_subagent: When *True* the ``task`` tool is excluded.
        _teammate_name: If set, this loop checks the named inbox each turn.
        _message_bus: MessageBus instance for team communication.

    Returns:
        ``{"response": str, "trace": list, "iterations": int, "todos": list}``
    """
    # Set task context for skill handlers (univ_slug, year)
    from src.agent_runtime.skills.impl.common import set_task_context
    set_task_context(univ_slug=univ_slug, year=year)

    client, model = resolve_openai_client()
    tools = build_openai_tools(
        registry,
        include_task=not _is_subagent,
        page_type_hint=page_type_hint,
        auto_paginate=auto_paginate,
    )
    todo = TodoManager()
    tasks = TaskManager()
    bg = BackgroundManager()
    from src.agent_runtime.team import MessageBus, TeammateManager

    bus = _message_bus or MessageBus()
    team = TeammateManager(bus=bus)
    protocols = ProtocolManager()
    worktrees = WorktreeManager(tasks=tasks)
    agent_name = _teammate_name or "lead"
    bus.ensure_inbox(agent_name)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    trace: list[dict[str, Any]] = []
    collected_programs: list[dict[str, Any]] = []
    iterations_since_todo = 0
    consecutive_timeouts = 0

    for iteration in range(1, max_iterations + 1):
        logger.info("[AgentLoop] Iteration %d — calling LLM", iteration)

        # -- s08: drain background notifications before LLM call --
        _drain_bg_notifications(messages, bg)

        # -- s09: drain inbox messages before LLM call --
        _drain_inbox(messages, agent_name, bus)

        # -- s06 Layer 1: micro_compact (silent, every turn) --
        micro_compact(messages)

        # -- s06 Layer 2: auto_compact (token threshold) --
        if should_auto_compact(messages):
            logger.info("[AgentLoop] Auto-compacting at iteration %d", iteration)
            messages[:] = await auto_compact(messages, client, model)

        # -- s11: identity re-injection after compression --
        if _teammate_name and len(messages) <= 3:
            _inject_identity(messages, _teammate_name, system_prompt)

        # -- s03 nag reminder: inject into last tool result when overdue --
        _maybe_inject_nag(messages, iterations_since_todo, todo)

        try:
            _emit_loop_event(
                event_sink,
                "llm_call_started",
                iteration=iteration,
            )
            response = await asyncio.wait_for(
                _streaming_llm_call(
                    client, model, messages, tools,
                    event_sink=event_sink, iteration=iteration,
                ),
                timeout=LLM_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            consecutive_timeouts += 1
            logger.warning(
                "[AgentLoop] LLM call timed out after %ds at iteration %d "
                "(%d consecutive)",
                LLM_CALL_TIMEOUT, iteration, consecutive_timeouts,
            )
            if consecutive_timeouts >= 2:
                logger.error(
                    "[AgentLoop] %d consecutive LLM timeouts — aborting loop",
                    consecutive_timeouts,
                )
                _emit_loop_done(
                    event_sink,
                    iteration=iteration,
                    reason="consecutive_timeouts",
                )
                return {
                    "response": "",
                    "trace": trace,
                    "iterations": iteration,
                    "todos": todo.items,
                    "collected_programs": collected_programs,
                    "error": "consecutive LLM timeouts",
                }
            messages.append({
                "role": "system",
                "content": (
                    "Your last LLM call timed out after 8 minutes. "
                    "The context may be too large. Try a simpler approach "
                    "or call compact to reduce context."
                ),
            })
            continue

        choice = response.choices[0]
        consecutive_timeouts = 0  # reset on successful call
        assistant_msg = choice.message
        _emit_loop_event(
            event_sink,
            "llm_call_finished",
            iteration=iteration,
        )

        # Detect output truncation (token limit hit)
        if getattr(choice, "finish_reason", None) == "length":
            logger.warning(
                "[AgentLoop] LLM output truncated (finish_reason=length) "
                "at iteration %d — tool call arguments may be incomplete",
                iteration,
            )

        # Append the raw assistant message to the conversation
        messages.append(_serialize_assistant_message(assistant_msg))

        # If no tool calls → the agent decided it is done
        if not assistant_msg.tool_calls:
            # Guard: if this is an index crawl and no programs were persisted,
            # nudge the agent to keep going instead of finishing empty-handed.
            if (
                page_type_hint in ("index", "auto", None)
                and not collected_programs
                and iteration < max_iterations - 1
            ):
                logger.warning(
                    "[AgentLoop] Agent tried to finish at iteration %d "
                    "with 0 programs on index page — nudging to continue",
                    iteration,
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "You have not extracted any programs yet. "
                        "You MUST fetch at least one detail page URL, "
                        "extract the program info, and call persist_programs_skill "
                        "before finishing. Continue working."
                    ),
                })
                continue

            final_text = assistant_msg.content or ""
            logger.info(
                "[AgentLoop] Agent finished after %d iteration(s)", iteration
            )
            trace.append(
                {
                    "stage": "agent_done",
                    "iteration": iteration,
                    "response_preview": final_text[:300],
                }
            )
            _emit_loop_done(
                event_sink,
                iteration=iteration,
                reason="completed",
                response_preview=final_text[:300],
            )
            return {
                "response": final_text,
                "trace": trace,
                "iterations": iteration,
                "todos": todo.items,
                "collected_programs": collected_programs,
            }

        # Track whether a todo update happened this iteration
        todo_updated_this_iteration = False
        idle_requested = False
        browser_fetch_done_this_iteration = False

        # Execute every tool call the model requested
        for tool_call in assistant_msg.tool_calls:
            fn_name = tool_call.function.name
            fn_args_raw = tool_call.function.arguments

            logger.info(
                "[AgentLoop] Tool call: %s(%s)",
                fn_name,
                fn_args_raw[:2000],
            )
            _emit_loop_event(
                event_sink,
                "tool_call_started",
                iteration=iteration,
                tool=fn_name,
                tool_call_id=tool_call.id,
            )

            # Rate-limit: only one browser_automation_skill call per iteration
            # to prevent context blowup from parallel detail page fetches.
            if fn_name == "browser_automation_skill" and browser_fetch_done_this_iteration:
                result_str = (
                    "SKIPPED: Only one browser fetch per iteration. "
                    "Process the current page first (extract info, "
                    "call persist_programs_skill), then fetch the next URL."
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })
                trace.append({
                    "stage": "tool_call",
                    "iteration": iteration,
                    "tool": fn_name,
                    "args_preview": fn_args_raw[:500],
                    "result_preview": result_str,
                })
                _emit_loop_event(
                    event_sink,
                    "tool_call_finished",
                    iteration=iteration,
                    tool=fn_name,
                    tool_call_id=tool_call.id,
                )
                continue

            # -- Built-in tool dispatch --
            if fn_name == "todo":
                result_str = _handle_todo_call(fn_args_raw, todo)
                todo_updated_this_iteration = True
            elif fn_name == "load_skill":
                result_str = _handle_load_skill_call(fn_args_raw)
            elif fn_name == "compact":
                # s06 Layer 3: manual compact — summarize and reset
                logger.info("[AgentLoop] Manual compact requested")
                messages[:] = await auto_compact(messages, client, model)
                result_str = "Conversation compressed. Continuing from summary."
            elif fn_name in ("task_create", "task_update", "task_list", "task_get"):
                result_str = _handle_task_graph_call(fn_name, fn_args_raw, tasks)
            elif fn_name == "bg_run":
                result_str = _handle_bg_run(fn_args_raw, bg, registry)
            elif fn_name == "bg_check":
                result_str = await _handle_bg_check(fn_args_raw, bg)
            elif fn_name == "team_spawn":
                result_str = _handle_team_spawn(fn_args_raw, team, registry, page_type_hint)
            elif fn_name == "team_send":
                result_str = _handle_team_send(fn_args_raw, agent_name, bus)
            elif fn_name == "team_inbox":
                result_str = _handle_team_inbox(agent_name, bus, team)
            elif fn_name == "idle":
                # s11: signal idle — loop will stop, teammate enters idle phase
                result_str = "Entering idle mode. Waiting for new work."
                idle_requested = True
            elif fn_name == "claim_task":
                result_str = _handle_claim_task(fn_args_raw, agent_name, tasks)
            elif fn_name == "protocol_request":
                result_str = _handle_protocol_request(
                    fn_args_raw, agent_name, protocols, bus
                )
            elif fn_name == "protocol_respond":
                result_str = _handle_protocol_respond(
                    fn_args_raw, agent_name, protocols, bus
                )
            elif fn_name == "protocol_status":
                result_str = _handle_protocol_status(fn_args_raw, protocols)
            elif fn_name in (
                "worktree_create", "worktree_run", "worktree_list",
                "worktree_keep", "worktree_remove",
            ):
                result_str = _handle_worktree_call(fn_name, fn_args_raw, worktrees)
            elif fn_name == "task":
                result_str = await _handle_task_call(
                    fn_args_raw, registry
                )
            else:
                result_str = await _handle_skill_call(
                    fn_name, fn_args_raw, registry
                )

            # Track browser fetch for rate-limiting
            if fn_name == "browser_automation_skill":
                browser_fetch_done_this_iteration = True

                # AUTO-PERSIST: If the skill extracted programs, persist them
                # directly in code — never ask the LLM to relay the data.
                result_str = await _auto_persist_browser_programs(
                    result_str, univ_slug, year, dry_run,
                    registry, collected_programs,
                    event_sink, iteration,
                )

            # Accumulate program counts from persist_programs_skill
            if fn_name == "persist_programs_skill":
                _accumulate_persist_results(
                    result_str, fn_args_raw, collected_programs
                )

            trace.append(
                {
                    "stage": "tool_call",
                    "iteration": iteration,
                    "tool": fn_name,
                    "args_preview": fn_args_raw[:500],
                    "result_preview": result_str[:500],
                }
            )
            _emit_loop_event(
                event_sink,
                "tool_call_finished",
                iteration=iteration,
                tool=fn_name,
                tool_call_id=tool_call.id,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                }
            )

        # s11: if idle was requested, stop the loop so teammate enters idle phase
        if idle_requested:
            logger.info("[AgentLoop] Idle requested — stopping loop")
            _emit_loop_done(
                event_sink,
                iteration=iteration,
                reason="idle_requested",
                response_preview="Entered idle mode.",
            )
            return {
                "response": "Entered idle mode.",
                "trace": trace,
                "iterations": iteration,
                "todos": todo.items,
                "collected_programs": collected_programs,
            }

        # Update nag counter
        if todo_updated_this_iteration:
            iterations_since_todo = 0
        else:
            iterations_since_todo += 1

    logger.warning("[AgentLoop] Hit max iterations (%d)", max_iterations)
    _emit_loop_done(
        event_sink,
        iteration=max_iterations,
        reason="max_iterations",
        response_preview="Agent reached maximum iteration limit.",
    )
    return {
        "response": "Agent reached maximum iteration limit.",
        "trace": trace,
        "iterations": max_iterations,
        "todos": todo.items,
        "collected_programs": collected_programs,
    }


# ---------------------------------------------------------------------------
# Tool dispatch helpers
# ---------------------------------------------------------------------------


def _handle_todo_call(fn_args_raw: str, todo: TodoManager) -> str:
    """Execute the built-in todo tool."""
    try:
        fn_args = json.loads(fn_args_raw)
        rendered = todo.update(fn_args.get("items", []))
        return rendered
    except Exception as exc:
        logger.warning("[AgentLoop] todo tool failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _task_create(fn_args: dict[str, Any], tasks: TaskManager) -> str:
    result = tasks.create(
        subject=fn_args.get("subject", ""),
        description=fn_args.get("description", ""),
        blocked_by=fn_args.get("blocked_by"),
    )
    return json.dumps(result, ensure_ascii=False)


def _task_update(fn_args: dict[str, Any], tasks: TaskManager) -> str:
    result = tasks.update(
        task_id=fn_args["task_id"],
        status=fn_args.get("status"),
        add_blocked_by=fn_args.get("add_blocked_by"),
        add_blocks=fn_args.get("add_blocks"),
    )
    if result is None:
        return json.dumps({"error": f"Task {fn_args['task_id']} not found"})
    return json.dumps(result, ensure_ascii=False)


def _task_list(_fn_args: dict[str, Any], tasks: TaskManager) -> str:
    all_tasks = tasks.list_all()
    return tasks.render() if all_tasks else "(no tasks)"


def _task_get(fn_args: dict[str, Any], tasks: TaskManager) -> str:
    result = tasks.get(fn_args["task_id"])
    if result is None:
        return json.dumps({"error": f"Task {fn_args['task_id']} not found"})
    return json.dumps(result, ensure_ascii=False)


_TASK_DISPATCH: dict[str, Callable[..., str]] = {
    "task_create": _task_create,
    "task_update": _task_update,
    "task_list": _task_list,
    "task_get": _task_get,
}


def _handle_task_graph_call(
    fn_name: str, fn_args_raw: str, tasks: TaskManager
) -> str:
    """Dispatch task_create/update/list/get calls (s07)."""
    try:
        fn_args = json.loads(fn_args_raw)
        handler = _TASK_DISPATCH.get(fn_name)
        if handler is None:
            return json.dumps({"error": f"Unknown task tool: {fn_name}"})
        return handler(fn_args, tasks)
    except Exception as exc:
        logger.warning("[AgentLoop] %s failed: %s", fn_name, exc)
        return json.dumps({"error": str(exc)})


def _handle_bg_run(
    fn_args_raw: str, bg: BackgroundManager, registry: SkillRegistry
) -> str:
    """Schedule a skill to run in the background (s08)."""
    try:
        fn_args = json.loads(fn_args_raw)
        skill_name = fn_args.get("skill_name", "")
        skill_args = fn_args.get("args", {})

        if not skill_name:
            return json.dumps({"error": "bg_run requires 'skill_name'"})

        # Create an async coroutine for the skill execution
        async def _run_skill() -> Any:
            return await asyncio.to_thread(registry.execute, skill_name, skill_args)

        task_id = bg.run(
            _run_skill(),
            skill_name=skill_name,
            args_preview=json.dumps(skill_args, ensure_ascii=False)[:200],
        )
        return json.dumps({"task_id": task_id, "status": "started", "skill": skill_name})
    except Exception as exc:
        logger.warning("[AgentLoop] bg_run failed: %s", exc)
        return json.dumps({"error": str(exc)})


async def _handle_bg_check(fn_args_raw: str, bg: BackgroundManager) -> str:
    """Wait for a background task to complete and return its result (s08).

    Blocks up to ``timeout`` seconds (default 300) so the agent loop
    does not burn iterations polling.
    """
    try:
        fn_args = json.loads(fn_args_raw)
        task_id = fn_args.get("task_id")
        timeout = float(fn_args.get("timeout", 300))
        if task_id:
            result = await bg.wait(task_id, timeout=timeout)
            return json.dumps(result, ensure_ascii=False)
        return json.dumps(bg.list_all(), ensure_ascii=False)
    except Exception as exc:
        logger.warning("[AgentLoop] bg_check failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _drain_bg_notifications(
    messages: list[dict[str, Any]], bg: BackgroundManager
) -> None:
    """Inject completed background results into the conversation (s08)."""
    notifs = bg.drain_notifications()
    if not notifs:
        return

    notif_lines = [
        f"[{n['task_id']}] {n['skill']}: {n['result']}" for n in notifs
    ]
    notif_text = "\n".join(notif_lines)

    messages.append({
        "role": "user",
        "content": f"<background-results>\n{notif_text}\n</background-results>",
    })
    messages.append({
        "role": "assistant",
        "content": "Noted background results.",
    })
    logger.info("[AgentLoop] Injected %d background notification(s)", len(notifs))


def _handle_claim_task(
    fn_args_raw: str, agent_name: str, tasks: TaskManager
) -> str:
    """Claim an unclaimed task from the board (s11)."""
    try:
        fn_args = json.loads(fn_args_raw)
        task_id = fn_args.get("task_id")
        if task_id is None:
            return json.dumps({"error": "claim_task requires 'task_id'"})
        result = tasks.claim(int(task_id), agent_name)
        if result is None:
            return json.dumps({"error": f"Task {task_id} not found or already claimed"})
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        logger.warning("[AgentLoop] claim_task failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _inject_identity(
    messages: list[dict[str, Any]], name: str, system_prompt: str
) -> None:
    """Re-inject identity after context compression (s11).

    When messages are very short (post-compression), insert an identity
    block so the agent remembers who it is.
    """
    identity = (
        f"<identity>You are '{name}'. Your system prompt: "
        f"{system_prompt[:200]}... Continue your work.</identity>"
    )
    messages.insert(0, {"role": "user", "content": identity})
    messages.insert(1, {"role": "assistant", "content": f"I am {name}. Continuing."})
    logger.info("[AgentLoop] Injected identity block for '%s'", name)


def _handle_protocol_request(
    fn_args_raw: str,
    sender: str,
    protocols: ProtocolManager,
    bus: MessageBus,
) -> str:
    """Create a protocol request and notify the target (s10)."""
    try:
        fn_args = json.loads(fn_args_raw)
        record = protocols.create_request(
            protocol=fn_args.get("protocol", ""),
            sender=sender,
            target=fn_args.get("target", ""),
            description=fn_args.get("description", ""),
        )
        # Notify target via message bus
        bus.send(
            sender=sender,
            to=record["target"],
            content=(
                f"[PROTOCOL:{record['protocol']}] {record['description']}\n"
                f"request_id: {record['request_id']}\n"
                f"Use protocol_respond to approve or reject."
            ),
            msg_type="protocol_request",
        )
        return json.dumps(record, ensure_ascii=False)
    except Exception as exc:
        logger.warning("[AgentLoop] protocol_request failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _handle_protocol_respond(
    fn_args_raw: str,
    responder: str,
    protocols: ProtocolManager,
    bus: MessageBus,
) -> str:
    """Approve or reject a protocol request (s10)."""
    try:
        fn_args = json.loads(fn_args_raw)
        request_id = fn_args.get("request_id", "")
        approve = fn_args.get("approve", False)
        reason = fn_args.get("reason", "")

        record = protocols.respond(request_id, approve, reason)
        if record is None:
            return json.dumps({"error": f"Request {request_id} not found"})

        # Notify the original requester
        status_word = "approved" if approve else "rejected"
        bus.send(
            sender=responder,
            to=record["from"],
            content=(
                f"[PROTOCOL:{record['protocol']}] Request {request_id} "
                f"{status_word}. {reason}"
            ),
            msg_type="protocol_response",
        )
        return json.dumps(record, ensure_ascii=False)
    except Exception as exc:
        logger.warning("[AgentLoop] protocol_respond failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _handle_protocol_status(
    fn_args_raw: str, protocols: ProtocolManager
) -> str:
    """List protocol requests (s10)."""
    try:
        fn_args = json.loads(fn_args_raw)
        status_filter = fn_args.get("status_filter")
        requests = protocols.list_by_status(status_filter)
        if not requests:
            return "(no protocol requests)"
        return protocols.render()
    except Exception as exc:
        logger.warning("[AgentLoop] protocol_status failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _handle_team_spawn(
    fn_args_raw: str,
    team: TeammateManager,
    registry: SkillRegistry,
    page_type_hint: str | None = None,
) -> str:
    """Spawn a new teammate (s09)."""
    try:
        fn_args = json.loads(fn_args_raw)
        return team.spawn(
            name=fn_args.get("name", ""),
            role=fn_args.get("role", ""),
            prompt=fn_args.get("prompt", ""),
            registry=registry,
            page_type_hint="detail" if page_type_hint in ("index", "detail") else page_type_hint,
        )
    except Exception as exc:
        logger.warning("[AgentLoop] team_spawn failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _handle_team_send(
    fn_args_raw: str, sender: str, bus: MessageBus
) -> str:
    """Send a message to a teammate (s09)."""
    try:
        fn_args = json.loads(fn_args_raw)
        return bus.send(
            sender=sender,
            to=fn_args.get("to", ""),
            content=fn_args.get("content", ""),
        )
    except Exception as exc:
        logger.warning("[AgentLoop] team_send failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _handle_team_inbox(
    agent_name: str, bus: MessageBus, team: TeammateManager
) -> str:
    """Read inbox + show team roster (s09)."""
    try:
        msgs = bus.read_inbox(agent_name)
        roster = team.render()
        parts = [f"Team roster:\n{roster}"]
        if msgs:
            parts.append(f"Inbox ({len(msgs)} message(s)):")
            parts.append(json.dumps(msgs, indent=2, ensure_ascii=False))
        else:
            parts.append("Inbox: (empty)")
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("[AgentLoop] team_inbox failed: %s", exc)
        return json.dumps({"error": str(exc)})


def _wt_create(fn_args: dict[str, Any], wt: WorktreeManager) -> str:
    result = wt.create(name=fn_args.get("name", ""), task_id=fn_args.get("task_id"))
    return json.dumps(result, ensure_ascii=False)


def _wt_run(fn_args: dict[str, Any], wt: WorktreeManager) -> str:
    result = wt.run_in(
        name=fn_args.get("name", ""),
        command=fn_args.get("command", ""),
        timeout=fn_args.get("timeout", 300),
    )
    return json.dumps(result, ensure_ascii=False)


def _wt_list(_fn_args: dict[str, Any], wt: WorktreeManager) -> str:
    return wt.render() if wt.list_all() else "(no worktrees)"


def _wt_keep(fn_args: dict[str, Any], wt: WorktreeManager) -> str:
    return json.dumps(wt.keep(name=fn_args.get("name", "")), ensure_ascii=False)


def _wt_remove(fn_args: dict[str, Any], wt: WorktreeManager) -> str:
    result = wt.remove(
        name=fn_args.get("name", ""),
        complete_task=fn_args.get("complete_task", False),
        force=fn_args.get("force", False),
    )
    return json.dumps(result, ensure_ascii=False)


_WT_DISPATCH: dict[str, Callable[..., str]] = {
    "worktree_create": _wt_create,
    "worktree_run": _wt_run,
    "worktree_list": _wt_list,
    "worktree_keep": _wt_keep,
    "worktree_remove": _wt_remove,
}


def _handle_worktree_call(
    fn_name: str, fn_args_raw: str, worktrees: WorktreeManager
) -> str:
    """Dispatch worktree_create/run/list/keep/remove calls (s12)."""
    try:
        fn_args = json.loads(fn_args_raw)
        handler = _WT_DISPATCH.get(fn_name)
        if handler is None:
            return json.dumps({"error": f"Unknown worktree tool: {fn_name}"})
        return handler(fn_args, worktrees)
    except Exception as exc:
        logger.warning("[AgentLoop] %s failed: %s", fn_name, exc)
        return json.dumps({"error": str(exc)})


def _drain_inbox(
    messages: list[dict[str, Any]], agent_name: str, bus: MessageBus
) -> None:
    """Inject pending inbox messages into the conversation (s09)."""
    msgs = bus.read_inbox(agent_name)
    if not msgs:
        return

    inbox_text = json.dumps(msgs, indent=2, ensure_ascii=False)
    messages.append({
        "role": "user",
        "content": f"<inbox>\n{inbox_text}\n</inbox>",
    })
    messages.append({
        "role": "assistant",
        "content": "Noted inbox messages.",
    })
    logger.info(
        "[AgentLoop] Injected %d inbox message(s) for '%s'",
        len(msgs),
        agent_name,
    )


def _handle_load_skill_call(fn_args_raw: str) -> str:
    """Load a knowledge skill by name (s05)."""
    try:
        fn_args = json.loads(fn_args_raw)
        name = fn_args.get("name", "")
        return _SKILL_LOADER.get_content(name)
    except Exception as exc:
        logger.warning("[AgentLoop] load_skill failed: %s", exc)
        return json.dumps({"error": str(exc)})


async def _handle_task_call(
    fn_args_raw: str, registry: SkillRegistry
) -> str:
    """Spawn a subagent loop (s04) and return its summary text."""
    try:
        fn_args = json.loads(fn_args_raw)
        prompt = fn_args.get("prompt", "")
        if not prompt:
            return json.dumps({"error": "task tool requires a 'prompt'"})

        logger.info("[AgentLoop] Spawning subagent for: %s", prompt[:120])

        result = await agent_loop(
            user_message=prompt,
            registry=registry,
            system_prompt=SUBAGENT_SYSTEM_PROMPT,
            max_iterations=SUBAGENT_MAX_ITERATIONS,
            _is_subagent=True,
        )

        summary = result.get("response", "(subagent returned no summary)")
        logger.info(
            "[AgentLoop] Subagent finished in %d iteration(s)",
            result.get("iterations", 0),
        )
        return summary
    except Exception as exc:
        logger.warning("[AgentLoop] Subagent failed: %s", exc)
        return json.dumps({"error": f"subagent failed: {exc}"})


async def _handle_skill_call(
    fn_name: str, fn_args_raw: str, registry: SkillRegistry
) -> str:
    """Execute a SkillRegistry tool in a thread."""
    try:
        fn_args = json.loads(fn_args_raw)
    except json.JSONDecodeError as exc:
        logger.warning("[AgentLoop] Tool %s JSON parse failed: %s", fn_name, exc)
        if fn_name == "persist_programs_skill":
            return json.dumps({
                "error": (
                    f"Invalid JSON in tool arguments: {exc}. "
                    "Your tool call was too large and got truncated. "
                    "You MUST split into ONE program per call. "
                    "Use ONLY required fields: name_en, source_url. "
                    "Omit description, tuition_fees, and other large fields — "
                    "they can be added later. Example: "
                    '{"univ_slug":"x","year":2026,"programs":[{"name_en":"Program Name","source_url":"https://..."}]}'
                )
            })
        return json.dumps({
            "error": (
                f"Invalid JSON in tool arguments: {exc}. "
                "Your output was likely truncated. "
                "Simplify the payload or split into smaller calls."
            )
        })
    try:
        result = await asyncio.to_thread(registry.execute, fn_name, fn_args)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("[AgentLoop] Tool %s failed: %s", fn_name, exc)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# s03: Nag reminder injection
# ---------------------------------------------------------------------------


def _maybe_inject_nag(
    messages: list[dict[str, Any]],
    iterations_since_todo: int,
    todo: TodoManager,
) -> None:
    """Inject a <reminder> into the last tool-result message when overdue.

    Modifies ``messages`` in place. Only fires when the model has gone
    ``NAG_INTERVAL`` or more iterations without calling the ``todo`` tool.
    """
    if iterations_since_todo < NAG_INTERVAL:
        return
    if not messages:
        return

    # Find the last tool-result message to attach the reminder to
    last = messages[-1]
    if last.get("role") != "tool":
        return

    todo_state = todo.render()
    reminder = (
        "<reminder>You haven't updated your todo list in "
        f"{iterations_since_todo} iterations. Review your plan and call "
        f"the `todo` tool to update progress.\n\n"
        f"Current todos:\n{todo_state}</reminder>"
    )

    # Prepend reminder to the tool result content
    existing = last.get("content", "")
    last["content"] = f"{reminder}\n\n{existing}"
    logger.info(
        "[AgentLoop] Injected nag reminder (iterations_since_todo=%d)",
        iterations_since_todo,
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _serialize_assistant_message(msg: Any) -> dict[str, Any]:
    """Convert an OpenAI ChatCompletionMessage to a plain dict for messages list."""
    serialized: dict[str, Any] = {"role": "assistant"}

    if msg.content:
        serialized["content"] = msg.content

    if msg.tool_calls:
        serialized["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

    return serialized

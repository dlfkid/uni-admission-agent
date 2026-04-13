"""Typed skill registry and execution helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.agent_bridge.client_automation_bridge import ClientAutomationBridge
from src.agent_bridge.serve_tool_bridge import ServeToolBridge
from src.agent_runtime.skills.contracts import (
    AnalyzePageSkillInput,
    AnalyzePageSkillOutput,
    BrowserAutomationSkillInput,
    BrowserAutomationSkillOutput,
    CrawlDetailBatchSkillInput,
    CrawlDetailBatchSkillOutput,
    PaginatedCrawlSkillInput,
    PaginatedCrawlSkillOutput,
    PersistProgramsSkillInput,
    PersistProgramsSkillOutput,
    QueryDbSkillInput,
    QueryDbSkillOutput,
    ReviewPatchSkillInput,
    ReviewPatchSkillOutput,
    SelectDetailCandidatesSkillInput,
    SelectDetailCandidatesSkillOutput,
)
from src.agent_runtime.skills.impl import (
    analyze_page_skill_handler,
    browser_automation_skill_handler,
    legacy_crawl_batch_skill_handler,
    paginated_crawl_skill_handler,
    persist_programs_skill_handler,
    query_db_skill_handler,
    review_patch_skill_handler,
    select_detail_candidates_skill_handler,
)


class SkillDef(BaseModel):
    """Skill metadata and typed execution contract."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel], dict[str, Any]]


class SkillRegistry:
    """In-memory registry for skill definitions."""

    def __init__(self, skills: list[SkillDef]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return name in self._skills

    def __iter__(self) -> Iterator[str]:
        return iter(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def keys(self) -> list[str]:
        """Return all registered skill names."""
        return list(self._skills.keys())

    def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate payload, execute handler, and validate output."""
        if name not in self._skills:
            raise KeyError(f"Unknown skill: {name}")

        skill = self._skills[name]
        model_in = skill.input_model.model_validate(payload or {})
        raw_output = skill.handler(model_in)
        model_out = skill.output_model.model_validate(raw_output or {})
        return model_out.model_dump(mode="json")


def build_skill_registry(
    serve_bridge: ServeToolBridge | None = None,
    client_bridge: ClientAutomationBridge | None = None,
) -> SkillRegistry:
    """Build the default skill registry used by agent runtimes.

    Registry contracts are kept stable so runtime orchestration can migrate
    from direct service calls to fully skill-driven dispatch incrementally.

    .. note::
        Currently ``PydanticAIRuntime`` calls services directly.  Once the
        ``pydantic-ai`` Agent integration lands (Phase C), skills registered
        here will be exposed as agent tools via the ``pydantic-ai`` tool API.
    """
    serve_bridge = serve_bridge or ServeToolBridge()
    client_bridge = client_bridge or ClientAutomationBridge()

    skills = [
        SkillDef(
            name="analyze_page_skill",
            input_model=AnalyzePageSkillInput,
            output_model=AnalyzePageSkillOutput,
            handler=lambda payload: analyze_page_skill_handler(payload, serve_bridge),
        ),
        SkillDef(
            name="select_detail_candidates_skill",
            input_model=SelectDetailCandidatesSkillInput,
            output_model=SelectDetailCandidatesSkillOutput,
            handler=select_detail_candidates_skill_handler,
        ),
        SkillDef(
            name="legacy_crawl_batch_skill",
            input_model=CrawlDetailBatchSkillInput,
            output_model=CrawlDetailBatchSkillOutput,
            handler=legacy_crawl_batch_skill_handler,
        ),
        SkillDef(
            name="persist_programs_skill",
            input_model=PersistProgramsSkillInput,
            output_model=PersistProgramsSkillOutput,
            handler=persist_programs_skill_handler,
        ),
        SkillDef(
            name="review_patch_skill",
            input_model=ReviewPatchSkillInput,
            output_model=ReviewPatchSkillOutput,
            handler=review_patch_skill_handler,
        ),
        SkillDef(
            name="query_db_skill",
            input_model=QueryDbSkillInput,
            output_model=QueryDbSkillOutput,
            handler=query_db_skill_handler,
        ),
        SkillDef(
            name="browser_automation_skill",
            input_model=BrowserAutomationSkillInput,
            output_model=BrowserAutomationSkillOutput,
            handler=lambda payload: browser_automation_skill_handler(payload, client_bridge),
        ),
        SkillDef(
            name="paginated_crawl_skill",
            input_model=PaginatedCrawlSkillInput,
            output_model=PaginatedCrawlSkillOutput,
            handler=lambda payload: paginated_crawl_skill_handler(payload, client_bridge),
        ),
    ]
    return SkillRegistry(skills)

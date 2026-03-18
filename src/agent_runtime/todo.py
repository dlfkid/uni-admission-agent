"""TodoManager — structured task tracking for the agent loop (s03 pattern).

Gives the LLM a scratchpad to plan multi-step work and track progress.
The harness enforces exactly-one-in_progress and renders the list back
into every LLM turn so the model never loses sight of its plan.
"""

from __future__ import annotations

from typing import Any


class TodoManager:
    """In-memory todo list with state enforcement.

    Rules:
    - At most **one** item may be ``in_progress`` at a time.
    - Valid statuses: ``pending``, ``in_progress``, ``completed``.
    """

    VALID_STATUSES = {"pending", "in_progress", "completed"}

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def update(self, items: list[dict[str, Any]]) -> str:
        """Replace the full list. Returns the rendered todo text."""
        validated: list[dict[str, str]] = []
        in_progress_count = 0

        for item in items:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            status = str(item.get("status") or "pending").strip().lower()
            if status not in self.VALID_STATUSES:
                status = "pending"
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})

        if in_progress_count > 1:
            raise ValueError(
                "Only one task can be in_progress at a time "
                f"(got {in_progress_count})."
            )

        self.items = validated
        return self.render()

    def render(self) -> str:
        """Render the list as a compact text block."""
        if not self.items:
            return "(empty todo list)"

        status_icon = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
        }
        lines: list[str] = []
        for i, item in enumerate(self.items, 1):
            icon = status_icon.get(item["status"], "[ ]")
            lines.append(f"{icon} {i}. {item['content']}")
        return "\n".join(lines)

    def inject_into_system(self) -> str:
        """Return a block suitable for prepending to the system prompt."""
        rendered = self.render()
        if rendered == "(empty todo list)":
            return ""
        return f"\n<current-todos>\n{rendered}\n</current-todos>\n"

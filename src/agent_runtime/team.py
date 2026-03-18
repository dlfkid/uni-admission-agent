"""Agent Teams — persistent teammates with JSONL mailbox communication (s09 pattern).

TeammateManager spawns persistent agent loops as asyncio tasks.
MessageBus provides append-only JSONL inboxes for inter-agent messaging.
Each teammate checks its inbox before every LLM call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TEAM_DIR = Path(".team")
INBOX_DIR = TEAM_DIR / "inbox"
TEAMMATE_MAX_ITERATIONS = 30
IDLE_POLL_INTERVAL = 5   # seconds between idle polls
IDLE_TIMEOUT = 60        # seconds before auto-shutdown


# ---------------------------------------------------------------------------
# MessageBus — JSONL file-based inboxes
# ---------------------------------------------------------------------------


class MessageBus:
    """Append-only JSONL mailboxes for inter-agent communication."""

    def __init__(self, inbox_dir: Path | None = None) -> None:
        self.dir = inbox_dir or INBOX_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
    ) -> str:
        """Append a message to a teammate's inbox."""
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        path = self.dir / f"{to}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        logger.info("[MessageBus] %s -> %s: %s", sender, to, content[:80])
        return f"Message sent to {to}"

    def broadcast(
        self, sender: str, content: str, exclude: set[str] | None = None
    ) -> str:
        """Send to all inboxes except sender and excluded names."""
        exclude = (exclude or set()) | {sender}
        sent_to: list[str] = []
        for path in self.dir.glob("*.jsonl"):
            name = path.stem
            if name in exclude:
                continue
            self.send(sender, name, content, msg_type="broadcast")
            sent_to.append(name)
        return f"Broadcast to: {', '.join(sent_to) or '(nobody)'}"

    def read_inbox(self, name: str) -> list[dict[str, Any]]:
        """Read and drain all messages from a teammate's inbox."""
        path = self.dir / f"{name}.jsonl"
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        msgs = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # Drain
        path.write_text("", encoding="utf-8")
        return msgs

    def ensure_inbox(self, name: str) -> None:
        """Create an empty inbox file if it doesn't exist."""
        path = self.dir / f"{name}.jsonl"
        if not path.exists():
            path.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# TeammateManager — spawn and manage persistent agent loops
# ---------------------------------------------------------------------------


class TeammateManager:
    """Manage a team of persistent agent loops."""

    def __init__(
        self,
        team_dir: Path | None = None,
        bus: MessageBus | None = None,
    ) -> None:
        self.dir = team_dir or TEAM_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.bus = bus or MessageBus()
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self._async_tasks: dict[str, asyncio.Task[None]] = {}

    def spawn(
        self,
        name: str,
        role: str,
        prompt: str,
        registry: Any,
    ) -> str:
        """Spawn a new teammate with its own agent loop."""
        # Check for duplicates
        for m in self.config["members"]:
            if m["name"] == name:
                return f"Teammate '{name}' already exists (status: {m['status']})"

        member = {
            "name": name,
            "role": role,
            "status": "working",
            "spawned_at": time.time(),
        }
        self.config["members"].append(member)
        self._save_config()

        # Create inbox
        self.bus.ensure_inbox(name)

        # Launch async task
        async_task = asyncio.ensure_future(
            self._teammate_loop(name, role, prompt, registry)
        )
        self._async_tasks[name] = async_task
        logger.info("[Team] Spawned teammate '%s' (role: %s)", name, role)
        return f"Spawned teammate '{name}' (role: {role})"

    def list_members(self) -> list[dict[str, Any]]:
        """Return current team roster."""
        return self.config["members"]

    def render(self) -> str:
        """Render team status as text."""
        members = self.config["members"]
        if not members:
            return "(no team members)"
        lines = []
        for m in members:
            lines.append(f"  - {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Teammate loop
    # ------------------------------------------------------------------

    async def _teammate_loop(
        self,
        name: str,
        role: str,
        prompt: str,
        registry: Any,
    ) -> None:
        """Run a persistent WORK ↔ IDLE agent loop for a teammate (s11).

        WORK phase: run agent_loop with the current prompt.
        IDLE phase: poll inbox + unclaimed tasks every few seconds.
          - inbox message → resume WORK
          - unclaimed task → claim + resume WORK
          - timeout → shutdown
        """
        from src.agent_runtime.loop import agent_loop
        from src.agent_runtime.task_manager import TaskManager

        system_prompt = (
            f"You are '{name}', a team member with role: {role}.\n"
            f"You can communicate with teammates using the team_send tool.\n"
            f"When your current task is complete, call the `idle` tool to "
            f"enter idle mode and wait for more work. Or send a summary "
            f"to 'lead' and stop.\n"
        )

        current_prompt = prompt
        task_mgr = TaskManager()

        while True:
            # -- WORK PHASE --
            self._update_member_status(name, "working")
            try:
                result = await agent_loop(
                    user_message=current_prompt,
                    registry=registry,
                    system_prompt=system_prompt,
                    max_iterations=TEAMMATE_MAX_ITERATIONS,
                    _is_subagent=True,
                    _teammate_name=name,
                    _message_bus=self.bus,
                )
                summary = result.get("response", "(no summary)")
                self.bus.send(name, "lead", f"[WORK DONE] {summary}")
                logger.info("[Team] Teammate '%s' finished work phase", name)
            except Exception as exc:
                logger.warning("[Team] Teammate '%s' work failed: %s", name, exc)
                self.bus.send(name, "lead", f"[ERROR] {exc}")

            # -- IDLE PHASE --
            self._update_member_status(name, "idle")
            logger.info("[Team] Teammate '%s' entering idle phase", name)

            resume_prompt = await self._idle_poll(name, task_mgr)
            if resume_prompt is None:
                # Timeout → shutdown
                self._update_member_status(name, "shutdown")
                self.bus.send(name, "lead", f"[SHUTDOWN] Idle timeout after {IDLE_TIMEOUT}s")
                logger.info("[Team] Teammate '%s' shutting down (idle timeout)", name)
                return

            current_prompt = resume_prompt

    async def _idle_poll(
        self, name: str, task_mgr: Any
    ) -> str | None:
        """Poll for new work during idle phase. Returns new prompt or None on timeout."""
        polls = IDLE_TIMEOUT // IDLE_POLL_INTERVAL

        for _ in range(polls):
            await asyncio.sleep(IDLE_POLL_INTERVAL)

            # Check inbox
            msgs = self.bus.read_inbox(name)
            if msgs:
                import json as _json
                inbox_text = _json.dumps(msgs, ensure_ascii=False, indent=2)
                logger.info("[Team] Teammate '%s' woke by inbox message", name)
                return f"You received messages while idle:\n{inbox_text}\nResume work."

            # Scan unclaimed tasks
            unclaimed = task_mgr.unclaimed_tasks()
            if unclaimed:
                task = unclaimed[0]
                claimed = task_mgr.claim(task["id"], name)
                if claimed:
                    logger.info(
                        "[Team] Teammate '%s' auto-claimed task #%d: %s",
                        name, task["id"], task["subject"],
                    )
                    return (
                        f"You auto-claimed task #{task['id']}: {task['subject']}\n"
                        f"Description: {task.get('description', '')}\n"
                        f"Work on this task now."
                    )

        return None  # timeout

    def _update_member_status(self, name: str, status: str) -> None:
        for m in self.config["members"]:
            if m["name"] == name:
                m["status"] = status
                break
        self._save_config()

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"members": []}

    def _save_config(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

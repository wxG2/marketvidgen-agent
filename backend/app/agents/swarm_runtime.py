from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SwarmRunController:
    run_id: str
    human_messages: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    latest_snapshot: dict[str, Any] = field(default_factory=dict)

    async def send_human_message(self, message: str):
        await self.human_messages.put(message)

    async def drain_human_messages(self) -> list[str]:
        messages: list[str] = []
        while not self.human_messages.empty():
            messages.append(await self.human_messages.get())
        return messages

    def update_snapshot(self, snapshot: dict[str, Any]):
        self.latest_snapshot = snapshot


_controllers: dict[str, SwarmRunController] = {}


def register_swarm_controller(run_id: str) -> SwarmRunController:
    controller = SwarmRunController(run_id=run_id)
    _controllers[run_id] = controller
    return controller


def get_swarm_controller(run_id: str) -> SwarmRunController | None:
    return _controllers.get(run_id)


def unregister_swarm_controller(run_id: str):
    _controllers.pop(run_id, None)

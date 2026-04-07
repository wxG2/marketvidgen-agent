from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]


class ToolDefinition:
    """Describes a single callable tool available to agents.

    Attributes:
        name: Unique tool identifier (snake_case).
        description: Human-readable description shown to agents for selection.
        fn: Async callable that executes the tool.
        required_permission: Optional permission string an agent must hold.
    """

    def __init__(
        self,
        name: str,
        description: str,
        fn: ToolFn,
        required_permission: Optional[str] = None,
    ) -> None:
        self.name = name
        self.description = description
        self.fn = fn
        self.required_permission = required_permission

    async def call(self, **kwargs: Any) -> Any:
        return await self.fn(**kwargs)


class ToolRegistry:
    """Central registry of tools that agents can discover and invoke at runtime.

    Enables agents to move beyond hardcoded capability lists toward dynamic
    tool selection based on context.  Each tool can carry an optional
    *permission* requirement; agent permissions are granted explicitly via
    :meth:`grant_permission`.

    Usage::

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="search_materials",
            description="Search the material library by keyword",
            fn=my_search_fn,
        ))
        registry.grant_permission("orchestrator", "search_materials")

        result = await registry.invoke("search_materials", agent_name="orchestrator", query="beach")
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._agent_permissions: dict[str, set[str]] = {}

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool. Overwrites any existing tool with the same name."""
        self._tools[tool.name] = tool
        logger.debug(f"[tool_registry] Registered tool '{tool.name}'")

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ── Permissions ──────────────────────────────────────────────────────────

    def grant_permission(self, agent_name: str, permission: str) -> None:
        """Grant *permission* to *agent_name*."""
        self._agent_permissions.setdefault(agent_name, set()).add(permission)

    def revoke_permission(self, agent_name: str, permission: str) -> None:
        self._agent_permissions.get(agent_name, set()).discard(permission)

    def has_permission(self, agent_name: str, permission: str) -> bool:
        return permission in self._agent_permissions.get(agent_name, set())

    # ── Discovery ────────────────────────────────────────────────────────────

    def list_tools(self, agent_name: Optional[str] = None) -> list[dict[str, str]]:
        """Return tool descriptors visible to *agent_name* (or all tools if None)."""
        result = []
        for tool in self._tools.values():
            if tool.required_permission is None:
                result.append({"name": tool.name, "description": tool.description})
            elif agent_name is not None and self.has_permission(
                agent_name, tool.required_permission
            ):
                result.append({"name": tool.name, "description": tool.description})
        return result

    # ── Invocation ───────────────────────────────────────────────────────────

    async def invoke(
        self, tool_name: str, agent_name: str, **kwargs: Any
    ) -> Any:
        """Invoke *tool_name* on behalf of *agent_name*.

        Raises:
            KeyError: Tool not found in registry.
            PermissionError: Agent lacks the required permission.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Tool '{tool_name}' not found in registry")

        if tool.required_permission is not None:
            if not self.has_permission(agent_name, tool.required_permission):
                raise PermissionError(
                    f"Agent '{agent_name}' lacks permission "
                    f"'{tool.required_permission}' required for tool '{tool_name}'"
                )

        logger.info(f"[tool_registry] Agent '{agent_name}' → tool '{tool_name}'")
        return await tool.call(**kwargs)

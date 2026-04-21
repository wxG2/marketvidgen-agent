from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]
LazyToolLoader = Callable[[], dict[str, Any]]


class ToolDefinition:
    """Describes a single callable tool available to agents.

    Follows Claude API conventions: the canonical schema is stored as
    ``input_schema`` (JSON Schema object) rather than the OpenAI-style
    ``parameters`` wrapper.  Use :meth:`to_openai_tool` when the downstream
    LLM expects OpenAI-compatible function-calling format (e.g. Qwen
    dashscope compatible-mode).

    Attributes:
        name: Unique tool identifier (snake_case, max 64 chars).
        description: 3-4 sentence description explaining what the tool does,
            when to call it, what each parameter means, and any caveats.
            This is the most important factor in how well the LLM selects tools.
        input_schema: JSON Schema object (Claude ``input_schema`` format).
            Root must be ``{"type": "object", "properties": {...}}``.
            Every property should carry its own ``description``.
        fn: Async callable that executes the tool.
        required_permission: Optional permission string an agent must hold.
    """

    def __init__(
        self,
        name: str,
        description: str,
        fn: Optional[ToolFn] = None,
        input_schema: Optional[dict[str, Any]] = None,
        required_permission: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        source_path: Optional[str] = None,
        lazy_loader: Optional[LazyToolLoader] = None,
    ) -> None:
        self.name = name
        self.description = description
        self.fn = fn
        self.input_schema: dict[str, Any] = input_schema or {
            "type": "object",
            "properties": {},
        }
        self.required_permission = required_permission
        self.metadata: dict[str, Any] = metadata or {}
        self.source_path = source_path
        self._lazy_loader = lazy_loader
        self._loaded = lazy_loader is None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def ensure_loaded(self) -> None:
        if self._lazy_loader is None:
            self._loaded = True
            return

        payload = self._lazy_loader() or {}
        loaded_fn = payload.get("fn")
        if loaded_fn is not None:
            self.fn = loaded_fn

        loaded_schema = payload.get("input_schema")
        if loaded_schema:
            self.input_schema = loaded_schema

        loaded_description = payload.get("description")
        if isinstance(loaded_description, str) and loaded_description.strip():
            self.description = loaded_description

        loaded_metadata = payload.get("metadata")
        if isinstance(loaded_metadata, dict):
            self.metadata = {**self.metadata, **loaded_metadata}

        loaded_source_path = payload.get("source_path")
        if isinstance(loaded_source_path, str) and loaded_source_path.strip():
            self.source_path = loaded_source_path

        self._lazy_loader = None
        self._loaded = True

    # ── Format converters ────────────────────────────────────────────────────

    def to_claude_tool(self) -> dict[str, Any]:
        """Return the canonical Claude API tool definition.

        Format::

            {
                "name": "...",
                "description": "...",
                "input_schema": {"type": "object", "properties": {...}, "required": [...]}
            }
        """
        self.ensure_loaded()
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai_tool(self) -> dict[str, Any]:
        """Return an OpenAI-compatible function-calling definition.

        Required by Qwen dashscope compatible-mode and any OpenAI-format API.

        Format::

            {
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {"type": "object", "properties": {...}}
                }
            }
        """
        self.ensure_loaded()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    async def call(self, **kwargs: Any) -> Any:
        self.ensure_loaded()
        if self.fn is None:
            raise RuntimeError(f"Tool '{self.name}' is not executable.")
        return await self.fn(**kwargs)


class ToolRegistry:
    """Central registry of tools that agents can discover and invoke at runtime.

    Enables agents to move beyond hardcoded capability lists toward dynamic
    tool selection based on context.  Each tool can carry an optional
    *permission* requirement; agent permissions are granted explicitly via
    :meth:`grant_permission`.

    Tool schemas are stored in Claude API canonical format (``input_schema``)
    and converted to the target LLM format on demand via :meth:`list_tools`.

    Usage::

        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="search_materials",
            description="...",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword"},
                },
                "required": ["query"],
            },
            fn=my_search_fn,
        ))
        registry.grant_permission("orchestrator", "search_materials")

        # Get schemas for LLM tool-calling (Qwen / OpenAI format)
        tools = registry.list_tools("orchestrator", fmt="openai")

        # Invoke
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

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Return the ToolDefinition for *name*, or None if not registered."""
        return self._tools.get(name)

    # ── Permissions ──────────────────────────────────────────────────────────

    def grant_permission(self, agent_name: str, permission: str) -> None:
        """Grant *permission* to *agent_name*."""
        self._agent_permissions.setdefault(agent_name, set()).add(permission)

    def revoke_permission(self, agent_name: str, permission: str) -> None:
        self._agent_permissions.get(agent_name, set()).discard(permission)

    def has_permission(self, agent_name: str, permission: str) -> bool:
        return permission in self._agent_permissions.get(agent_name, set())

    # ── Discovery ────────────────────────────────────────────────────────────

    def list_tools(
        self,
        agent_name: Optional[str] = None,
        fmt: str = "openai",
    ) -> list[dict[str, Any]]:
        """Return tool schemas visible to *agent_name* (or all tools if None).

        Args:
            agent_name: Filter to tools the agent has permission for.
            fmt: Output format — ``"openai"`` (default, Qwen-compatible) or
                 ``"claude"`` (Anthropic API format).

        Returns:
            List of tool schema dicts ready to pass to the LLM API.
        """
        result: list[dict[str, Any]] = []
        for tool in self._tools.values():
            visible = (
                tool.required_permission is None
                or (
                    agent_name is not None
                    and self.has_permission(agent_name, tool.required_permission)
                )
            )
            if not visible:
                continue
            if fmt == "claude":
                result.append(tool.to_claude_tool())
            else:
                result.append(tool.to_openai_tool())
        return result

    def list_tool_definitions(self, agent_name: Optional[str] = None) -> list[ToolDefinition]:
        """Return visible ToolDefinition objects for *agent_name*."""
        result: list[ToolDefinition] = []
        for tool in self._tools.values():
            visible = (
                tool.required_permission is None
                or (
                    agent_name is not None
                    and self.has_permission(agent_name, tool.required_permission)
                )
            )
            if visible:
                result.append(tool)
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

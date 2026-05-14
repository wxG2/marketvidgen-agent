from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.agents.core import ToolDefinition

ToolFn = Callable[..., Awaitable[Any]]

_SKILL_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_SKILL_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


@dataclass(frozen=True)
class RuntimeSkillManifest:
    """Metadata-only runtime skill descriptor discovered from `SKILL.md`.

    This mirrors Claude's progressive disclosure model:
    - startup: only frontmatter metadata is loaded
    - selected skill: full `SKILL.md` body is read
    - execution/fallback: schema + runtime entry are loaded on demand
    """

    skill_name: str
    tool_name: str
    description: str
    skill_dir: Path
    skill_md_path: Path
    runtime_entry: Path
    schema_path: Path
    use_when: tuple[str, ...] = field(default_factory=tuple)
    do_not_use_when: tuple[str, ...] = field(default_factory=tuple)
    required_inputs: tuple[str, ...] = field(default_factory=tuple)
    validation_rules: tuple[str, ...] = field(default_factory=tuple)
    routing_hints: tuple[str, ...] = field(default_factory=tuple)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    disable_model_invocation: bool = False
    user_invocable: bool = False
    context: str = "direct"
    agent: str | None = None
    required_permission: str | None = None

    def __post_init__(self) -> None:
        if not _SKILL_PACKAGE_NAME_RE.fullmatch(self.skill_name):
            raise ValueError(
                "Runtime skill package names must use lowercase letters, digits, and hyphens."
            )
        if not _SKILL_NAME_RE.fullmatch(self.tool_name):
            raise ValueError(
                "Runtime skill tool names must be snake_case identifiers."
            )
        if not self.description.strip():
            raise ValueError("RuntimeSkillManifest.description must not be empty.")
        if self.context not in {"direct", "fork"}:
            raise ValueError("RuntimeSkillManifest.context must be either 'direct' or 'fork'.")

    def metadata(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "name": self.tool_name,
            "description": self.description,
            "use_when": list(self.use_when),
            "do_not_use_when": list(self.do_not_use_when),
            "required_inputs": list(self.required_inputs),
            "validation_rules": list(self.validation_rules),
            "routing_hints": list(self.routing_hints),
            "allowed_tools": list(self.allowed_tools),
            "disable_model_invocation": self.disable_model_invocation,
            "user_invocable": self.user_invocable,
            "context": self.context,
            "agent": self.agent,
            "required_permission": self.required_permission,
            "source_path": str(self.skill_md_path),
            "source_label": f"{self.skill_name}/SKILL.md",
            "runtime_entry": str(self.runtime_entry),
            "schema_path": str(self.schema_path),
        }


@dataclass(frozen=True)
class RuntimeSkillSpec:
    """Project-local runtime skill contract for Python-backed chat skills.

    These skills are not filesystem `SKILL.md` packages. They are function-
    calling tools exposed to Orchestrator chat routing, so we keep snake_case names that
    match tool invocation identifiers while still preserving the same
    design-time metadata: routing description, boundaries, required inputs,
    validation rules, and invocation policy.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    skill_name: str | None = None
    use_when: tuple[str, ...] = field(default_factory=tuple)
    do_not_use_when: tuple[str, ...] = field(default_factory=tuple)
    required_inputs: tuple[str, ...] = field(default_factory=tuple)
    validation_rules: tuple[str, ...] = field(default_factory=tuple)
    routing_hints: tuple[str, ...] = field(default_factory=tuple)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    disable_model_invocation: bool = False
    user_invocable: bool = False
    context: str = "direct"
    agent: str | None = None
    required_permission: str | None = None
    source_path: str | None = None
    instructions: str = ""
    supporting_files: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _SKILL_NAME_RE.fullmatch(self.name):
            raise ValueError(
                "Runtime skill names must be snake_case tool identifiers "
                "containing only lowercase letters, digits, and underscores."
            )
        if self.skill_name is not None and not _SKILL_PACKAGE_NAME_RE.fullmatch(self.skill_name):
            raise ValueError(
                "RuntimeSkillSpec.skill_name must use lowercase letters, digits, and hyphens."
            )
        if not self.description.strip():
            raise ValueError("RuntimeSkillSpec.description must not be empty.")
        if self.input_schema.get("type") != "object":
            raise ValueError("RuntimeSkillSpec.input_schema must be a JSON object schema.")
        if self.context not in {"direct", "fork"}:
            raise ValueError("RuntimeSkillSpec.context must be either 'direct' or 'fork'.")

    def to_tool_definition(self, fn: ToolFn) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            fn=fn,
            required_permission=self.required_permission,
            metadata=self.metadata(),
            source_path=self.source_path,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name or self.name.replace("_", "-"),
            "name": self.name,
            "description": self.description,
            "use_when": list(self.use_when),
            "do_not_use_when": list(self.do_not_use_when),
            "required_inputs": list(self.required_inputs),
            "validation_rules": list(self.validation_rules),
            "routing_hints": list(self.routing_hints),
            "allowed_tools": list(self.allowed_tools),
            "disable_model_invocation": self.disable_model_invocation,
            "user_invocable": self.user_invocable,
            "context": self.context,
            "agent": self.agent,
            "required_permission": self.required_permission,
            "source_path": self.source_path,
            "instructions": self.instructions,
            "supporting_files": list(self.supporting_files),
        }

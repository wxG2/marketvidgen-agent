from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import yaml

from app.agents.core import ToolDefinition, ToolRegistry
from app.agents.skills.spec import RuntimeSkillManifest, RuntimeSkillSpec

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

RuntimeSkillBinding = RuntimeSkillManifest


def discover_runtime_skill_manifests(
    skills_root: str | None = None,
) -> list[RuntimeSkillManifest]:
    root = _resolve_skills_root(skills_root)
    manifests: list[RuntimeSkillManifest] = []
    if not root.exists():
        return manifests

    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith(("_", ".")) or entry.name == "__pycache__":
            continue

        skill_md_path = entry / "SKILL.md"
        if not skill_md_path.is_file():
            continue
        manifests.append(_load_manifest_from_path(skill_md_path))

    logger.info(
        "Discovered %s runtime skills: %s",
        len(manifests),
        [item.tool_name for item in manifests],
    )
    return manifests


def discover_runtime_skill_bindings(
    skills_root: str | None = None,
) -> list[RuntimeSkillBinding]:
    return discover_runtime_skill_manifests(skills_root=skills_root)


def get_runtime_skill_manifest(
    identifier: str,
    *,
    skills_root: str | None = None,
) -> RuntimeSkillManifest:
    for manifest in discover_runtime_skill_manifests(skills_root=skills_root):
        if identifier in {manifest.tool_name, manifest.skill_name}:
            return manifest
    raise KeyError(f"Runtime skill '{identifier}' not found")


def load_runtime_skill_spec(
    identifier: str,
    *,
    skills_root: str | None = None,
) -> RuntimeSkillSpec:
    manifest = get_runtime_skill_manifest(identifier, skills_root=skills_root)
    _frontmatter, body = _read_skill_document(manifest.skill_md_path)
    input_schema = json.loads(manifest.schema_path.read_text(encoding="utf-8"))
    supporting_files = _extract_supporting_files(body, manifest.skill_dir)
    return RuntimeSkillSpec(
        name=manifest.tool_name,
        skill_name=manifest.skill_name,
        description=manifest.description,
        input_schema=input_schema,
        use_when=manifest.use_when,
        do_not_use_when=manifest.do_not_use_when,
        required_inputs=manifest.required_inputs,
        validation_rules=manifest.validation_rules,
        routing_hints=manifest.routing_hints,
        allowed_tools=manifest.allowed_tools,
        disable_model_invocation=manifest.disable_model_invocation,
        user_invocable=manifest.user_invocable,
        context=manifest.context,
        agent=manifest.agent,
        required_permission=manifest.required_permission,
        source_path=str(manifest.skill_md_path),
        instructions=body.strip(),
        supporting_files=tuple(str(path) for path in supporting_files),
    )


def load_runtime_skill_factory(
    identifier: str,
    *,
    skills_root: str | None = None,
) -> Callable[..., Any]:
    manifest = get_runtime_skill_manifest(identifier, skills_root=skills_root)
    module = _load_runtime_module(manifest)
    expected_factory_name = f"create_{manifest.tool_name}_skill"
    factory = getattr(module, expected_factory_name, None)
    if callable(factory):
        return factory

    fallback_factories = [
        value
        for name, value in vars(module).items()
        if name.startswith("create_") and name.endswith("_skill") and callable(value)
    ]
    if len(fallback_factories) == 1:
        return fallback_factories[0]

    raise ValueError(
        f"Runtime skill '{manifest.skill_name}' must expose callable "
        f"'{expected_factory_name}' or exactly one create_*_skill factory."
    )


def register_runtime_skills(
    *,
    tool_registry: ToolRegistry,
    dependency_map: dict[str, Any],
    agent_name: str | None = None,
    skills_root: str | None = None,
) -> list[RuntimeSkillManifest]:
    manifests = discover_runtime_skill_manifests(skills_root=skills_root)
    for manifest in manifests:
        tool_registry.register(
            ToolDefinition(
                name=manifest.tool_name,
                description=manifest.description,
                fn=None,
                input_schema=None,
                required_permission=manifest.required_permission,
                metadata=manifest.metadata(),
                source_path=str(manifest.skill_md_path),
                lazy_loader=lambda manifest=manifest: _build_lazy_tool_payload(manifest, dependency_map),
            )
        )
        if agent_name and manifest.required_permission:
            tool_registry.grant_permission(agent_name, manifest.required_permission)
        logger.info(
            "Registered runtime skill '%s' from %s",
            manifest.tool_name,
            manifest.skill_md_path,
        )
    return manifests


def _build_lazy_tool_payload(
    manifest: RuntimeSkillManifest,
    dependency_map: dict[str, Any],
) -> dict[str, Any]:
    spec = load_runtime_skill_spec(manifest.tool_name)
    factory = load_runtime_skill_factory(manifest.tool_name)
    fn = _instantiate_factory(factory, manifest.tool_name, dependency_map)
    return {
        "fn": fn,
        "input_schema": spec.input_schema,
        "description": spec.description,
        "metadata": spec.metadata(),
        "source_path": str(manifest.skill_md_path),
    }


def _instantiate_factory(
    factory: Callable[..., Any],
    tool_name: str,
    dependency_map: dict[str, Any],
) -> Any:
    signature = inspect.signature(factory)
    factory_kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if name in dependency_map:
            factory_kwargs[name] = dependency_map[name]
            continue
        if parameter.default is inspect.Signature.empty:
            raise ValueError(
                f"Cannot instantiate runtime skill '{tool_name}': "
                f"missing dependency '{name}'."
            )
    return factory(**factory_kwargs)


@lru_cache(maxsize=None)
def _load_manifest_from_path(skill_md_path: Path) -> RuntimeSkillManifest:
    frontmatter, _body = _read_skill_document(skill_md_path)
    skill_dir = skill_md_path.parent
    skill_name = str(frontmatter.get("name") or skill_dir.name).strip()
    description = str(frontmatter.get("description") or "").strip()
    tool_name = str(frontmatter.get("tool-name") or skill_name.replace("-", "_")).strip()
    runtime_entry = skill_dir / str(frontmatter.get("runtime-entry") or "runtime.py")
    schema_path = skill_dir / str(frontmatter.get("schema-file") or "schema.json")
    return RuntimeSkillManifest(
        skill_name=skill_name,
        tool_name=tool_name,
        description=description,
        skill_dir=skill_dir,
        skill_md_path=skill_md_path,
        runtime_entry=runtime_entry,
        schema_path=schema_path,
        use_when=_to_tuple(frontmatter.get("use-when")),
        do_not_use_when=_to_tuple(frontmatter.get("do-not-use-when")),
        required_inputs=_to_tuple(frontmatter.get("required-inputs")),
        validation_rules=_to_tuple(frontmatter.get("validation-rules")),
        routing_hints=_to_tuple(frontmatter.get("routing-hints")),
        allowed_tools=_to_tuple(frontmatter.get("allowed-tools")),
        disable_model_invocation=bool(frontmatter.get("disable-model-invocation", False)),
        user_invocable=bool(frontmatter.get("user-invocable", False)),
        context=str(frontmatter.get("context") or "direct").strip(),
        agent=_optional_str(frontmatter.get("agent")),
        required_permission=_optional_str(frontmatter.get("required-permission")),
    )


def _read_skill_document(skill_md_path: Path) -> tuple[dict[str, Any], str]:
    content = skill_md_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError(f"Runtime skill '{skill_md_path}' must start with YAML frontmatter.")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        raise ValueError(f"Runtime skill '{skill_md_path}' has invalid YAML frontmatter.")
    body = content[match.end():]
    return frontmatter, body


def _extract_supporting_files(body: str, skill_dir: Path) -> list[Path]:
    supporting_files: list[Path] = []
    for raw_link in _MARKDOWN_LINK_RE.findall(body):
        link = raw_link.strip()
        if not link or "://" in link or link.startswith(("#", "/")):
            continue
        candidate = (skill_dir / link).resolve()
        if not candidate.exists() or not candidate.is_file():
            continue
        if candidate.suffix.lower() not in {".md", ".txt"}:
            continue
        supporting_files.append(candidate)
    return supporting_files


@lru_cache(maxsize=None)
def _load_runtime_module(manifest: RuntimeSkillManifest) -> ModuleType:
    module_name = f"app.agents.skills.runtime.{manifest.tool_name}"
    spec = importlib.util.spec_from_file_location(module_name, manifest.runtime_entry)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load runtime module for skill '{manifest.tool_name}'")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_skills_root(skills_root: str | None) -> Path:
    if skills_root:
        return Path(skills_root).resolve()
    return Path(__file__).resolve().parent


def _to_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

"""Load only reviewed, version-pinned skill bundles from the application image."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

KNOWN_TOOLS = {
    "repo.list_tree",
    "repo.read_file",
    "repo.search_text",
    "repo.read_manifest",
    "repo.get_git_metadata",
}


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=1, max_length=500)
    triggers: dict = Field(default_factory=dict)
    allowed_tools: list[str] = Field(min_length=1, max_length=10)
    risk_tier: str = "read_only"
    input_schema: str = "analysis-facts-v1"
    output_schema: str = "infrastructure-assessment-v2"
    max_context_chars: int = Field(default=12000, ge=1, le=40000)
    context_files: list[str] = Field(default_factory=list, max_length=10)


@dataclass(frozen=True)
class SkillBundle:
    metadata: SkillMetadata
    instructions: str
    references: dict[str, str]
    content_hash: str

    @property
    def key(self) -> tuple[str, str]:
        return self.metadata.id, self.metadata.version

    @property
    def context(self) -> str:
        refs = "".join(
            f"\n\n## Reference: {name}\n{text}" for name, text in self.references.items()
        )
        return self.instructions + refs


class SkillRegistry:
    def __init__(self, root: Path, *, max_context_chars: int):
        self.root = root.resolve()
        self.max_context_chars = max_context_chars
        self._bundles: dict[tuple[str, str], SkillBundle] = {}
        self._active: dict[str, str] = {}

    def load(self) -> "SkillRegistry":
        active_path = self.root / "active.yaml"
        if not active_path.is_file():
            raise ValueError("skill_active_versions_missing")
        try:
            active = yaml.safe_load(active_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError("skill_active_versions_invalid") from exc
        if not isinstance(active, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in active.items()
        ):
            raise ValueError("skill_active_versions_invalid")
        self._active = active
        for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
            self._load_bundle(directory)
        if not self._bundles:
            raise ValueError("skill_registry_empty")
        for skill_id, version in self._active.items():
            if (skill_id, version) not in self._bundles:
                raise ValueError("skill_active_version_unknown")
        return self

    def _load_bundle(self, directory: Path) -> None:
        metadata_path, instructions_path = directory / "skill.yaml", directory / "SKILL.md"
        if not metadata_path.is_file() or not instructions_path.is_file():
            return
        try:
            metadata = SkillMetadata.model_validate(
                yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            )
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise ValueError("skill_metadata_invalid") from exc
        if metadata.id != directory.name or any(
            tool not in KNOWN_TOOLS for tool in metadata.allowed_tools
        ):
            raise ValueError("skill_metadata_invalid")
        if (
            metadata.risk_tier != "read_only"
            or metadata.output_schema != "infrastructure-assessment-v2"
        ):
            raise ValueError("skill_policy_invalid")
        instructions = instructions_path.read_text(encoding="utf-8")
        references: dict[str, str] = {}
        payload: list[tuple[str, bytes]] = [
            ("skill.yaml", metadata_path.read_bytes()),
            ("SKILL.md", instructions.encode()),
        ]
        for reference in metadata.context_files:
            candidate = (directory / reference).resolve()
            if directory not in candidate.parents or not candidate.is_file():
                raise ValueError("skill_reference_invalid")
            text = candidate.read_text(encoding="utf-8")
            references[reference] = text
            payload.append((reference, text.encode()))
        content = instructions + "".join(references.values())
        if len(content) > metadata.max_context_chars:
            raise ValueError("skill_context_exceeded")
        digest = hashlib.sha256()
        for name, data in sorted(payload):
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
        bundle = SkillBundle(metadata, instructions, references, digest.hexdigest())
        if bundle.key in self._bundles:
            raise ValueError("skill_duplicate")
        self._bundles[bundle.key] = bundle

    def select(self, facts: dict) -> list[SkillBundle]:
        paths = set(facts.get("paths", []))
        languages = set(facts.get("languages", []))
        selected: list[SkillBundle] = []
        for skill_id, version in sorted(self._active.items()):
            bundle = self._bundles[(skill_id, version)]
            trigger = bundle.metadata.triggers
            any_files = set(trigger.get("any_files", []))
            any_languages = set(trigger.get("any_languages", []))
            if (
                trigger.get("always")
                or any_files.intersection(paths)
                or any_languages.intersection(languages)
            ):
                selected.append(bundle)
        total = sum(len(bundle.context) for bundle in selected)
        if total > self.max_context_chars:
            raise ValueError("selected_skill_context_exceeded")
        return selected

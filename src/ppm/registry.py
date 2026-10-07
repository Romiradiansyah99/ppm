"""The definition registry (plan section 3.4): one YAML per entity, versioned
in git, signed by the named owner. The registry is the enforcement point -
ingest/action graphs validate every write against it.

Every enum in the shipped registry is a GUESS tagged in the plan until the
named owner signs it, so the loader exposes `unresolved_definitions()` for
`ppm status` to surface the outstanding definition fights.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ppm.config import get_settings

REQUIRED_TOP_KEYS = ("entity", "table", "registry_version", "status", "owner")


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: str = ""

    def entity(self, name: str) -> dict[str, Any]:
        try:
            return self.entities[name]
        except KeyError:
            raise RegistryError(f"unknown entity '{name}' (registry knows: {sorted(self.entities)})") from None

    def enum_values(self, entity: str, field_name: str) -> list[str]:
        enums = self.entity(entity).get("enums", {})
        spec = enums.get(field_name)
        if not spec:
            return []
        return list(spec.get("values", []))

    def is_valid_enum(self, entity: str, field_name: str, value: str | None) -> bool:
        if value is None:
            return False
        values = self.enum_values(entity, field_name)
        return not values or value in values

    # --- units ---------------------------------------------------------------

    def canonical_unit(self, raw: str | None) -> str | None:
        if not raw:
            return None
        key = " ".join(str(raw).lower().split()).strip(".")
        aliases: dict[str, str] = {}
        canonical = self.entity("benchmark_rate").get("units", {}).get("canonical", {})
        for canon, names in canonical.items():
            aliases[canon] = canon
            for name in names:
                aliases[" ".join(str(name).lower().split()).strip(".")] = canon
        return aliases.get(key)

    # --- element codes ---------------------------------------------------------

    def element_codes_resolved(self) -> bool:
        spec = self.entity("benchmark_rate").get("element_codes", {})
        return spec.get("status") == "RESOLVED" and bool(spec.get("codes"))

    def is_known_element_code(self, code: str | None) -> bool:
        if not code:
            return False
        spec = self.entity("benchmark_rate").get("element_codes", {})
        return str(code).strip().upper() in {str(c).upper() for c in spec.get("codes", [])}

    def unknown_code_severity(self) -> str:
        """While the element-code fight is unresolved, unknown codes are
        warnings so Phase 0 can run; resolved lists make them hard errors."""
        spec = self.entity("benchmark_rate").get("element_codes", {})
        if not self.element_codes_resolved():
            return "warning"
        return spec.get("unknown_code_severity", "error")

    # --- validation rules -------------------------------------------------------

    def require_fields(self, entity: str) -> list[str]:
        return list(self.entity(entity).get("validation", {}).get("require", []))

    def warn_fields(self, entity: str) -> list[str]:
        return list(self.entity(entity).get("validation", {}).get("warn", []))

    def spec_levels(self) -> list[str]:
        return self.enum_values("benchmark_rate", "spec_level")

    def area_bases(self) -> list[str]:
        return list(self.entity("cost_plan").get("enums", {}).get("area_basis", {}).get("values", []))

    def rate_bases(self) -> list[str]:
        return list(self.entity("cost_plan").get("enums", {}).get("rate_basis", {}).get("values", []))

    def redaction_blocklist(self) -> list[str]:
        rel = self.entity("document").get("redaction", {}).get("blocklist_file")
        if not rel:
            return []
        path = get_settings().registry_dir.parent / rel
        if not path.exists():
            return []
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]

    # --- reporting ---------------------------------------------------------------

    def unresolved_definitions(self) -> list[str]:
        out: list[str] = []
        for name, doc in sorted(self.entities.items()):
            for section in ("enums",):
                for field_name, spec in (doc.get(section) or {}).items():
                    if spec.get("status") in {"GUESS", "UNRESOLVED"}:
                        out.append(f"{name}.{field_name} [{spec.get('status')}] owner={spec.get('owner', doc.get('owner'))}")
            for section in ("element_codes", "area_basis", "rate_basis", "date_indexation"):
                spec = doc.get(section)
                if isinstance(spec, dict) and spec.get("status") in {"GUESS", "UNRESOLVED"}:
                    out.append(f"{name}.{section} [{spec.get('status')}] owner={spec.get('owner', doc.get('owner'))}")
        return out


def load_registry(registry_dir: Path | str | None = None) -> Registry:
    directory = Path(registry_dir) if registry_dir else get_settings().registry_dir
    entity_dir = directory / "entities"
    if not entity_dir.is_dir():
        raise RegistryError(f"no registry at {entity_dir}")

    entities: dict[str, dict[str, Any]] = {}
    digest = hashlib.sha256()
    for path in sorted(entity_dir.glob("*.yaml")):
        raw = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(raw)
        doc = yaml.safe_load(raw)
        if not isinstance(doc, dict):
            raise RegistryError(f"{path.name}: not a mapping")
        missing = [k for k in REQUIRED_TOP_KEYS if k not in doc]
        if missing:
            raise RegistryError(f"{path.name}: missing keys {missing}")
        name = str(doc["entity"])
        if name != path.stem:
            raise RegistryError(f"{path.name}: entity '{name}' does not match filename")
        if name in entities:
            raise RegistryError(f"duplicate entity '{name}'")
        entities[name] = doc
    if not entities:
        raise RegistryError(f"no entity files under {entity_dir}")
    return Registry(entities=entities, version=digest.hexdigest()[:12])


@lru_cache(maxsize=4)
def get_registry(registry_dir: str | None = None) -> Registry:
    return load_registry(registry_dir)

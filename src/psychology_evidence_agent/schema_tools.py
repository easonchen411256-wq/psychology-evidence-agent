"""Generate and verify committed Codex JSON Schema artifacts from domain models."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .domain.agent import AgentPlan
from .domain.evidence import EvidenceCard
from .domain.review import ReviewDraft
from .domain.screening import ScreeningResult
from .domain.synthesis import EvidenceSynthesis

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "agent_plan.schema.json": AgentPlan,
    "literature_screening.schema.json": ScreeningResult,
    "evidence_card.schema.json": EvidenceCard,
    "evidence_synthesis.schema.json": EvidenceSynthesis,
    "review_draft.schema.json": ReviewDraft,
}
SCHEMA_DIRECTORY = Path(__file__).parent / "resources" / "schemas"
GENERATED_COMMENT = "GENERATED FILE — DO NOT EDIT MANUALLY. Run `pea schemas export`."


def _inline_references(value: Any, definitions: Mapping[str, Any]) -> Any:
    if isinstance(value, list):
        return [_inline_references(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise ValueError(f"Unsupported generated schema reference: {reference!r}")
        definition = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(definition, dict):
            raise ValueError(f"Missing generated schema definition: {reference!r}")
        inlined = copy.deepcopy(definition)
        inlined.update({key: item for key, item in value.items() if key != "$ref"})
        return _inline_references(inlined, definitions)
    return {
        key: _inline_references(item, definitions) for key, item in value.items() if key != "$defs"
    }


def _make_object_schemas_strict(value: Any) -> Any:
    """Make object schemas compatible with Codex strict response formats.

    Codex requires every property in an object schema to be listed in
    ``required``. Pydantic correctly distinguishes fields with defaults from
    required input fields, but model output schemas need the stricter contract.
    Optional values remain represented by their existing nullable/union schema;
    only the presence of the property itself becomes required.
    """
    if isinstance(value, list):
        return [_make_object_schemas_strict(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {key: _make_object_schemas_strict(item) for key, item in value.items()}
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["required"] = list(properties)
    return normalized


def _normalize_agent_plan_wire_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Use a strict, portable representation for planner argument maps.

    JSON Schema strict mode cannot express an object with arbitrary keys. The
    domain model intentionally keeps tool arguments as a mapping, so the
    Codex-only wire contract represents that mapping as name/value entries.
    The planner adapter converts it back before domain validation.
    """
    steps = schema.get("properties", {}).get("steps")
    if not isinstance(steps, dict):
        return schema
    step_schema = steps.get("items")
    if not isinstance(step_schema, dict):
        return schema
    step_properties = step_schema.get("properties")
    if not isinstance(step_properties, dict) or "arguments" not in step_properties:
        return schema
    step_properties["arguments"] = {
        "type": "array",
        "title": "Arguments",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                        {"type": "number"},
                        {"type": "boolean"},
                        {"type": "array", "items": {"type": "string"}},
                    ]
                },
            },
            "required": ["name", "value"],
        },
    }
    return schema


def normalize_for_codex(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline Pydantic references for the CLI's simplest portable JSON Schema subset."""
    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        raise ValueError("Pydantic generated an invalid $defs object.")
    normalized = _inline_references(copy.deepcopy(schema), definitions)
    if not isinstance(normalized, dict):
        raise ValueError("Pydantic generated a non-object root schema.")
    normalized = _make_object_schemas_strict(normalized)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": GENERATED_COMMENT,
        **normalized,
    }


def generated_schemas() -> dict[str, dict[str, Any]]:
    generated: dict[str, dict[str, Any]] = {}
    for name, model in SCHEMA_MODELS.items():
        schema = normalize_for_codex(model.model_json_schema())
        if name == "agent_plan.schema.json":
            schema = _normalize_agent_plan_wire_schema(schema)
        generated[name] = schema
    return generated


def schema_text(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export_schemas() -> list[Path]:
    """Write deterministic generated schema artifacts into the source checkout."""
    SCHEMA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in generated_schemas().items():
        path = SCHEMA_DIRECTORY / name
        path.write_text(schema_text(schema), encoding="utf-8")
        written.append(path)
    return written


def check_schemas() -> list[Path]:
    """Return schema paths whose committed bytes drifted from domain models."""
    drifted: list[Path] = []
    for name, schema in generated_schemas().items():
        path = SCHEMA_DIRECTORY / name
        expected = schema_text(schema)
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != expected:
            drifted.append(path)
    return drifted


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or check Pydantic-derived JSON Schema files."
    )
    subcommands = parser.add_subparsers(dest="action", required=True)
    subcommands.add_parser("export", help="Write generated schemas into package resources.")
    subcommands.add_parser("check", help="Fail when committed schemas drift from domain models.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.action == "export":
        for path in export_schemas():
            print(f"Generated {path}")
        return 0
    drifted = check_schemas()
    if drifted:
        print("Generated schemas differ from committed files:")
        print("\n".join(str(path) for path in drifted))
        return 1
    print("Generated schemas match committed files.")
    return 0

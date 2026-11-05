
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


CONSTRAINT_KEYWORDS = {
    "minLength", "maxLength",
    "minItems", "maxItems",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "pattern", "format", "default",
}

# OpenAI Structured Outputs supports these JSON Schema constraints for the
# non-fine-tuned models used by Deep Research. The fine-tuned-model exclusions
# documented by the provider do not apply to this adapter's configured model.
STRICT_PROVIDER_CONSTRAINTS = {
    "minLength", "maxLength",
    "minItems", "maxItems",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "pattern", "format",
}
SUPPORTED_STRING_FORMATS = {
    "date-time", "time", "date", "duration", "email", "hostname",
    "ipv4", "ipv6", "uuid",
}


def to_openai_schema(
    model: type[BaseModel], *, preserve_numeric_bounds: bool = False,
    preserve_strict_constraints: bool = False,
) -> dict[str, Any]:
    schema = deepcopy(model.model_json_schema())

    def clean(node: Any) -> None:
        if isinstance(node, dict):
            constraints: list[str] = []
            if "minimum" in node:
                constraints.append(f"value must be greater than or equal to {node['minimum']}")
            if "maximum" in node:
                constraints.append(f"value must be less than or equal to {node['maximum']}")
            if "minLength" in node:
                constraints.append(f"length must be at least {node['minLength']}")
            if "maxLength" in node:
                constraints.append(f"length must be at most {node['maxLength']}")
            if "minItems" in node:
                constraints.append(f"array must contain at least {node['minItems']} items")
            if "maxItems" in node:
                constraints.append(f"array must contain at most {node['maxItems']} items")
            if constraints:
                existing = str(node.get("description") or "").strip()
                suffix = "Constraints: " + "; ".join(constraints) + "."
                node["description"] = f"{existing} {suffix}".strip()
            preserved = set()
            if preserve_numeric_bounds:
                preserved.update({"minimum", "maximum"})
            if preserve_strict_constraints:
                preserved.update(STRICT_PROVIDER_CONSTRAINTS)
            if node.get("format") not in SUPPORTED_STRING_FORMATS:
                preserved.discard("format")
            unsupported = CONSTRAINT_KEYWORDS - preserved
            for key in unsupported:
                node.pop(key, None)

            if node.get("type") == "object":
                properties = node.get("properties", {})
                node["required"] = list(properties)
                node["additionalProperties"] = False

            for value in node.values():
                clean(value)

        elif isinstance(node, list):
            for item in node:
                clean(item)

    clean(schema)
    return schema

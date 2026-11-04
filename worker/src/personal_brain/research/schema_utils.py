
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


UNSUPPORTED = {
    "minLength", "maxLength",
    "minItems", "maxItems",
    "minimum", "maximum",
    "pattern", "format", "default",
}


def to_openai_schema(model: type[BaseModel]) -> dict[str, Any]:
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
            if constraints:
                existing = str(node.get("description") or "").strip()
                suffix = "Constraints: " + "; ".join(constraints) + "."
                node["description"] = f"{existing} {suffix}".strip()
            for key in UNSUPPORTED:
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

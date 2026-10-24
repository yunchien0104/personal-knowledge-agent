
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

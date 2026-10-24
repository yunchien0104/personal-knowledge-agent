
from personal_brain.research.contracts import (
    AtomicResearchDraft,
    VerificationResult,
)

from personal_brain.research.schema_utils import to_openai_schema

def check_schema(model):
    schema = to_openai_schema(model)
    errors = []

    unsupported = {
        "minLength", "maxLength",
        "minItems", "maxItems",
        "minimum", "maximum",
        "pattern", "format",
        "default",
    }

    def walk(node, path="root"):
        if isinstance(node, dict):
            if node.get("type") == "object":
                properties = node.get("properties", {})
                required = set(node.get("required", []))

                if set(properties) != required:
                    errors.append(
                        f"{path}: required fields mismatch"
                    )

                if node.get("additionalProperties") is not False:
                    errors.append(
                        f"{path}: additionalProperties must be false"
                    )

            for key, value in node.items():
                if key in unsupported:
                    errors.append(f"{path}: unsupported {key}")
                walk(value, f"{path}.{key}")

        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(schema)

    if errors:
        print(f"FAIL: {model.__name__}")
        for error in errors:
            print(" -", error)
    else:
        print(f"PASS: {model.__name__}")

    return len(errors) == 0


results = [
    check_schema(AtomicResearchDraft),
    check_schema(VerificationResult),
]

if not all(results):
    raise SystemExit(1)

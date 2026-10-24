import json
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class StructuredLLMClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def generate(
        self,
        system_prompt: str,
        user_input: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_input, ensure_ascii=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "research_output",
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }

        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=90) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LLM HTTP {error.code}: {detail[:2000]}"
            ) from error

        except (URLError, TimeoutError) as error:
            raise RuntimeError("LLM connection failed") from error

        content = result["choices"][0]["message"]["content"]
        return json.loads(content)

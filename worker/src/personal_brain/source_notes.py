from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from personal_brain.ingestion import LeaseLostError, ProcessResult, _LeaseGuard
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


PROMPT_VERSION = "source-note-v1"
CATEGORIES = ("facts", "dataPoints", "arguments", "conclusions", "uncertainties")
CATEGORY_NAME = {
    "facts": "fact",
    "dataPoints": "data",
    "arguments": "argument",
    "conclusions": "conclusion",
    "uncertainties": "uncertainty",
}


class ModelError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StructuredNoteModel(Protocol):
    provider: str
    model_name: str

    def generate(self, document: Mapping[str, Any]) -> Mapping[str, Any]: ...


class LocalExtractiveStructuredNoteModel:
    """Zero-cost E2E adapter; production keeps the external adapter by default."""

    provider = "local"
    model_name = "extractive-source-note-v1"

    def generate(self, document: Mapping[str, Any]) -> Mapping[str, Any]:
        rows = document.get("evidence")
        if not isinstance(rows, list):
            raise ModelError("invalid_source_context", "Source Evidence is invalid", retryable=False)
        facts: list[dict[str, Any]] = []
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            evidence_id = row.get("id")
            excerpt = row.get("excerpt")
            if not isinstance(evidence_id, str) or not isinstance(excerpt, str) or not excerpt.strip():
                continue
            quote = excerpt.strip()[:4000]
            facts.append({
                "text": quote,
                "citations": [{"evidenceId": evidence_id, "quote": quote}],
            })
        if not facts:
            raise ModelError("invalid_source_context", "Source Evidence is empty", retryable=False)
        return {
            "title": "Extractive Source Note",
            "summary": "This draft contains only verbatim statements extracted from the uploaded source.",
            "topics": ["Uploaded source"],
            "facts": facts,
            "dataPoints": [],
            "arguments": [],
            "conclusions": [],
            "uncertainties": [],
        }


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


NOTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "topics", *CATEGORIES],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "summary": {"type": "string", "minLength": 1, "maxLength": 12000},
        "topics": {
            "type": "array", "maxItems": 30,
            "items": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        **{
            field: {
                "type": "array", "maxItems": 30,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["text", "citations"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "citations": {
                            "type": "array", "minItems": 1, "maxItems": 20,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["evidenceId", "quote"],
                                "properties": {
                                    "evidenceId": {"type": "string", "format": "uuid"},
                                    "quote": {"type": "string", "minLength": 1, "maxLength": 4000},
                                },
                            },
                        },
                    },
                },
            }
            for field in CATEGORIES
        },
    },
}


class OpenAICompatibleStructuredNoteModel:
    def __init__(
        self, base_url: str, api_key: str, model_name: str, provider: str,
        max_output_tokens: int = 4096,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("LLM_API_BASE_URL must be an HTTPS origin")
        if not api_key or not model_name or not provider:
            raise ValueError("LLM provider configuration is incomplete")
        if not 256 <= max_output_tokens <= 16384:
            raise ValueError("SOURCE_NOTE_MAX_OUTPUT_TOKENS must be between 256 and 16384")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.model_name = model_name
        self.provider = provider
        self._max_output_tokens = max_output_tokens
        self._opener = build_opener(_RejectRedirects())

    @classmethod
    def from_environment(cls) -> OpenAICompatibleStructuredNoteModel:
        return cls(
            os.environ.get("LLM_API_BASE_URL", ""),
            os.environ.get("LLM_API_KEY", ""),
            os.environ.get("SOURCE_NOTE_MODEL", ""),
            os.environ.get("LLM_PROVIDER", "openai-compatible"),
            int(os.environ.get("SOURCE_NOTE_MAX_OUTPUT_TOKENS", "4096")),
        )

    def generate(self, document: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps({
            "model": self.model_name,
            "temperature": 0,
            "max_tokens": self._max_output_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Create a factual source note from only the supplied PDF evidence. "
                        "Every item must cite evidenceId and copy an exact supporting quote. "
                        "Do not call a claim verified; preserve uncertainty and omit unsupported claims."
                    ),
                },
                {"role": "user", "content": json.dumps(document, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "source_note", "strict": True, "schema": NOTE_SCHEMA},
            },
        }, ensure_ascii=False, separators=(",", ":")).encode()
        request = Request(
            self._url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=90) as response:
                payload = json.loads(response.read())
        except HTTPError as error:
            raise ModelError(
                "llm_http_error", f"LLM request failed with status {error.code}",
                retryable=error.code == 429 or error.code >= 500,
            ) from error
        except (TimeoutError, URLError, json.JSONDecodeError) as error:
            raise ModelError("llm_unavailable", "LLM response was unavailable", retryable=True) from error
        try:
            content = payload["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False) from error
        if not isinstance(result, dict):
            raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False)
        return result


@dataclass(frozen=True, slots=True)
class ValidatedNote:
    title: str
    body: str
    claims: list[dict[str, Any]]
    output_hash: str


def _text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False)
    return value.strip()


def validate_note_output(
    output: Mapping[str, Any], context: Mapping[str, Any], artifact: Mapping[str, Any]
) -> ValidatedNote:
    if set(output) != {"title", "summary", "topics", *CATEGORIES}:
        raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False)
    title = _text(output.get("title"), maximum=300)
    summary = _text(output.get("summary"), maximum=12000)
    topics = output.get("topics")
    if not isinstance(topics, list) or len(topics) > 30:
        raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False)
    clean_topics = [_text(topic, maximum=120) for topic in topics]

    fingerprint = context.get("sourceFingerprint")
    if artifact.get("source_hash") != fingerprint or artifact.get("schema_version") != 1:
        raise ModelError("source_mismatch", "Parsed source does not match the active Revision", retryable=False)
    pages = artifact.get("pages")
    evidence_rows = context.get("evidence")
    if not isinstance(pages, list) or not isinstance(evidence_rows, list):
        raise ModelError("source_mismatch", "Parsed source context is invalid", retryable=False)
    page_text = {
        page.get("page_number"): page.get("text")
        for page in pages if isinstance(page, dict)
    }
    evidence: dict[str, Mapping[str, Any]] = {}
    for row in evidence_rows:
        if not isinstance(row, dict) or row.get("sourceHash") != fingerprint:
            raise ModelError("source_mismatch", "Evidence does not match the active Revision", retryable=False)
        locator = row.get("locator")
        if not isinstance(locator, dict) or locator.get("kind") != "pdf_page":
            raise ModelError("source_mismatch", "Evidence locator is invalid", retryable=False)
        excerpt = row.get("excerpt")
        if not isinstance(excerpt, str) or page_text.get(locator.get("page_number")) != excerpt:
            raise ModelError("source_mismatch", "Evidence is not traceable to parsed PDF text", retryable=False)
        evidence[str(row.get("id"))] = row

    claims: list[dict[str, Any]] = []
    sections: list[str] = ["## 摘要", summary]
    if clean_topics:
        sections.extend(["## 主題", "\n".join(f"- {topic}" for topic in clean_topics)])
    labels = {
        "facts": "重要事實", "dataPoints": "數據", "arguments": "論點",
        "conclusions": "結論", "uncertainties": "不確定性",
    }
    for field in CATEGORIES:
        items = output.get(field)
        if not isinstance(items, list) or len(items) > 30:
            raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False)
        rendered: list[str] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"text", "citations"}:
                raise ModelError("invalid_model_output", "LLM returned invalid structured output", retryable=False)
            claim_text = _text(item.get("text"), maximum=4000)
            citations = item.get("citations")
            if not isinstance(citations, list) or not 1 <= len(citations) <= 20:
                raise ModelError("invalid_model_output", "Every claim requires source citations", retryable=False)
            clean_citations: list[dict[str, str]] = []
            seen: set[str] = set()
            for citation in citations:
                if not isinstance(citation, dict) or set(citation) != {"evidenceId", "quote"}:
                    raise ModelError("invalid_model_output", "Citation output is invalid", retryable=False)
                evidence_id = citation.get("evidenceId")
                quote = _text(citation.get("quote"), maximum=4000)
                row = evidence.get(evidence_id) if isinstance(evidence_id, str) else None
                if row is None or quote not in str(row.get("excerpt", "")) or evidence_id in seen:
                    raise ModelError("unsupported_claim", "A claim is not supported by cited Evidence", retryable=False)
                seen.add(evidence_id)
                clean_citations.append({"evidenceId": evidence_id, "quote": quote})
            claims.append({"text": claim_text, "category": CATEGORY_NAME[field], "citations": clean_citations})
            rendered.append(f"- {claim_text}")
        if rendered:
            sections.extend([f"## {labels[field]}", "\n".join(rendered)])
    if not claims:
        raise ModelError("invalid_model_output", "Source Note must contain at least one cited claim", retryable=False)
    canonical = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return ValidatedNote(title, "\n\n".join(sections), claims, sha256(canonical).hexdigest())


class SourceNoteWorker:
    def __init__(self, gateway: Any, model: StructuredNoteModel, worker_id: str, *, lease_seconds: int = 120) -> None:
        self.gateway = gateway
        self.model = model
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def process_one(self) -> ProcessResult:
        job: ClaimedJob | None = self.gateway.claim(self.worker_id, self.lease_seconds, "llm")
        if job is None:
            return ProcessResult("idle")
        try:
            if job.payload.job_type != "generate_source_note":
                raise GatewayError("invalid_claim", "Worker received an unsupported Job", retryable=False)
            with _LeaseGuard(self.gateway, job, self.worker_id, self.lease_seconds) as lease:
                context = self.gateway.source_note_context(job, self.worker_id)
                if context is None:
                    raise LeaseLostError("Worker lease is no longer valid")
                artifact_path = context.get("artifactObjectKey")
                if not isinstance(artifact_path, str):
                    raise GatewayError("invalid_source_context", "Source context is invalid", retryable=False)
                raw = self.gateway.download_artifact(artifact_path)
                try:
                    artifact = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise GatewayError("invalid_source_artifact", "Source artifact is invalid", retryable=False) from error
                document = {
                    "revisionId": context.get("revisionId"),
                    "sourceFingerprint": context.get("sourceFingerprint"),
                    "evidence": context.get("evidence"),
                }
                input_size = len(json.dumps(document, ensure_ascii=False))
                maximum = int(os.environ.get("SOURCE_NOTE_MAX_INPUT_CHARS", "200000"))
                if input_size > maximum:
                    raise ModelError("source_too_large", "Source exceeds the configured LLM input limit", retryable=False)
                lease.pulse()
                output = self.model.generate(document)
                lease.pulse()
                validated = validate_note_output(output, context, artifact)
                lease.ensure()
            publication = self.gateway.publish_source_note(
                job, self.worker_id,
                model_provider=self.model.provider, model_name=self.model.model_name,
                prompt_version=PROMPT_VERSION, output_hash=validated.output_hash,
                title=validated.title, body=validated.body, claims=validated.claims,
            )
            if publication is None:
                raise LeaseLostError("Worker lease expired before publication")
            note_id = publication.get("noteId")
            if not isinstance(note_id, str):
                raise GatewayError(
                    "invalid_publication_response", "Source Note publication is invalid", retryable=True
                )
            return ProcessResult("completed", job.id, note_id=note_id)
        except LeaseLostError:
            return ProcessResult("lease_lost", job.id)
        except (ModelError, GatewayError) as error:
            retryable = error.retryable
            self.gateway.report_failure(
                job, self.worker_id,
                error_class="transient" if retryable else "permanent",
                error_code=error.code, error_message=str(error), retryable=retryable,
            )
            return ProcessResult("retry_wait" if retryable else "failed", job.id)

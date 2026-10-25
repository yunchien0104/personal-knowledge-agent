from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError
from pypdf.generic import DictionaryObject

from personal_brain.text_normalization import normalize_for_retrieval, preserve_extracted_text


@dataclass(frozen=True, slots=True)
class PdfParseLimits:
    max_file_size_bytes: int = 20 * 1024 * 1024
    max_pages: int = 1_000
    max_page_characters: int = 1_000_000
    max_total_characters: int = 5_000_000


@dataclass(frozen=True, slots=True)
class EvidenceLocator:
    kind: str
    page_number: int
    start_offset: int
    end_offset: int
    source_hash: str


@dataclass(frozen=True, slots=True)
class ParsedPdfPage:
    page_number: int
    text: str
    retrieval_text: str
    start_offset: int
    end_offset: int
    source_hash: str
    evidence_locator: EvidenceLocator


@dataclass(frozen=True, slots=True)
class ParsedPdf:
    schema_version: int
    source_hash: str
    page_count: int
    text: str
    retrieval_text: str
    pages: tuple[ParsedPdfPage, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PdfParseError(ValueError):
    """A safe, stable parser failure suitable for a durable job error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _has_image_xobject(page: Any) -> bool:
    try:
        resources = page.get("/Resources")
        if resources is None:
            return False
        resources = resources.get_object()
        if not isinstance(resources, DictionaryObject):
            return False
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return False
        xobjects = xobjects.get_object()
        return any(
            getattr(item.get_object(), "get", lambda _key: None)("/Subtype") == "/Image"
            for item in xobjects.values()
        )
    except (AttributeError, KeyError, TypeError, ValueError, PdfReadError):
        return False


def _resource_error(message: str) -> PdfParseError:
    return PdfParseError("resource_limit_exceeded", message)


def parse_native_pdf(data: bytes, limits: PdfParseLimits | None = None) -> ParsedPdf:
    """Extract selectable text without OCR and return page-traceable offsets."""

    active_limits = limits or PdfParseLimits()
    if not isinstance(data, bytes):
        raise TypeError("PDF input must be bytes")
    if not data:
        raise PdfParseError("corrupt_pdf", "PDF is empty or unreadable")
    if len(data) > active_limits.max_file_size_bytes:
        raise _resource_error("PDF exceeds the configured file-size limit")

    source_hash = sha256(data).hexdigest()
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise PdfParseError("encrypted_pdf", "Encrypted PDFs are not supported")
        page_count = len(reader.pages)
    except PdfParseError:
        raise
    except (EOFError, FileNotDecryptedError, PdfReadError, TypeError, ValueError) as error:
        raise PdfParseError("corrupt_pdf", "PDF is corrupt or unreadable") from error

    if page_count == 0:
        raise PdfParseError("blank_pdf", "PDF has no pages")
    if page_count > active_limits.max_pages:
        raise _resource_error("PDF exceeds the configured page limit")

    extracted_pages: list[str] = []
    contains_images = False
    total_characters = 0
    try:
        for page in reader.pages:
            contains_images = contains_images or _has_image_xobject(page)
            text = preserve_extracted_text(page.extract_text() or "")
            if len(text) > active_limits.max_page_characters:
                raise _resource_error("A PDF page exceeds the configured text limit")
            total_characters += len(text)
            if total_characters > active_limits.max_total_characters:
                raise _resource_error("PDF exceeds the configured total text limit")
            extracted_pages.append(text)
    except PdfParseError:
        raise
    except FileNotDecryptedError as error:
        raise PdfParseError("encrypted_pdf", "Encrypted PDFs are not supported") from error
    except (EOFError, PdfReadError, TypeError, ValueError) as error:
        raise PdfParseError("corrupt_pdf", "PDF is corrupt or unreadable") from error
    except NotImplementedError as error:
        raise PdfParseError("unsupported_pdf", "PDF uses an unsupported encoding or filter") from error

    if not any(extracted_pages):
        if contains_images:
            raise PdfParseError("scanned_pdf", "PDF contains no selectable text; OCR is not enabled")
        raise PdfParseError("blank_pdf", "PDF contains no selectable text")

    full_text = "\n\n".join(extracted_pages)
    parsed_pages: list[ParsedPdfPage] = []
    cursor = 0
    for index, text in enumerate(extracted_pages):
        if index:
            cursor += 2
        start_offset = cursor
        end_offset = start_offset + len(text)
        locator = EvidenceLocator(
            kind="pdf_page",
            page_number=index + 1,
            start_offset=start_offset,
            end_offset=end_offset,
            source_hash=source_hash,
        )
        parsed_pages.append(
            ParsedPdfPage(
                page_number=index + 1,
                text=text,
                retrieval_text=normalize_for_retrieval(text),
                start_offset=start_offset,
                end_offset=end_offset,
                source_hash=source_hash,
                evidence_locator=locator,
            )
        )
        cursor = end_offset

    return ParsedPdf(
        schema_version=2,
        source_hash=source_hash,
        page_count=page_count,
        text=full_text,
        retrieval_text="\n\n".join(normalize_for_retrieval(text) for text in extracted_pages),
        pages=tuple(parsed_pages),
    )

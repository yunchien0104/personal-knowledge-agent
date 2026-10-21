from hashlib import sha256
from pathlib import Path
import unittest

from personal_brain.pdf_parser import PdfParseError, PdfParseLimits, parse_native_pdf


FIXTURES = Path(__file__).parent / "fixtures"


class NativePdfParserTests(unittest.TestCase):
    def test_extracts_text_with_page_offsets_and_evidence_locators(self) -> None:
        source = (FIXTURES / "native-text.pdf").read_bytes()
        result = parse_native_pdf(source)

        self.assertEqual(result.page_count, 2)
        self.assertEqual([page.page_number for page in result.pages], [1, 2])
        self.assertEqual(result.source_hash, sha256(source).hexdigest())
        self.assertIn("First page native text.", result.pages[0].text)
        self.assertIn("Second page evidence.", result.pages[1].text)
        for page in result.pages:
            self.assertEqual(result.text[page.start_offset : page.end_offset], page.text)
            self.assertEqual(page.evidence_locator.kind, "pdf_page")
            self.assertEqual(page.evidence_locator.page_number, page.page_number)
            self.assertEqual(page.evidence_locator.source_hash, result.source_hash)

    def test_classifies_scanned_pdf_without_inventing_text(self) -> None:
        self.assert_parse_error("scanned-image-only.pdf", "scanned_pdf")

    def test_classifies_blank_pdf(self) -> None:
        self.assert_parse_error("blank.pdf", "blank_pdf")

    def test_rejects_encrypted_pdf(self) -> None:
        self.assert_parse_error("encrypted.pdf", "encrypted_pdf")

    def test_rejects_corrupt_pdf(self) -> None:
        self.assert_parse_error("corrupt.pdf", "corrupt_pdf")

    def test_enforces_file_and_page_limits(self) -> None:
        source = (FIXTURES / "native-text.pdf").read_bytes()
        with self.assertRaises(PdfParseError) as file_error:
            parse_native_pdf(source, PdfParseLimits(max_file_size_bytes=len(source) - 1))
        self.assertEqual(file_error.exception.code, "resource_limit_exceeded")

        with self.assertRaises(PdfParseError) as page_error:
            parse_native_pdf(source, PdfParseLimits(max_pages=1))
        self.assertEqual(page_error.exception.code, "resource_limit_exceeded")

    def assert_parse_error(self, fixture_name: str, expected_code: str) -> None:
        with self.assertRaises(PdfParseError) as caught:
            parse_native_pdf((FIXTURES / fixture_name).read_bytes())
        self.assertEqual(caught.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()

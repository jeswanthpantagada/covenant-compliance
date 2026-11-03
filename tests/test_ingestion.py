"""
Comprehensive Test Suite for Document Ingestion and Source Verification.
Tests both real SEC EDGAR documents and synthetic test documents.
"""

import json
import pytest
from pathlib import Path
from pypdf import PdfWriter

from src.utils.hashing import compute_file_sha256, verify_file_integrity
from src.core.ingestion import (
    DocumentIngestionService,
    DocumentBlock,
    ParsedDocument
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACTUAL_DATA_DIR = PROJECT_ROOT / "data" / "actual_agreements"
SOURCES_MANIFEST = ACTUAL_DATA_DIR / "sources.json"


@pytest.fixture(scope="module")
def sources_data():
    """Loads the verified sources manifest."""
    assert SOURCES_MANIFEST.is_file(), f"Manifest missing at {SOURCES_MANIFEST}"
    with open(SOURCES_MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def ingestion_service():
    """Initializes the document ingestion service."""
    return DocumentIngestionService()


def test_sources_manifest_integrity(sources_data):
    """
    Verifies that all 8 SEC documents in the manifest exist on disk
    and their SHA-256 checksums match exactly without corruption.
    """
    assert len(sources_data) == 8, f"Expected 8 documents in manifest, got {len(sources_data)}"

    for doc in sources_data:
        file_path = PROJECT_ROOT / doc["relative_path"]
        assert file_path.is_file(), f"File does not exist: {file_path}"

        expected_hash = doc["sha256"]
        actual_hash = compute_file_sha256(file_path)
        assert actual_hash == expected_hash, (
            f"Checksum mismatch for {doc['doc_id']} ({doc['filename']}):\n"
            f"  Expected: {expected_hash}\n"
            f"  Actual:   {actual_hash}"
        )
        assert verify_file_integrity(file_path, expected_hash) is True


def test_html_ingestion_preserves_provenance(ingestion_service, sources_data):
    """
    Verifies that HTML documents:
    1. Do NOT invent fake page numbers (page_number is strictly None).
    2. Preserve section and paragraph references.
    3. Treat paragraph IDs as internal reference identifiers, not official legal paragraph numbers.
    4. Produce valid, auditable citations.
    """
    redwire_meta = next(d for d in sources_data if d["doc_id"] == "redwire_credit_agreement_2020")
    file_path = PROJECT_ROOT / redwire_meta["relative_path"]

    parsed = ingestion_service.ingest_document(
        file_path=file_path,
        doc_id=redwire_meta["doc_id"],
        expected_sha256=redwire_meta["sha256"],
        additional_metadata=redwire_meta
    )

    assert isinstance(parsed, ParsedDocument)
    assert len(parsed.blocks) > 500, "Should extract hundreds of structural blocks"

    # Rule check: HTML documents must NEVER have page numbers invented
    html_page_numbers = [b.page_number for b in parsed.blocks if b.page_number is not None]
    assert len(html_page_numbers) == 0, "HTML documents must have page_number=None (no invented page numbers)"

    # Rule check: Sections and paragraphs must be preserved
    blocks_with_sections = [b for b in parsed.blocks if b.section_reference]
    assert len(blocks_with_sections) > 100, "Expected many blocks with active section references"

    # Citation check: verify InternalRef label is used
    sample_block = blocks_with_sections[0]
    citation = sample_block.citation
    assert "Doc: redwire_credit_agreement_2020" in citation
    assert "Section" in citation
    assert "InternalRef: internal_block_" in citation


def test_redwire_definitions_and_covenant(ingestion_service, sources_data):
    """
    Verifies extraction of 'Consolidated EBITDA' (full definition text & 25% cap)
    and Section 7.11 (Total Net Leverage Ratio covenant) in Redwire.
    """
    doc_meta = next(d for d in sources_data if d["doc_id"] == "redwire_credit_agreement_2020")
    file_path = PROJECT_ROOT / doc_meta["relative_path"]

    parsed = ingestion_service.ingest_document(
        file_path=file_path,
        doc_id=doc_meta["doc_id"],
        expected_sha256=doc_meta["sha256"]
    )

    # 1. Look up Consolidated EBITDA definition lead block & complete text
    ebitda_block = parsed.find_definition("Consolidated EBITDA")
    assert ebitda_block is not None, "Consolidated EBITDA definition must be found"
    assert "Consolidated Net Income" in ebitda_block.text

    ebitda_full = parsed.get_definition_text("Consolidated EBITDA")
    assert "25%" in ebitda_full, "Verified 25% aggregate cap must be in Consolidated EBITDA definition"
    assert "interest expense" in ebitda_full.lower()

    # 2. Look up Section 7.11 (Financial Covenant)
    s711_blocks = parsed.find_blocks_by_section("Section 7.11")
    assert len(s711_blocks) > 0, "Section 7.11 blocks must be extracted"
    s711_text = " ".join(b.text for b in s711_blocks)
    assert "Consolidated Total Net Leverage Ratio" in s711_text
    assert "5.00" in s711_text


def test_whole_earth_definitions_and_covenant(ingestion_service, sources_data):
    """
    Verifies extraction of 'Consolidated EBITDA' and Section 7.8 in Whole Earth Brands.
    """
    doc_meta = next(d for d in sources_data if d["doc_id"] == "whole_earth_ar_credit_agreement_2021")
    file_path = PROJECT_ROOT / doc_meta["relative_path"]

    parsed = ingestion_service.ingest_document(
        file_path=file_path,
        doc_id=doc_meta["doc_id"],
        expected_sha256=doc_meta["sha256"]
    )

    # Check Section 7.8 Financial Covenants
    s78_blocks = parsed.find_blocks_by_section("Section 7.8")
    assert len(s78_blocks) > 0, "Section 7.8 blocks must be found in Whole Earth"
    s78_text = " ".join(b.text for b in s78_blocks)
    assert "Consolidated Total Net Leverage Ratio" in s78_text
    assert "37,500,000" in s78_text, "Net Debt cash netting cap of $37.5M must be present"


def test_mercury_systems_definitions_and_covenant(ingestion_service, sources_data):
    """
    Verifies extraction of 'Consolidated EBITDA' and Section 8.11 in Mercury Systems conformed agreement.
    """
    doc_meta = next(d for d in sources_data if d["doc_id"] == "mercury_systems_amendment_6_conformed_2024")
    file_path = PROJECT_ROOT / doc_meta["relative_path"]

    parsed = ingestion_service.ingest_document(
        file_path=file_path,
        doc_id=doc_meta["doc_id"],
        expected_sha256=doc_meta["sha256"]
    )

    # Check Section 8.11 Financial Covenants
    s811_blocks = parsed.find_blocks_by_section("Section 8.11")
    assert len(s811_blocks) > 0, "Section 8.11 blocks must be found in Mercury Systems"
    s811_text = " ".join(b.text for b in s811_blocks)
    assert "Consolidated Total Net Leverage Ratio" in s811_text
    assert "5.25" in s811_text or "4.50" in s811_text


def test_tamper_detection_raises_error(ingestion_service, sources_data):
    """Verifies that tampering with expected hash raises ValueError immediately."""
    doc_meta = sources_data[0]
    file_path = PROJECT_ROOT / doc_meta["relative_path"]

    fake_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(ValueError, match="Integrity check failed"):
        ingestion_service.ingest_document(
            file_path=file_path,
            doc_id="tampered_doc",
            expected_sha256=fake_sha256
        )


def test_pdf_ingestion_preserves_page_numbers(ingestion_service, tmp_path):
    """
    Synthesizes a minimal multi-page test PDF to verify that the PDF parser
    strictly populates 1-based page numbers.
    """
    pdf_path = tmp_path / "sample_agreement.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    parsed = ingestion_service.ingest_document(
        file_path=pdf_path,
        doc_id="synthetic_pdf_test"
    )

    assert parsed.metadata.file_type == "pdf"
    for block in parsed.blocks:
        assert block.page_number in [1, 2]
        assert "InternalRef: p" in block.citation

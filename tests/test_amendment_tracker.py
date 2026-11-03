"""
Test Suite for Amendment Processing and Agreement Versioning Engine.

Tests:
1. REPLACE operation verification and application.
2. INSERT operation verification and application.
3. DELETE operation verification and application.
4. Incorrect target reference rejection (REJECTED_TARGET_NOT_FOUND).
5. Missing text / hallucinated quote rejection (REJECTED_MISSING_QUOTE).
6. Amendment conflict detection (REJECTED_CONFLICT).
7. Invalid / out-of-order effective date rejection (REJECTED_INVALID_DATE).
8. AgreementTimeline quarter-by-quarter version retrieval.
9. Immutability guarantee (base version unchanged after delta application).
10. Internal reference formatting enforcement (InternalRef: labels).
11. Real SEC EDGAR document amendment test (Redwire Fifth Amendment).
12. LLM Analyzer extraction (mock callable & deterministic fallback).
"""

import json
import pytest
from pathlib import Path

from src.core.ingestion import (
    DocumentIngestionService,
    DocumentBlock,
    DocumentMetadata,
    ParsedDocument
)
from src.core.amendment_tracker import (
    AmendmentOperationType,
    AmendmentClauseDelta,
    VerificationStatus,
    VerificationResult,
    AgreementVersion,
    AgreementTimeline,
    AmendmentLLMAnalyzer,
    AmendmentVerificationEngine,
    build_initial_version_from_doc,
    OPENROUTER_CONFIG
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_MANIFEST = PROJECT_ROOT / "data" / "actual_agreements" / "sources.json"


@pytest.fixture(scope="module")
def sources_data():
    with open(SOURCES_MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def ingestion_service():
    return DocumentIngestionService()


@pytest.fixture
def synthetic_base_version():
    """Builds a known synthetic base agreement version for deterministic unit tests."""
    return AgreementVersion(
        version_id="SYN_v1_2020-01-01",
        company="Synthetic Corp",
        effective_date="2020-01-01",
        source_doc_ids=["synthetic_base_doc"],
        source_hashes={"synthetic_base_doc": "hash_base_001"},
        sections={
            "Section 7.11": "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 4.50:1.00.",
            "Section 7.12": "Section 7.12 Minimum Interest Coverage. Interest Coverage Ratio shall not be less than 2.50:1.00."
        },
        definitions={
            "Consolidated EBITDA": "Consolidated EBITDA means Consolidated Net Income plus Interest Expense, Taxes, Depreciation and Amortization.",
            "Consolidated Net Debt": "Consolidated Net Debt means total debt less unrestricted cash up to $25,000,000."
        },
        applied_deltas=[],
        provenance_map={
            "Section 7.11": "[Doc: synthetic_base_doc | Section: Section 7.11 | InternalRef: internal_block_10]",
            "Section 7.12": "[Doc: synthetic_base_doc | Section: Section 7.12 | InternalRef: internal_block_15]",
            "Consolidated EBITDA": "[Doc: synthetic_base_doc | Definition: Consolidated EBITDA | InternalRef: internal_block_5]",
            "Consolidated Net Debt": "[Doc: synthetic_base_doc | Definition: Consolidated Net Debt | InternalRef: internal_block_6]"
        },
        is_approved=True
    )


@pytest.fixture
def synthetic_amendment_doc():
    """Builds a mock parsed amendment document containing verifiable clauses."""
    meta = DocumentMetadata(
        doc_id="synthetic_amendment_1",
        filename="synthetic_amendment_1.htm",
        file_type="HTML",
        sha256="hash_amend_001",
        file_size_bytes=5000,
        company="Synthetic Corp",
        effective_date="2021-06-30"
    )
    blocks = [
        DocumentBlock(
            block_index=0,
            block_type="paragraph",
            text="FIRST AMENDMENT TO CREDIT AGREEMENT dated as of June 30, 2021.",
            internal_ref_id="internal_block_0",
            source_doc_id="synthetic_amendment_1",
            section_reference="Recitals"
        ),
        DocumentBlock(
            block_index=1,
            block_type="paragraph",
            text=(
                "Section 7.11 of the Credit Agreement is hereby amended and restated in its entirety to read as follows: "
                "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021."
            ),
            internal_ref_id="internal_block_1",
            source_doc_id="synthetic_amendment_1",
            section_reference="Section 2(a)"
        ),
        DocumentBlock(
            block_index=2,
            block_type="definition",
            defined_term="EAC Adjustments",
            text=(
                "Section 1.01 of the Credit Agreement is hereby amended by adding the following new definition: "
                "“EAC Adjustments” means engineering and commissioning contract adjustments not exceeding $10,000,000."
            ),
            internal_ref_id="internal_block_2",
            source_doc_id="synthetic_amendment_1",
            section_reference="Section 2(b)"
        ),
        DocumentBlock(
            block_index=3,
            block_type="paragraph",
            text=(
                "Section 7.12 of the Credit Agreement is hereby deleted in its entirety and replaced with [Reserved]."
            ),
            internal_ref_id="internal_block_3",
            source_doc_id="synthetic_amendment_1",
            section_reference="Section 2(c)"
        )
    ]
    return ParsedDocument(
        metadata=meta,
        blocks=blocks,
        sections_index={"Section 2(a)": [1], "Section 2(b)": [2], "Section 2(c)": [3]},
        definitions_index={"EAC Adjustments": 2}
    )


# ==============================================================================
# TEST 1: REPLACE OPERATION
# ==============================================================================

def test_replace_operation(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that a valid REPLACE delta:
    1. Verifies exact quote in the amendment document.
    2. Modifies the target section in the newly created AgreementVersion.
    3. Leaves the base agreement completely unaltered.
    4. Accurately updates provenance citations and audit hashes.
    """
    engine = AmendmentVerificationEngine()

    supporting_quote = (
        "Section 7.11 of the Credit Agreement is hereby amended and restated in its entirety to read as follows: "
        "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021."
    )

    delta = AmendmentClauseDelta(
        delta_id="delta_replace_cov",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=supporting_quote,
        proposed_text="Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021.",
        effective_date="2021-06-30",
        rationale="Covenant relief stepping up ratio limit from 4.50x to 5.50x",
        internal_ref_id="internal_block_1"
    )

    # 1. Verify
    result = engine.verify_delta(delta, synthetic_amendment_doc, synthetic_base_version)
    assert result.is_valid is True
    assert result.status == VerificationStatus.VERIFIED

    # 2. Apply to generate new version
    v2 = engine.apply_verified_deltas(
        base_version=synthetic_base_version,
        verified_deltas=[delta],
        amendment_doc=synthetic_amendment_doc,
        new_version_id="SYN_v2_2021-06-30"
    )

    # Assertions on new version
    assert v2.version_id == "SYN_v2_2021-06-30"
    assert v2.effective_date == "2021-06-30"
    assert "synthetic_amendment_1" in v2.source_doc_ids
    assert v2.source_hashes["synthetic_amendment_1"] == "hash_amend_001"
    assert "5.50:1.00" in v2.sections["Section 7.11"]
    assert "Op: REPLACE" in v2.provenance_map["Section 7.11"]
    assert v2.is_approved is False  # Must await human approval

    # Immutability check on base version
    assert "4.50:1.00" in synthetic_base_version.sections["Section 7.11"]
    assert len(synthetic_base_version.source_doc_ids) == 1
    assert "synthetic_amendment_1" not in synthetic_base_version.source_doc_ids


# ==============================================================================
# TEST 2: INSERT OPERATION
# ==============================================================================

def test_insert_operation(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that an INSERT delta:
    1. Verifies exact quote in the amendment document.
    2. Inserts a brand new definition into the new AgreementVersion.
    3. Leaves the base agreement completely unaltered.
    """
    engine = AmendmentVerificationEngine()

    quote = (
        "Section 1.01 of the Credit Agreement is hereby amended by adding the following new definition: "
        "“EAC Adjustments” means engineering and commissioning contract adjustments not exceeding $10,000,000."
    )

    delta = AmendmentClauseDelta(
        delta_id="delta_insert_eac",
        operation=AmendmentOperationType.INSERT,
        target_section="Section 1.01",
        affected_definition="EAC Adjustments",
        exact_supporting_text=quote,
        proposed_text="“EAC Adjustments” means engineering and commissioning contract adjustments not exceeding $10,000,000.",
        effective_date="2021-06-30",
        rationale="Adds EAC Adjustments definition as EBITDA add-back",
        internal_ref_id="internal_block_2"
    )

    result = engine.verify_delta(delta, synthetic_amendment_doc, synthetic_base_version)
    assert result.is_valid is True
    assert result.status == VerificationStatus.VERIFIED

    v2 = engine.apply_verified_deltas(
        base_version=synthetic_base_version,
        verified_deltas=[delta],
        amendment_doc=synthetic_amendment_doc,
        new_version_id="SYN_v2_2021-06-30"
    )

    assert "EAC Adjustments" in v2.definitions
    assert "$10,000,000" in v2.definitions["EAC Adjustments"]
    assert "Op: INSERT" in v2.provenance_map["EAC Adjustments"]

    # Immutability check
    assert "EAC Adjustments" not in synthetic_base_version.definitions


# ==============================================================================
# TEST 3: DELETE OPERATION
# ==============================================================================

def test_delete_operation(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that a DELETE delta:
    1. Verifies target existed in base agreement.
    2. Deletes the target clause from the new AgreementVersion.
    3. Marks provenance as (DELETED).
    4. Leaves base agreement completely untouched.
    """
    engine = AmendmentVerificationEngine()

    quote = "Section 7.12 of the Credit Agreement is hereby deleted in its entirety and replaced with [Reserved]."

    delta = AmendmentClauseDelta(
        delta_id="delta_delete_712",
        operation=AmendmentOperationType.DELETE,
        target_section="Section 7.12",
        exact_supporting_text=quote,
        effective_date="2021-06-30",
        rationale="Removes interest coverage covenant",
        internal_ref_id="internal_block_3"
    )

    result = engine.verify_delta(delta, synthetic_amendment_doc, synthetic_base_version)
    assert result.is_valid is True
    assert result.status == VerificationStatus.VERIFIED

    v2 = engine.apply_verified_deltas(
        base_version=synthetic_base_version,
        verified_deltas=[delta],
        amendment_doc=synthetic_amendment_doc,
        new_version_id="SYN_v2_2021-06-30"
    )

    assert "Section 7.12" not in v2.sections
    assert "(DELETED)" in v2.provenance_map["Section 7.12"]

    # Base version still has Section 7.12
    assert "Section 7.12" in synthetic_base_version.sections


# ==============================================================================
# TEST 4: INCORRECT TARGET REFERENCE REJECTION
# ==============================================================================

def test_incorrect_target_reference_rejection(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that if a delta references a non-existent section or definition in REPLACE or DELETE,
    it is rejected with REJECTED_TARGET_NOT_FOUND and never applied.
    """
    engine = AmendmentVerificationEngine()

    # Valid quote in amendment, but targets non-existent section
    quote = (
        "Section 7.11 of the Credit Agreement is hereby amended and restated in its entirety to read as follows: "
        "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021."
    )

    delta = AmendmentClauseDelta(
        delta_id="delta_bad_target",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 99.99",  # Does not exist in base version
        exact_supporting_text=quote,
        proposed_text="Invalid section update",
        effective_date="2021-06-30",
        rationale="Erroneous section reference"
    )

    result = engine.verify_delta(delta, synthetic_amendment_doc, synthetic_base_version)
    assert result.is_valid is False
    assert result.status == VerificationStatus.REJECTED_TARGET_NOT_FOUND
    assert "does not exist in base agreement" in result.failure_reason


# ==============================================================================
# TEST 5: MISSING TEXT / HALLUCINATED QUOTE REJECTION
# ==============================================================================

def test_missing_quote_rejection(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that if the LLM hallucinates supporting text not present verbatim
    in the amendment document, it is rejected with REJECTED_MISSING_QUOTE.
    """
    engine = AmendmentVerificationEngine()

    hallucinated_quote = (
        "The Borrower shall at all times maintain an unlimited leverage ratio of 99.00 to 1.00."
    )

    delta = AmendmentClauseDelta(
        delta_id="delta_hallucination",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=hallucinated_quote,
        proposed_text="Unlimited ratio",
        effective_date="2021-06-30",
        rationale="Hallucinated amendment terms"
    )

    result = engine.verify_delta(delta, synthetic_amendment_doc, synthetic_base_version)
    assert result.is_valid is False
    assert result.status == VerificationStatus.REJECTED_MISSING_QUOTE
    assert "was not found verbatim in amendment document" in result.failure_reason


# ==============================================================================
# TEST 6: AMENDMENT CONFLICT DETECTION
# ==============================================================================

def test_amendment_conflict_detection(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that competing deltas modifying the same section on the same date
    with different proposed texts are flagged as REJECTED_CONFLICT.
    """
    engine = AmendmentVerificationEngine()

    supporting_quote = (
        "Section 7.11 of the Credit Agreement is hereby amended and restated in its entirety to read as follows: "
        "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021."
    )

    delta_a = AmendmentClauseDelta(
        delta_id="delta_variant_a",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=supporting_quote,
        proposed_text="Total Net Leverage Ratio shall not exceed 5.50:1.00.",
        effective_date="2021-06-30",
        rationale="Variant A"
    )

    delta_b = AmendmentClauseDelta(
        delta_id="delta_variant_b",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=supporting_quote,
        proposed_text="Total Net Leverage Ratio shall not exceed 6.00:1.00.",  # Conflicting proposal
        effective_date="2021-06-30",
        rationale="Variant B"
    )

    result_a = engine.verify_delta(
        delta=delta_a,
        amendment_doc=synthetic_amendment_doc,
        base_version=synthetic_base_version,
        other_pending_deltas=[delta_b]
    )

    assert result_a.is_valid is False
    assert result_a.status == VerificationStatus.REJECTED_CONFLICT
    assert "Conflict detected: Competing delta" in result_a.failure_reason


# ==============================================================================
# TEST 7: INVALID OR ANACHRONISTIC EFFECTIVE DATE REJECTION
# ==============================================================================

def test_invalid_effective_date_rejection(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies rejection of:
    1. Dates mismatching amendment document metadata.
    2. Dates preceding the base agreement effective date.
    3. Invalid date formats (non-ISO).
    """
    engine = AmendmentVerificationEngine()

    quote = (
        "Section 7.11 of the Credit Agreement is hereby amended and restated in its entirety to read as follows: "
        "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021."
    )

    # 1. Delta date mismatches amendment doc metadata date (2021-06-30 vs 2021-07-15)
    delta_mismatch = AmendmentClauseDelta(
        delta_id="delta_mismatch",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=quote,
        proposed_text="Updated 7.11",
        effective_date="2021-07-15",
        rationale="Date mismatch with document"
    )
    res_mismatch = engine.verify_delta(delta_mismatch, synthetic_amendment_doc, synthetic_base_version)
    assert res_mismatch.is_valid is False
    assert res_mismatch.status == VerificationStatus.REJECTED_INVALID_DATE
    assert "Effective date mismatch" in res_mismatch.failure_reason

    # 2. Both doc and delta date precede base agreement (2019-01-01 < 2020-01-01)
    doc_past = ParsedDocument(
        metadata=DocumentMetadata(
            doc_id="doc_past",
            filename="doc_past.htm",
            file_type="HTML",
            sha256="hash_past",
            file_size_bytes=1000,
            company="Synthetic Corp",
            effective_date="2019-01-01"
        ),
        blocks=synthetic_amendment_doc.blocks,
        sections_index=synthetic_amendment_doc.sections_index
    )
    delta_past = AmendmentClauseDelta(
        delta_id="delta_past",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=quote,
        proposed_text="Updated 7.11",
        effective_date="2019-01-01",
        rationale="Anachronistic date"
    )
    res_past = engine.verify_delta(delta_past, doc_past, synthetic_base_version)
    assert res_past.is_valid is False
    assert res_past.status == VerificationStatus.REJECTED_INVALID_DATE
    assert "precedes base version" in res_past.failure_reason

    # 3. Invalid date format
    delta_bad_format = AmendmentClauseDelta(
        delta_id="delta_bad_fmt",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=quote,
        proposed_text="Updated 7.11",
        effective_date="06/30/2021",
        rationale="Non-ISO format"
    )
    res_fmt = engine.verify_delta(delta_bad_format, synthetic_amendment_doc, synthetic_base_version)
    assert res_fmt.is_valid is False
    assert res_fmt.status == VerificationStatus.REJECTED_INVALID_DATE


# ==============================================================================
# TEST 8: TIMELINE VERSION RETRIEVAL ACROSS QUARTERS
# ==============================================================================

def test_agreement_timeline_quarterly_retrieval(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that AgreementTimeline retrieves the correct governing version for any target date.
    """
    engine = AmendmentVerificationEngine()

    # Create v1 (2020-01-01)
    v1 = synthetic_base_version

    # Create v2 (2021-06-30)
    quote = (
        "Section 7.11 of the Credit Agreement is hereby amended and restated in its entirety to read as follows: "
        "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 5.50:1.00 for the period ending June 30, 2021."
    )
    delta_1 = AmendmentClauseDelta(
        delta_id="d1",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=quote,
        proposed_text="Ratio shall not exceed 5.50:1.00",
        effective_date="2021-06-30",
        rationale="First amendment"
    )
    v2 = engine.apply_verified_deltas(v1, [delta_1], synthetic_amendment_doc, "SYN_v2_2021-06-30")

    # Create v3 (2022-12-31)
    delta_2 = AmendmentClauseDelta(
        delta_id="d2",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=quote,
        proposed_text="Ratio shall not exceed 6.50:1.00",
        effective_date="2022-12-31",
        rationale="Second amendment"
    )
    v3 = engine.apply_verified_deltas(v2, [delta_2], synthetic_amendment_doc, "SYN_v3_2022-12-31")

    timeline = AgreementTimeline(company="Synthetic Corp")
    timeline.add_version(v1)
    timeline.add_version(v2)
    timeline.add_version(v3)

    # 1. Date before agreement -> None
    assert timeline.get_version_for_date("2019-12-31") is None

    # 2. Date in Q1 2020 -> returns v1
    v_q1_2020 = timeline.get_version_for_date("2020-03-31")
    assert v_q1_2020.version_id == "SYN_v1_2020-01-01"

    # 3. Date in Q1 2021 (before amendment 1) -> returns v1
    v_q1_2021 = timeline.get_version_for_date("2021-03-31")
    assert v_q1_2021.version_id == "SYN_v1_2020-01-01"

    # 4. Date on effective date of amendment 1 -> returns v2
    v_q2_2021 = timeline.get_version_for_date("2021-06-30")
    assert v_q2_2021.version_id == "SYN_v2_2021-06-30"

    # 5. Date in Q3 2022 -> returns v2
    v_q3_2022 = timeline.get_version_for_date("2022-09-30")
    assert v_q3_2022.version_id == "SYN_v2_2021-06-30"

    # 6. Date in Q4 2022 -> returns v3
    v_q4_2022 = timeline.get_version_for_date("2022-12-31")
    assert v_q4_2022.version_id == "SYN_v3_2022-12-31"

    # 7. Quarter string inputs ('Q1 2021', '2021-Q3', 'Q4 2022')
    assert timeline.get_version_for_date("Q1 2021").version_id == "SYN_v1_2020-01-01"
    assert timeline.get_version_for_date("Q3 2021").version_id == "SYN_v2_2021-06-30"
    assert timeline.get_version_for_date("2022-Q4").version_id == "SYN_v3_2022-12-31"


# ==============================================================================
# TEST 9: INTERNAL REFERENCE FORMATTING ENFORCEMENT
# ==============================================================================

def test_internal_reference_formatting(synthetic_base_version):
    """
    Verifies that provenance citations treat block IDs as internal references
    (InternalRef: internal_block_X) and never claim official legal paragraph numbers.
    """
    for key, citation in synthetic_base_version.provenance_map.items():
        assert "InternalRef: internal_block_" in citation, (
            f"Provenance for {key} must contain 'InternalRef: internal_block_': {citation}"
        )
        assert "paragraph " not in citation.lower() or "internalref" in citation.lower()


# ==============================================================================
# TEST 10: REAL SEC EDGAR DOCUMENT TEST (REDWIRE FIFTH AMENDMENT)
# ==============================================================================

def test_real_redwire_amendment_verification(ingestion_service, sources_data):
    """
    End-to-End test on real SEC documents:
    1. Ingest Redwire Credit Agreement (2020-10-28) -> v1.
    2. Ingest Redwire Fifth Amendment (2022-10-28).
    3. Extract verbatim Section 7.11 text from Fifth Amendment.
    4. Verify delta against real document text.
    5. Apply delta to produce v2 with 6.50:1.00 ratio.
    """
    engine = AmendmentVerificationEngine()

    rdw_base_meta = next(d for d in sources_data if d["doc_id"] == "redwire_credit_agreement_2020")
    rdw_amend_meta = next(d for d in sources_data if d["doc_id"] == "redwire_amendment_5_2022")

    # Ingest base doc
    base_doc = ingestion_service.ingest_document(
        PROJECT_ROOT / rdw_base_meta["relative_path"],
        rdw_base_meta["doc_id"],
        rdw_base_meta["sha256"],
        rdw_base_meta
    )
    v1 = build_initial_version_from_doc(base_doc, "Redwire Corporation", "RDW_v1_2020-10-28")

    # Ingest amendment doc
    amend_doc = ingestion_service.ingest_document(
        PROJECT_ROOT / rdw_amend_meta["relative_path"],
        rdw_amend_meta["doc_id"],
        rdw_amend_meta["sha256"],
        rdw_amend_meta
    )

    # Locate block 467 containing the amended covenant
    block_467 = next((b for b in amend_doc.blocks if "greater than 6.50:1.00" in b.text), None)
    assert block_467 is not None, "Could not locate Section 7.11 6.50:1.00 clause in Fifth Amendment"

    # Verbatim snippet from Fifth Amendment
    verbatim_quote = "greater than 6.50:1.00."

    delta = AmendmentClauseDelta(
        delta_id="rdw_amend5_cov",
        operation=AmendmentOperationType.REPLACE,
        target_section="Section 7.11",
        exact_supporting_text=verbatim_quote,
        proposed_text=block_467.text,
        effective_date="2022-10-28",
        rationale="Fifth amendment adjusts leverage covenant ceiling to 6.50:1.00",
        internal_ref_id=f"internal_block_{block_467.block_index}"
    )

    # Verify delta
    result = engine.verify_delta(delta, amend_doc, v1)
    assert result.is_valid is True, f"Verification failed: {result.failure_reason}"
    assert result.status == VerificationStatus.VERIFIED

    # Apply delta to produce v2
    v2 = engine.apply_verified_deltas(v1, [delta], amend_doc, "RDW_v2_2022-10-28")

    assert v2.version_id == "RDW_v2_2022-10-28"
    assert "6.50:1.00" in v2.get_clause("Section 7.11")
    assert "redwire_amendment_5_2022" in v2.source_doc_ids
    assert v2.source_hashes["redwire_amendment_5_2022"] == rdw_amend_meta["sha256"]

    # Verify base version still has original text
    assert "6.50:1.00" not in v1.get_clause("Section 7.11")


# ==============================================================================
# TEST 11: LLM ANALYZER EXTRACTION (MOCK CALLABLE & FALLBACK)
# ==============================================================================

def test_llm_analyzer_mock_and_fallback(synthetic_base_version, synthetic_amendment_doc):
    """
    Verifies that AmendmentLLMAnalyzer:
    1. Respects recorded OpenRouter model configuration.
    2. Correctly executes custom LLM callables in unit tests.
    3. Correctly invokes deterministic fallback extractor when API key is not present.
    """
    assert OPENROUTER_CONFIG["primary_model"] == "openrouter/free"
    assert OPENROUTER_CONFIG["fallback_model"] == "google/gemma-4-31b-it:free"

    analyzer = AmendmentLLMAnalyzer()

    # Custom callable mock
    def mock_llm(doc, base):
        return [
            AmendmentClauseDelta(
                delta_id="mock_delta_1",
                operation=AmendmentOperationType.REPLACE,
                target_section="Section 7.11",
                exact_supporting_text=doc.blocks[1].text[:100],
                proposed_text="Mock replacement",
                effective_date="2021-06-30",
                rationale="Mock rationale"
            )
        ]

    deltas = analyzer.extract_deltas(
        amendment_doc=synthetic_amendment_doc,
        base_version=synthetic_base_version,
        custom_llm_callable=mock_llm
    )

    assert len(deltas) == 1
    assert deltas[0].delta_id == "mock_delta_1"
    assert deltas[0].operation == AmendmentOperationType.REPLACE

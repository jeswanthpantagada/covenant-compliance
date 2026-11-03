"""
Test Suite for Covenant Dependency Graph Builder.

Tests:
1. Recursive reference traversal starting from Consolidated EBITDA, Net Debt, and covenant sections.
2. Circularity detection (identifying circular post-addback EBITDA caps).
3. Detection and human-review flagging of missing/unresolved definitions.
4. Pruning of non-financial boilerplate terms (Person, Business Day, etc.).
5. Real SEC EDGAR agreement dependency graphs (Redwire and Whole Earth Brands).
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
    AgreementVersion,
    build_initial_version_from_doc
)
from src.core.dependency_graph import (
    DependencyGraphBuilder,
    DependencyGraph,
    DependencyNode,
    CircularityInfo
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
def synthetic_agreement_version():
    """Builds a controlled synthetic AgreementVersion with explicit cycles and missing terms."""
    return AgreementVersion(
        version_id="SYN_v1_TEST",
        company="Synthetic Test Corp",
        effective_date="2021-01-01",
        source_doc_ids=["synthetic_doc_1"],
        source_hashes={"synthetic_doc_1": "dummy_hash_123"},
        sections={
            "Section 7.11": "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 4.50:1.00 as of the end of any Test Period.",
            "Section 6.02": "Section 6.02 Financial Statements. Deliver Compliance Certificate within 45 days."
        },
        definitions={
            "Total Net Leverage Ratio": "Total Net Leverage Ratio means Consolidated Net Debt divided by Consolidated EBITDA for such Test Period.",
            "Consolidated Net Debt": "Consolidated Net Debt means Consolidated Total Indebtedness of any Person less unrestricted cash on any Business Day up to $25,000,000.",
            "Consolidated Total Indebtedness": "Consolidated Total Indebtedness means all outstanding borrowings under Term Loans and Revolving Credit.",
            "Consolidated EBITDA": (
                "Consolidated EBITDA means Consolidated Net Income plus Interest Expense, Taxes, "
                "plus Restructuring Charges; provided that Restructuring Charges shall not exceed 20% of Consolidated EBITDA."
            ),
            "Consolidated Net Income": "Consolidated Net Income means net income determined in accordance with GAAP.",
            "Interest Expense": "Interest Expense means total cash interest expense.",
            "Taxes": "Taxes means all income taxes.",
            "Restructuring Charges": "Restructuring Charges means severance and facility closure costs, capped by Consolidated EBITDA.",
            "Person": "Person means any natural person, corporation, limited liability company, trust, or entity.",
            "Business Day": "Business Day means any day other than a Saturday, Sunday, or legal holiday."
        },
        provenance_map={
            "Section 7.11": "[Doc: synthetic_doc_1 | Section 7.11 | InternalRef: internal_block_1]",
            "Section 6.02": "[Doc: synthetic_doc_1 | Section 6.02 | InternalRef: internal_block_2]",
            "Total Net Leverage Ratio": "[Doc: synthetic_doc_1 | Definition: Total Net Leverage Ratio | InternalRef: internal_block_3]",
            "Consolidated Net Debt": "[Doc: synthetic_doc_1 | Definition: Consolidated Net Debt | InternalRef: internal_block_4]",
            "Consolidated Total Indebtedness": "[Doc: synthetic_doc_1 | Definition: Consolidated Total Indebtedness | InternalRef: internal_block_5]",
            "Consolidated EBITDA": "[Doc: synthetic_doc_1 | Definition: Consolidated EBITDA | InternalRef: internal_block_6]",
            "Consolidated Net Income": "[Doc: synthetic_doc_1 | Definition: Consolidated Net Income | InternalRef: internal_block_7]",
            "Interest Expense": "[Doc: synthetic_doc_1 | Definition: Interest Expense | InternalRef: internal_block_8]",
            "Taxes": "[Doc: synthetic_doc_1 | Definition: Taxes | InternalRef: internal_block_9]",
            "Restructuring Charges": "[Doc: synthetic_doc_1 | Definition: Restructuring Charges | InternalRef: internal_block_10]",
            "Person": "[Doc: synthetic_doc_1 | Definition: Person | InternalRef: internal_block_11]",
            "Business Day": "[Doc: synthetic_doc_1 | Definition: Business Day | InternalRef: internal_block_12]"
        },
        is_approved=True
    )


# ==============================================================================
# TEST 1: RECURSIVE TRAVERSAL & SEED ROOTS
# ==============================================================================

def test_recursive_seed_traversal(synthetic_agreement_version):
    """
    Verifies that DependencyGraphBuilder discovers roots (Total Net Leverage Ratio,
    Consolidated EBITDA, Consolidated Net Debt, Section 7.11) and recursively traces
    their dependent definitions (Consolidated Net Income, Interest Expense, etc.).
    """
    builder = DependencyGraphBuilder(max_depth=3)
    graph = builder.build_graph(synthetic_agreement_version)

    assert isinstance(graph, DependencyGraph)
    assert "Total Net Leverage Ratio" in graph.nodes
    assert "Consolidated EBITDA" in graph.nodes
    assert "Consolidated Net Debt" in graph.nodes
    assert "Section 7.11" in graph.nodes
    assert "Consolidated Net Income" in graph.nodes
    assert "Interest Expense" in graph.nodes
    assert "Consolidated Total Indebtedness" in graph.nodes

    # Check edges
    assert ("Total Net Leverage Ratio", "Consolidated Net Debt") in graph.edges or \
           ("Total Net Leverage Ratio", "Consolidated EBITDA") in graph.edges


# ==============================================================================
# TEST 2: CIRCULARITY DETECTION (EBITDA CIRCULAR CAP)
# ==============================================================================

def test_circularity_detection_ebitda_cap(synthetic_agreement_version):
    """
    Verifies that the graph builder detects circular dependencies between
    Consolidated EBITDA and Restructuring Charges (capped at % of Consolidated EBITDA),
    classifies it as 'circular_cap', supplies the algebraic solution, and flags for review.
    """
    builder = DependencyGraphBuilder(max_depth=3)
    graph = builder.build_graph(synthetic_agreement_version)

    assert len(graph.cycles) > 0, "Expected circularity to be detected"
    ebitda_cycles = [c for c in graph.cycles if c.cycle_type == "circular_cap"]
    assert len(ebitda_cycles) > 0, "Expected circular_cap classification"

    cap_cycle = ebitda_cycles[0]
    assert "Consolidated EBITDA" in cap_cycle.cycle
    assert cap_cycle.mathematical_solution is not None
    assert "Capped Add-back" in cap_cycle.mathematical_solution
    assert cap_cycle.flagged_for_human_review is True


# ==============================================================================
# TEST 3: UNRESOLVED / MISSING TERMS DETECTION
# ==============================================================================

def test_unresolved_terms_detection():
    """
    Verifies that if an agreement references a term that is not defined in Section 1.01
    (e.g., 'Consolidated Senior Indebtedness' or 'Section 9.99'), it is placed into
    unresolved_terms and graph.needs_human_review is set to True.
    """
    version_with_missing_term = AgreementVersion(
        version_id="MISSING_TERM_TEST",
        company="Missing Term Corp",
        effective_date="2021-01-01",
        source_doc_ids=["doc_missing"],
        source_hashes={"doc_missing": "hash_missing"},
        sections={
            "Section 7.11": "Section 7.11 Financial Covenant. Total Net Leverage Ratio subject to Section 9.99."
        },
        definitions={
            "Total Net Leverage Ratio": "Total Net Leverage Ratio means Consolidated Net Debt divided by Consolidated EBITDA.",
            "Consolidated EBITDA": "Consolidated EBITDA means Consolidated Net Income plus Interest Expense.",
            "Consolidated Net Debt": "Consolidated Net Debt means Consolidated Senior Indebtedness less cash."
            # Note: 'Consolidated Senior Indebtedness' and 'Section 9.99' are NOT defined!
        },
        is_approved=True
    )

    builder = DependencyGraphBuilder(max_depth=3)
    graph = builder.build_graph(version_with_missing_term)

    assert graph.needs_human_review is True
    assert len(graph.unresolved_terms) > 0
    assert any("Section 9.99" in u for u in graph.unresolved_terms) or \
           any("Senior Indebtedness" in u for u in graph.unresolved_terms)
    assert any("Missing or unresolved definitions" in r for r in graph.review_reasons)


# ==============================================================================
# TEST 4: PRUNING OF NON-FINANCIAL BOILERPLATE
# ==============================================================================

def test_pruning_boilerplate_terms(synthetic_agreement_version):
    """
    Verifies that non-financial legal boilerplate terms like 'Person' and 'Business Day'
    are pruned and not added as core nodes in the financial graph.
    """
    builder = DependencyGraphBuilder(max_depth=3)
    graph = builder.build_graph(synthetic_agreement_version)

    assert "Person" not in graph.nodes
    assert "Business Day" not in graph.nodes
    assert any("person" in p.lower() for p in graph.pruned_terms) or \
           any("business day" in p.lower() for p in graph.pruned_terms)


# ==============================================================================
# TEST 5: REAL SEC AGREEMENT TEST (REDWIRE CORPORATION)
# ==============================================================================

def test_real_redwire_dependency_graph(ingestion_service, sources_data):
    """
    Verifies that the graph builder successfully extracts the real Redwire
    definitions graph, discovering Section 7.11, Consolidated EBITDA,
    Consolidated Total Net Debt, and circular add-back caps.
    """
    rdw_meta = next(d for d in sources_data if d["doc_id"] == "redwire_credit_agreement_2020")
    file_path = PROJECT_ROOT / rdw_meta["relative_path"]

    doc = ingestion_service.ingest_document(
        file_path, rdw_meta["doc_id"], rdw_meta["sha256"], rdw_meta
    )
    v1 = build_initial_version_from_doc(doc, "Redwire Corporation", "RDW_v1_TEST")

    builder = DependencyGraphBuilder(max_depth=2)
    graph = builder.build_graph(v1)

    assert "Consolidated EBITDA" in graph.nodes
    assert "Section 7.11" in graph.nodes
    assert "Consolidated Total Net Debt" in graph.nodes
    assert "Consolidated Total Net Leverage Ratio" in graph.nodes

    # Verifies that circular EBITDA cycles are detected
    has_ebitda_cycle = any(c.cycle_type == "circular_cap" for c in graph.cycles)
    assert has_ebitda_cycle is True


# ==============================================================================
# TEST 6: REAL SEC AGREEMENT TEST (WHOLE EARTH BRANDS)
# ==============================================================================

def test_real_whole_earth_dependency_graph(ingestion_service, sources_data):
    """
    Verifies that Whole Earth Brands graph identifies Section 7.8,
    Consolidated EBITDA, and Consolidated Total Net Debt (with $37.5M netting cap).
    """
    we_meta = next(d for d in sources_data if d["doc_id"] == "whole_earth_ar_credit_agreement_2021")
    file_path = PROJECT_ROOT / we_meta["relative_path"]

    doc = ingestion_service.ingest_document(
        file_path, we_meta["doc_id"], we_meta["sha256"], we_meta
    )
    v1 = build_initial_version_from_doc(doc, "Whole Earth Brands, Inc.", "FREE_v1_TEST")

    builder = DependencyGraphBuilder(max_depth=2)
    graph = builder.build_graph(v1)

    assert "Consolidated EBITDA" in graph.nodes
    assert "Section 7.8" in graph.nodes
    assert "Total Debt" in graph.nodes or "Consolidated Total Net Debt" in graph.nodes
    assert "Consolidated Total Net Leverage Ratio" in graph.nodes

    ebitda_text = graph.nodes["Consolidated EBITDA"].clause_text
    assert "10,000,000" in ebitda_text or "12.5" in ebitda_text

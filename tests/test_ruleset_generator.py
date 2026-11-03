"""
Test Suite for Rule Set Generator and Verification Engine.

Tests:
1. RuleSet schema creation and Pydantic validation.
2. Verbatim clause verification against governing AgreementVersion.
3. Detection and rejection of hallucinated / unverified clauses.
4. Mathematical boundary validation (cap percentage, dollar bounds, ratio ceiling).
5. Version ID and source document hash linkage checks.
6. Ambiguity preservation (storing alternative interpretations).
7. Human reviewer approval gate (is_approved sign-off).
8. Real SEC EDGAR Rule Sets (Redwire, Whole Earth Brands, Mercury Systems).
9. OpenRouter LLM Generator (custom callable & config check).
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
    build_initial_version_from_doc,
    OPENROUTER_CONFIG
)
from src.core.dependency_graph import DependencyGraphBuilder
from src.core.ruleset_generator import (
    RuleSetGenerator,
    RuleSetVerificationEngine,
    RuleSet,
    RuleSetCap,
    CapType,
    AddBackRule,
    AddBackDirection,
    NetDebtRule,
    CovenantRule,
    CovenantScheduleTier,
    InterpretationAlternative
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
def synthetic_version_and_ruleset():
    """Builds a verified synthetic AgreementVersion and corresponding RuleSet."""
    v1 = AgreementVersion(
        version_id="SYN_v1_RULESET_TEST",
        company="Synthetic Test Corp",
        effective_date="2021-01-01",
        source_doc_ids=["synthetic_base_doc"],
        source_hashes={"synthetic_base_doc": "verified_hash_999"},
        sections={
            "Section 7.11": "Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 4.50:1.00 as of the end of any Test Period."
        },
        definitions={
            "Consolidated EBITDA": (
                "Consolidated EBITDA means Consolidated Net Income plus Interest Expense, Taxes, "
                "plus Restructuring Charges; provided that Restructuring Charges shall not exceed 25% of Consolidated EBITDA."
            ),
            "Consolidated Net Debt": "Consolidated Net Debt means total debt less unrestricted cash up to $25,000,000."
        },
        provenance_map={
            "Section 7.11": "[Doc: synthetic_base_doc | Section 7.11 | InternalRef: internal_block_1]",
            "Consolidated EBITDA": "[Doc: synthetic_base_doc | Definition: Consolidated EBITDA | InternalRef: internal_block_2]",
            "Consolidated Net Debt": "[Doc: synthetic_base_doc | Definition: Consolidated Net Debt | InternalRef: internal_block_3]"
        },
        is_approved=True
    )

    ruleset = RuleSet(
        ruleset_id="ruleset_SYN_v1",
        version_id="SYN_v1_RULESET_TEST",
        company="Synthetic Test Corp",
        source_hashes={"synthetic_base_doc": "verified_hash_999"},
        created_at="2021-01-01T00:00:00Z",
        starting_metric="Consolidated Net Income",
        add_backs=[
            AddBackRule(
                rule_id="syn_interest",
                category="Interest Expense",
                description="Cash interest expense",
                direction=AddBackDirection.ADDITION,
                is_subject_to_cap=False,
                verbatim_clause="Interest Expense",
                citation="[Doc: synthetic_base_doc | Definition: Consolidated EBITDA | InternalRef: internal_block_2]"
            ),
            AddBackRule(
                rule_id="syn_restructuring",
                category="Restructuring Charges",
                description="Restructuring costs capped at 25% of EBITDA",
                direction=AddBackDirection.ADDITION,
                is_subject_to_cap=True,
                cap=RuleSetCap(
                    cap_id="syn_cap_25",
                    cap_type=CapType.PERCENT_OF_EBITDA_POST,
                    percent_limit=0.25,
                    basis_metric="POST_ADDBACK_EBITDA",
                    exact_clause="Restructuring Charges shall not exceed 25% of Consolidated EBITDA",
                    citation="[Doc: synthetic_base_doc | Definition: Consolidated EBITDA | InternalRef: internal_block_2]"
                ),
                verbatim_clause="Restructuring Charges shall not exceed 25% of Consolidated EBITDA",
                citation="[Doc: synthetic_base_doc | Definition: Consolidated EBITDA | InternalRef: internal_block_2]"
            )
        ],
        deductions=[],
        net_debt_rule=NetDebtRule(
            debt_components=["total debt"],
            excluded_debt=[],
            cash_netting_type="CAPPED_CASH_NETTING",
            cash_cap_amount=25000000.0,
            cash_conditions=["unrestricted cash up to $25,000,000"],
            verbatim_clause="Consolidated Net Debt means total debt less unrestricted cash up to $25,000,000.",
            citation="[Doc: synthetic_base_doc | Definition: Consolidated Net Debt | InternalRef: internal_block_3]"
        ),
        covenant_rule=CovenantRule(
            covenant_name="Total Net Leverage Ratio",
            governing_section="Section 7.11",
            metric_formula="Consolidated Net Debt / LTM EBITDA",
            testing_frequency="Quarterly",
            schedule=[
                CovenantScheduleTier(
                    tier_id="tier_1",
                    period_description="Shall not exceed 4.50:1.00",
                    ratio_ceiling=4.50
                )
            ],
            verbatim_clause="Section 7.11 Financial Covenant. Total Net Leverage Ratio shall not exceed 4.50:1.00",
            citation="[Doc: synthetic_base_doc | Section 7.11 | InternalRef: internal_block_1]"
        ),
        interpretations=[],
        human_review_items=[],
        is_approved=False
    )

    return v1, ruleset


# ==============================================================================
# TEST 1: VERBATIM CLAUSE VERIFICATION PASSES
# ==============================================================================

def test_verbatim_verification_passes(synthetic_version_and_ruleset):
    """
    Verifies that when all quoted clauses exist verbatim in the agreement version,
    RuleSetVerificationEngine returns is_valid=True with zero unverified clauses.
    """
    v1, ruleset = synthetic_version_and_ruleset
    engine = RuleSetVerificationEngine()
    result = engine.verify_ruleset(ruleset, v1)

    assert result.is_valid is True
    assert len(result.unverified_clauses) == 0
    assert len(result.math_errors) == 0
    assert len(result.hash_mismatches) == 0


# ==============================================================================
# TEST 2: HALLUCINATED CLAUSE REJECTION
# ==============================================================================

def test_hallucinated_clause_rejection(synthetic_version_and_ruleset):
    """
    Verifies that if an add-back contains a clause that was hallucinated or altered,
    verification fails (is_valid=False) and the bad clause is recorded in unverified_clauses.
    """
    v1, ruleset = synthetic_version_and_ruleset
    # Inject hallucinated quote
    ruleset.add_backs[0].verbatim_clause = "All expenses whatsoever shall be added back without limitation or condition"

    engine = RuleSetVerificationEngine()
    result = engine.verify_ruleset(ruleset, v1)

    assert result.is_valid is False
    assert len(result.unverified_clauses) > 0
    assert any("Interest Expense" in u for u in result.unverified_clauses)


# ==============================================================================
# TEST 3: MATHEMATICAL BOUNDARY VALIDATION
# ==============================================================================

def test_mathematical_boundary_validation(synthetic_version_and_ruleset):
    """
    Verifies detection of invalid numerical boundaries:
    - percent_limit > 1.0 (e.g. 1.25)
    - negative dollar limits
    - negative cash netting cap
    - negative or zero ratio ceilings
    """
    v1, ruleset = synthetic_version_and_ruleset

    # 1. Invalid percent limit > 1.0
    ruleset.add_backs[1].cap.percent_limit = 1.25
    engine = RuleSetVerificationEngine()
    res1 = engine.verify_ruleset(ruleset, v1)
    assert res1.is_valid is False
    assert any("percent_limit" in e for e in res1.math_errors)

    # 2. Negative dollar cap
    ruleset.add_backs[1].cap.percent_limit = 0.25
    ruleset.add_backs[1].cap.dollar_limit = -500000.0
    res2 = engine.verify_ruleset(ruleset, v1)
    assert res2.is_valid is False
    assert any("dollar_limit" in e for e in res2.math_errors)

    # 3. Negative cash netting cap
    ruleset.add_backs[1].cap.dollar_limit = None
    ruleset.net_debt_rule.cash_cap_amount = -1000.0
    res3 = engine.verify_ruleset(ruleset, v1)
    assert res3.is_valid is False
    assert any("cash_cap_amount" in e for e in res3.math_errors)

    # 4. Zero ratio ceiling
    ruleset.net_debt_rule.cash_cap_amount = 25000000.0
    ruleset.covenant_rule.schedule[0].ratio_ceiling = -4.50
    res4 = engine.verify_ruleset(ruleset, v1)
    assert res4.is_valid is False
    assert any("ratio_ceiling" in e for e in res4.math_errors)


# ==============================================================================
# TEST 4: VERSION ID AND HASH MISMATCH
# ==============================================================================

def test_version_id_and_hash_mismatch(synthetic_version_and_ruleset):
    """
    Verifies that a RuleSet linked to a different version ID or tampered
    source hash is rejected with hash_mismatches.
    """
    v1, ruleset = synthetic_version_and_ruleset
    engine = RuleSetVerificationEngine()

    # Mismatched version_id
    ruleset.version_id = "WRONG_VERSION_ID"
    res1 = engine.verify_ruleset(ruleset, v1)
    assert res1.is_valid is False
    assert any("Version ID mismatch" in m for m in res1.hash_mismatches)

    # Mismatched hash
    ruleset.version_id = v1.version_id
    ruleset.source_hashes["synthetic_base_doc"] = "tampered_hash_abc"
    res2 = engine.verify_ruleset(ruleset, v1)
    assert res2.is_valid is False
    assert any("Hash mismatch" in m for m in res2.hash_mismatches)


# ==============================================================================
# TEST 5: AMBIGUITY PRESERVATION & ALTERNATIVE READINGS
# ==============================================================================

def test_ambiguity_preservation(synthetic_version_and_ruleset):
    """
    Verifies that when wording is ambiguous, the RuleSet preserves both interpretations
    in interpretations and flags for human review instead of picking one unilaterally.
    """
    v1, ruleset = synthetic_version_and_ruleset
    interp = InterpretationAlternative(
        ambiguity_id="interp_circ_cap",
        clause_reference="Restructuring cap",
        issue_description="Cap states 25% of EBITDA after giving effect to add-backs",
        interpretation_a={"method": "closed_form", "limit": "0.3333 * base"},
        interpretation_b={"method": "fixed_point_iteration"},
        recommended_reading="interpretation_a"
    )
    ruleset.interpretations.append(interp)

    engine = RuleSetVerificationEngine()
    res = engine.verify_ruleset(ruleset, v1)

    assert any("ambiguous interpretations" in f for f in res.human_review_flags)
    assert len(ruleset.interpretations) == 1
    assert ruleset.interpretations[0].ambiguity_id == "interp_circ_cap"


# ==============================================================================
# TEST 6: HUMAN REVIEW GATE
# ==============================================================================

def test_human_review_approval_gate(synthetic_version_and_ruleset):
    """
    Verifies that synthesized Rule Sets start unapproved (is_approved=False).
    Only upon explicit human sign-off via approve() does is_approved become True,
    recording the reviewer name and timestamp.
    """
    v1, ruleset = synthetic_version_and_ruleset
    assert ruleset.is_approved is False
    assert ruleset.approved_by is None
    assert ruleset.approval_timestamp is None

    # Sign off
    ruleset.approve("Senior Credit Risk Reviewer")
    assert ruleset.is_approved is True
    assert ruleset.approved_by == "Senior Credit Risk Reviewer"
    assert ruleset.approval_timestamp is not None


# ==============================================================================
# TEST 7: REAL SEC EDGAR AGREEMENT - REDWIRE CORP
# ==============================================================================

def test_real_redwire_ruleset_synthesis(ingestion_service, sources_data):
    """
    End-to-End test on real Redwire Corporation Credit Agreement:
    1. Traces definitions via DependencyGraph.
    2. Synthesizes RuleSet with 25% post-EBITDA circular cap.
    3. Verifies RuleSet against source document.
    """
    meta = next(d for d in sources_data if d["doc_id"] == "redwire_credit_agreement_2020")
    file_path = PROJECT_ROOT / meta["relative_path"]

    doc = ingestion_service.ingest_document(file_path, meta["doc_id"], meta["sha256"], meta)
    v1 = build_initial_version_from_doc(doc, meta["company"], "RDW_v1_REAL")

    builder = DependencyGraphBuilder(max_depth=2)
    graph = builder.build_graph(v1)

    generator = RuleSetGenerator()
    ruleset = generator.generate_ruleset(graph, v1)

    verifier = RuleSetVerificationEngine()
    res = verifier.verify_ruleset(ruleset, v1)

    assert res.is_valid is True, f"Redwire verification failed: {res.unverified_clauses}"
    assert ruleset.company == "Redwire Corporation"
    assert ruleset.covenant_rule.schedule[0].ratio_ceiling == 5.00
    assert ruleset.net_debt_rule.cash_netting_type == "UNCAPPED_CASH_NETTING"

    # Verify 25% circular cap is captured across clauses (a)(iv) and (a)(vii)
    restruct = next(ab for ab in ruleset.add_backs if ab.category == "Restructuring Charges")
    assert restruct.is_subject_to_cap is True
    assert restruct.cap.cap_type == CapType.PERCENT_OF_EBITDA_POST
    assert restruct.cap.percent_limit == 0.25

    # Verify clause (a)(iv) Extraordinary Charges is captured and shares the same aggregate cap
    extraordinary = next(ab for ab in ruleset.add_backs if ab.category == "Extraordinary Charges")
    assert extraordinary.is_subject_to_cap is True
    assert extraordinary.cap.cap_id == "rdw_shared_aggregate_25_post_cap"

    # Verify clause (a)(vii)(B) Synergies is captured and shares the same aggregate cap
    synergies = next(ab for ab in ruleset.add_backs if ab.category == "Synergies")
    assert synergies.is_subject_to_cap is True
    assert synergies.cap.cap_id == "rdw_shared_aggregate_25_post_cap"

    # Verify Section 1.11(c)(ii) Pro Forma Synergies is captured and shares the same aggregate cap
    proforma = next(ab for ab in ruleset.add_backs if ab.category == "Pro Forma Synergies")
    assert proforma.is_subject_to_cap is True
    assert proforma.cap.cap_id == "rdw_shared_aggregate_25_post_cap"

    # Verify aggregate properties
    assert restruct.cap.cap_id == extraordinary.cap.cap_id == synergies.cap.cap_id == proforma.cap.cap_id
    assert restruct.cap.is_aggregate is True
    assert set(restruct.cap.shared_with_categories) == {
        "Extraordinary Charges", "Restructuring Charges", "Synergies", "Pro Forma Synergies"
    }

    # Verify non-cash charges addition and non-cash gains deduction
    non_cash_add = next(ab for ab in ruleset.add_backs if ab.category == "Non-Cash Charges")
    assert non_cash_add.is_subject_to_cap is False
    assert len(ruleset.deductions) >= 1
    assert ruleset.deductions[0].category == "Non-Cash Gains"


# ==============================================================================
# TEST 8: REAL SEC EDGAR AGREEMENT - WHOLE EARTH BRANDS
# ==============================================================================

def test_real_whole_earth_ruleset_synthesis(ingestion_service, sources_data):
    """
    End-to-End test on real Whole Earth Brands A&R Credit Agreement:
    1. Traces definitions via DependencyGraph.
    2. Synthesizes RuleSet with $37.5M cash netting cap and $10M/12.5% fee cap.
    3. Verifies RuleSet against source document.
    """
    meta = next(d for d in sources_data if d["doc_id"] == "whole_earth_ar_credit_agreement_2021")
    file_path = PROJECT_ROOT / meta["relative_path"]

    doc = ingestion_service.ingest_document(file_path, meta["doc_id"], meta["sha256"], meta)
    v1 = build_initial_version_from_doc(doc, meta["company"], "FREE_v1_REAL")

    builder = DependencyGraphBuilder(max_depth=2)
    graph = builder.build_graph(v1)

    generator = RuleSetGenerator()
    ruleset = generator.generate_ruleset(graph, v1)

    verifier = RuleSetVerificationEngine()
    res = verifier.verify_ruleset(ruleset, v1)

    assert res.is_valid is True, f"Whole Earth verification failed: {res.unverified_clauses}"
    assert ruleset.company == "Whole Earth Brands, Inc."
    assert ruleset.covenant_rule.schedule[0].ratio_ceiling == 5.50

    # Verify $37,500,000 cash netting cap
    assert ruleset.net_debt_rule.cash_netting_type == "CAPPED_CASH_NETTING"
    assert ruleset.net_debt_rule.cash_cap_amount == 37500000.0

    # Verify transaction fee cap
    tx_fee = next(ab for ab in ruleset.add_backs if ab.category == "Transaction Expenses")
    assert tx_fee.cap.cap_type == CapType.GREATER_OF
    assert tx_fee.cap.dollar_limit == 10000000.0
    assert tx_fee.cap.percent_limit == 0.125


# ==============================================================================
# TEST 9: REAL SEC EDGAR AGREEMENT - MERCURY SYSTEMS
# ==============================================================================

def test_real_mercury_systems_ruleset_synthesis(ingestion_service, sources_data):
    """
    End-to-End test on real Mercury Systems Credit Agreement:
    1. Traces definitions via DependencyGraph.
    2. Synthesizes RuleSet with 5.25:1.00 ceiling and acquisition holiday.
    3. Verifies RuleSet against source document.
    """
    meta = next(d for d in sources_data if d["doc_id"] == "mercury_systems_credit_agreement_2016")
    file_path = PROJECT_ROOT / meta["relative_path"]

    doc = ingestion_service.ingest_document(file_path, meta["doc_id"], meta["sha256"], meta)
    v1 = build_initial_version_from_doc(doc, meta["company"], "MRCY_v1_REAL")

    builder = DependencyGraphBuilder(max_depth=2)
    graph = builder.build_graph(v1)

    generator = RuleSetGenerator()
    ruleset = generator.generate_ruleset(graph, v1)

    verifier = RuleSetVerificationEngine()
    res = verifier.verify_ruleset(ruleset, v1)

    assert res.is_valid is True, f"Mercury verification failed: {res.unverified_clauses}"
    assert ruleset.company == "Mercury Systems, Inc."
    assert ruleset.covenant_rule.schedule[0].ratio_ceiling == 5.25
    assert ruleset.covenant_rule.acquisition_holiday is not None


# ==============================================================================
# TEST 10: OPENROUTER LLM SYNTHESIS & CALLABLE MOCK
# ==============================================================================

def test_ruleset_generator_mock_callable(synthetic_version_and_ruleset):
    """
    Verifies that RuleSetGenerator supports custom LLM callables and adheres
    to recorded OpenRouter configuration parameters.
    """
    assert OPENROUTER_CONFIG["primary_model"] == "openrouter/free"
    assert OPENROUTER_CONFIG["temperature"] == 0.0

    v1, expected_ruleset = synthetic_version_and_ruleset
    builder = DependencyGraphBuilder(max_depth=1)
    graph = builder.build_graph(v1)

    def mock_llm_synthesizer(g, v):
        return expected_ruleset

    generator = RuleSetGenerator()
    generated = generator.generate_ruleset(graph, v1, custom_llm_callable=mock_llm_synthesizer)

    assert generated.ruleset_id == expected_ruleset.ruleset_id
    assert generated.starting_metric == "Consolidated Net Income"
    assert len(generated.add_backs) == len(expected_ruleset.add_backs)

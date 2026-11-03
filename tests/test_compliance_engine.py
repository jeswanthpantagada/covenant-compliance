"""
Comprehensive Test Suite for Phase 2: Compliance Calculation Engine.

Tests all 12 synthetic test cases:
1. Comfortably compliant (Pass under all readings).
2. Direct covenant breach (Fail under all readings).
3. Capped add-back haircut.
4. Circular post-addback EBITDA cap exact algebraic resolution.
5. Contractual cash netting cap ($37.5M ceiling).
6. Unallocated aggregate cap overrun (human review flag).
7. Outcome depends on a reading (borderline interpretation).
8. Negative EBITDA scenario (flags for review, avoids default).
9. Zero EBITDA scenario (avoids ZeroDivisionError).
10. Missing approvals gate block (Cannot compute).
11. Ineligible add-back rejection (routine capex disguised).
12. Unit multiplier scaling ($000s).
"""

import json
from pathlib import Path
import pytest

from src.core.ingestion import DocumentIngestionService
from src.core.amendment_tracker import build_initial_version_from_doc
from src.core.dependency_graph import DependencyGraphBuilder
from src.core.ruleset_generator import RuleSetGenerator
from src.compliance.financial_data import (
    QuarterlyFinancialPackage,
    IncomeStatement,
    BalanceSheet,
    ClaimedAddBack
)
from src.compliance.synthetic_test_generator import build_synthetic_suite
from src.compliance.account_mapper import AccountMapper
from src.compliance.addback_auditor import AddBackAuditor, AddBackEligibilityStatus
from src.compliance.calculation_engine import (
    ComplianceCalculationEngine,
    ComplianceStatus,
    ComplianceWorkpaper
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_MANIFEST = PROJECT_ROOT / "data" / "actual_agreements" / "sources.json"


@pytest.fixture(scope="module")
def synthetic_suite():
    return build_synthetic_suite()


@pytest.fixture(scope="module")
def mapper():
    return AccountMapper()


@pytest.fixture(scope="module")
def auditor():
    return AddBackAuditor()


@pytest.fixture(scope="module")
def engine():
    return ComplianceCalculationEngine()


# ==============================================================================
# TEST CASES 1 TO 12
# ==============================================================================

def test_case_01_pass_comfortably(synthetic_suite, mapper, auditor, engine):
    """Case 1: Healthy compliance, ratio ~1.04x vs 4.50x covenant."""
    c = synthetic_suite[0]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.PASS_UNDER_ALL_READINGS
    assert wp.summary_metrics["Is_Compliant"] is True
    assert wp.summary_metrics["Actual_Total_Net_Leverage_Ratio"] < 2.0
    assert wp.summary_metrics["Ratio_Headroom"] > 2.0
    assert wp.summary_metrics["Absolute_EBITDA_Buffer"] > 0
    assert wp.summary_metrics["EBITDA_Cushion_Pct"] > 50.0


def test_case_02_breach_high_debt(synthetic_suite, mapper, auditor, engine):
    """Case 2: Heavy debt load leading to failure under all readings."""
    c = synthetic_suite[1]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.FAIL_UNDER_ALL_READINGS
    assert wp.summary_metrics["Is_Compliant"] is False
    assert wp.summary_metrics["Actual_Total_Net_Leverage_Ratio"] > 7.0
    assert wp.summary_metrics["Ratio_Headroom"] < 0
    assert wp.summary_metrics["Absolute_EBITDA_Buffer"] < 0


def test_case_03_capped_addback_haircut(synthetic_suite, mapper, auditor, engine):
    """Case 3: $25M claimed restructuring is subjected to deterministic haircut."""
    c = synthetic_suite[2]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert len(wp.cap_breakdowns) > 0
    cap_detail = wp.cap_breakdowns[0]
    assert cap_detail.eligible_claimed == 25.0e6
    assert cap_detail.allowed_amount < 25.0e6
    assert cap_detail.haircut_amount > 0


def test_case_04_circular_post_ebitda_cap_exactness(synthetic_suite, mapper, auditor, engine):
    """
    Case 4: Tests exact algebraic solution:
    Baseline = $30M, Cap = 25% post-circular = (0.25 / 0.75) * 30M = exactly $10,000,000.
    """
    c = synthetic_suite[3]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    cap_detail = wp.cap_breakdowns[0]
    assert round(cap_detail.computed_limit, 2) == 10000000.00
    assert round(cap_detail.allowed_amount, 2) == 10000000.00
    assert round(cap_detail.haircut_amount, 2) == 5000000.00


def test_case_05_contractual_cash_netting_cap(synthetic_suite, mapper, auditor, engine):
    """
    Case 5: Borrower holds $60M cash, but rule set enforces $37.5M cap.
    Net debt must reflect exactly $37.5M reduction, leaving $22.5M unnetted.
    """
    c = synthetic_suite[4]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.summary_metrics["Unrestricted_Cash"] == 60.0e6
    assert wp.summary_metrics["Cash_Netted"] == 37.5e6
    # Total debt 200M - 37.5M = 162.5M
    assert wp.summary_metrics["Consolidated_Net_Debt"] == 162.5e6


def test_case_06_unallocated_aggregate_cap_overrun(synthetic_suite, mapper, auditor, engine):
    """
    Case 6: Two add-backs (Restructuring $5M + Synergies $5M) exceed aggregate limit.
    Flags for human review without arbitrarily choosing which category to cut,
    returning Cannot compute until human approval/allocation is completed.
    """
    c = synthetic_suite[5]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any(c.is_aggregate_exceeded_without_waterfall for c in wp.cap_breakdowns)
    assert any("Aggregate add-back cap was exceeded" in f for f in wp.human_review_flags)


def test_case_07_outcome_depends_on_reading(synthetic_suite, mapper, auditor, engine):
    """
    Case 7: Borderline ratio passes under Reading A (4.500x <= 4.55x)
    but fails under Reading B (4.615x > 4.55x).
    Status must be 'Outcome depends on a reading'.
    """
    c = synthetic_suite[6]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.OUTCOME_DEPENDS_ON_A_READING
    assert "Primary" in wp.readings_details
    assert "Alternative" in wp.readings_details


def test_case_08_negative_ebitda_handling(synthetic_suite, mapper, auditor, engine):
    """
    Case 8: Negative EBITDA does not cause ZeroDivisionError and does NOT
    declare default automatically; flags for human review and sets Cannot compute.
    """
    c = synthetic_suite[7]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any("LTM Consolidated EBITDA is non-positive" in f for f in wp.human_review_flags)


def test_case_09_zero_ebitda_handling(synthetic_suite, mapper, auditor, engine):
    """Case 9: Zero EBITDA handled safely without ZeroDivisionError."""
    c = synthetic_suite[8]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any("LTM Consolidated EBITDA is non-positive" in f for f in wp.human_review_flags)


def test_case_10_missing_approvals_cannot_compute(synthetic_suite, mapper, auditor, engine):
    """
    Case 10: Verifies that all 3 Human Approval Gates strictly block execution:
    - Gate 1: Unapproved RuleSet -> CANNOT_COMPUTE
    - Gate 2: Unapproved AccountMappingPlan -> CANNOT_COMPUTE
    - Gate 3: Unapproved AddBackAuditReport -> CANNOT_COMPUTE
    Calculation proceeds only after all 3 gates are signed off by a human reviewer.
    """
    c = synthetic_suite[9]
    pkg, rs = c["package"], c["ruleset"]
    assert rs.is_approved is False

    plan = mapper.suggest_mappings(pkg, rs)
    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)

    # Gate 1 Block
    wp1 = engine.calculate_compliance(pkg, rs, plan, audit)
    assert wp1.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any("RuleSet has not received human reviewer approval" in n for n in wp1.audit_notes)

    # Approve Gate 1
    rs.approve("Risk Reviewer")

    # Gate 2 Block
    wp2 = engine.calculate_compliance(pkg, rs, plan, audit)
    assert wp2.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any("Account mapping plan has not received human reviewer approval" in n for n in wp2.audit_notes)

    # Approve Gate 2
    plan.approve("Risk Reviewer")

    # Gate 3 Block
    wp3 = engine.calculate_compliance(pkg, rs, plan, audit)
    assert wp3.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any("Add-back audit report has not received human reviewer approval" in n for n in wp3.audit_notes)

    # Approve Gate 3 -> All 3 gates approved
    audit.approve("Risk Reviewer")
    wp4 = engine.calculate_compliance(pkg, rs, plan, audit)
    assert wp4.overall_status in [ComplianceStatus.PASS_UNDER_ALL_READINGS, ComplianceStatus.FAIL_UNDER_ALL_READINGS]


def test_case_11_ineligible_addback_rejection(synthetic_suite, mapper, auditor, engine):
    """
    Case 11: Capex disguised as restructuring add-back is audited as INELIGIBLE
    and given zero allowed amount.
    """
    c = synthetic_suite[10]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    assert len(audit.audited_items) == 1
    assert audit.audited_items[0].status == AddBackEligibilityStatus.INELIGIBLE
    assert audit.audited_items[0].eligible_amount == 0.0

    audit.approve("Senior Reviewer")
    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.overall_status == ComplianceStatus.PASS_UNDER_ALL_READINGS
    assert wp.summary_metrics["Capped_Addbacks_Allowed"] == 0.0


def test_case_12_unit_multiplier_scaling(synthetic_suite, mapper, auditor, engine):
    """
    Case 12: Financial statements reported in thousands (multiplier=1000)
    are accurately scaled to exact dollar figures.
    """
    c = synthetic_suite[11]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Senior Reviewer")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    audit.approve("Senior Reviewer")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    # Net income 10,000 * 1000 = 10,000,000
    assert wp.summary_metrics["Consolidated_Net_Income"] == 10.0e6
    # Total debt 150,000 * 1000 = 150,000,000
    assert wp.summary_metrics["Total_Funded_Debt"] == 150.0e6
    assert wp.overall_status == ComplianceStatus.PASS_UNDER_ALL_READINGS


def test_human_reviewer_override_flow(synthetic_suite, mapper, auditor, engine):
    """
    Tests human reviewer override workflow:
    Reviewer changes an add-back status and amount, re-approves, and recalculates.
    """
    c = synthetic_suite[2]
    pkg, rs = c["package"], c["ruleset"]

    plan = mapper.suggest_mappings(pkg, rs)
    plan.approve("Reviewer A")

    audit = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, rs)
    # Reviewer overrides eligible amount to 5.0M
    audit.override_item("cb3", AddBackEligibilityStatus.ELIGIBLE, 5.0e6, "Approved after audit verification")
    audit.approve("Reviewer A")

    wp = engine.calculate_compliance(pkg, rs, plan, audit)

    assert wp.cap_breakdowns[0].allowed_amount == 5.0e6


# ==============================================================================
# TEST: REDWIRE SHARED AGGREGATE 25% POST-EBITDA CAP COMPLIANCE
# ==============================================================================

def test_redwire_shared_aggregate_25_cap_compliance(mapper, auditor, engine):
    """
    Dedicated test for Redwire Corporation Credit Agreement (2020) shared aggregate 25% cap:
    Governed by Section 1.01 Consolidated EBITDA proviso:
    'all amounts added to Consolidated EBITDA pursuant to clauses (a)(iv) and (a)(vii)...
     shall not exceed, in the aggregate, 25% of Consolidated EBITDA (after giving effect to all such amounts)'

    Algebraic resolution:
    Limit = (0.25 / 0.75) * Baseline EBITDA = (1/3) * Baseline EBITDA.
    For Baseline EBITDA = $30.0M, Limit = $10.0M.

    Scenario A (Compliant within Aggregate Cap):
    - Extraordinary Charges (clause a(iv)): $3.0M
    - Restructuring Charges (clause a(vii)(A)): $4.0M
    - Synergies (clause a(vii)(B)): $2.0M
    Total claimed = $9.0M <= $10.0M limit.
    All $9.0M allowed in full without haircut.
    Overall status: PASS_UNDER_ALL_READINGS.

    Scenario B (Overrun Exceeding Aggregate Cap without Waterfall):
    - Extraordinary Charges: $5.0M
    - Restructuring Charges: $5.0M
    - Synergies: $3.0M
    Total claimed = $13.0M > $10.0M limit.
    Haircut = $3.0M.
    Because multiple categories share the aggregate cap and no contractual priority waterfall exists,
    engine marks is_aggregate_exceeded_without_waterfall = True, flags for review,
    and returns CANNOT_COMPUTE pending reviewer allocation.
    """
    with open(SOURCES_MANIFEST, "r", encoding="utf-8") as f:
        sources_data = json.load(f)

    meta = next(d for d in sources_data if d["doc_id"] == "redwire_credit_agreement_2020")
    file_path = PROJECT_ROOT / meta["relative_path"]

    ingest_svc = DocumentIngestionService()
    doc = ingest_svc.ingest_document(file_path, meta["doc_id"], meta["sha256"], meta)
    v1 = build_initial_version_from_doc(doc, meta["company"], "RDW_v1_CAP_TEST")

    graph = DependencyGraphBuilder(max_depth=2).build_graph(v1)
    ruleset = RuleSetGenerator().generate_ruleset(graph, v1)
    ruleset.approve("Senior Credit Officer")

    # Verify that clauses (a)(iv) Extraordinary, (a)(vii)(A) Restructuring, (a)(vii)(B) Synergies,
    # and Section 1.11(c)(ii) Pro Forma Synergies all share the identical aggregate cap ID
    c_ids = {
        ab.cap.cap_id
        for ab in ruleset.add_backs
        if ab.category in ["Extraordinary Charges", "Restructuring Charges", "Synergies", "Pro Forma Synergies"]
    }
    assert len(c_ids) == 1
    assert "rdw_shared_aggregate_25_post_cap" in c_ids

    # -------------------------------------------------------------
    # Scenario A: Within Shared Aggregate Limit ($9M claimed on $30M baseline)
    # -------------------------------------------------------------
    pkg_compliant = QuarterlyFinancialPackage(
        package_id="pkg_rdw_compliant_q4",
        company="Redwire Corporation",
        quarter="Q4 2020",
        period_end_date="2020-12-31",
        unit_multiplier=1.0,
        income_statement=IncomeStatement(
            revenue=100.0e6,
            operating_expenses=70.0e6,
            depreciation_amortization=3.0e6,
            interest_expense=5.0e6,
            tax_expense=2.0e6,
            net_income=20.0e6  # Baseline EBITDA = 20M + 5M + 2M + 3M = 30.0M
        ),
        balance_sheet=BalanceSheet(
            unrestricted_cash=15.0e6,
            cash_and_equivalents=15.0e6,
            term_loans=80.0e6,
            revolving_loans=10.0e6
        ),
        prior_three_quarters_ebitda=[30.0e6, 30.0e6, 30.0e6],
        claimed_add_backs=[
            ClaimedAddBack(
                addback_id="rdw_cb_extraordinary",
                category="Extraordinary Charges",
                claimed_amount=3.0e6,
                description="Settlement of non-recurring pre-acquisition legal claim",
                supporting_evidence="Signed legal settlement agreement and CFO certificate"
            ),
            ClaimedAddBack(
                addback_id="rdw_cb_restructuring",
                category="Restructuring Charges",
                claimed_amount=4.0e6,
                description="Facility consolidation and integration costs",
                supporting_evidence="Board approved restructuring plan and invoices"
            ),
            ClaimedAddBack(
                addback_id="rdw_cb_synergies",
                category="Synergies",
                claimed_amount=2.0e6,
                description="Run-rate operating expense reduction synergies",
                supporting_evidence="Responsible Officer certificate realizable within 24 months"
            )
        ]
    )

    plan_a = mapper.suggest_mappings(pkg_compliant, ruleset)
    plan_a.approve("Senior Reviewer")

    audit_a = auditor.audit_addbacks(pkg_compliant.package_id, pkg_compliant.claimed_add_backs, ruleset)
    audit_a.approve("Senior Reviewer")

    wp_a = engine.calculate_compliance(pkg_compliant, ruleset, plan_a, audit_a)

    # Assertions for Scenario A
    assert len(wp_a.cap_breakdowns) == 1
    cap_a = wp_a.cap_breakdowns[0]
    assert cap_a.cap_id == "rdw_shared_aggregate_25_post_cap"
    assert round(cap_a.computed_limit, 2) == 10000000.00  # (0.25 / 0.75) * 30M = exactly $10.0M
    assert round(cap_a.eligible_claimed, 2) == 9000000.00
    assert round(cap_a.allowed_amount, 2) == 9000000.00
    assert round(cap_a.haircut_amount, 2) == 0.00
    assert cap_a.is_aggregate_exceeded_without_waterfall is False
    assert wp_a.summary_metrics["Capped_Addbacks_Allowed"] == 9000000.00
    assert wp_a.summary_metrics["Current_Quarter_EBITDA"] == 39000000.00
    assert wp_a.summary_metrics["LTM_EBITDA"] == 129000000.00
    assert wp_a.summary_metrics["Consolidated_Net_Debt"] == 75000000.00
    assert wp_a.overall_status == ComplianceStatus.PASS_UNDER_ALL_READINGS

    # -------------------------------------------------------------
    # Scenario B: Overrun Exceeding Aggregate Limit ($13M claimed on $30M baseline)
    # -------------------------------------------------------------
    pkg_overrun = QuarterlyFinancialPackage(
        package_id="pkg_rdw_overrun_q4",
        company="Redwire Corporation",
        quarter="Q4 2020",
        period_end_date="2020-12-31",
        unit_multiplier=1.0,
        income_statement=IncomeStatement(
            revenue=100.0e6,
            operating_expenses=70.0e6,
            depreciation_amortization=3.0e6,
            interest_expense=5.0e6,
            tax_expense=2.0e6,
            net_income=20.0e6  # Baseline EBITDA = 30.0M -> Limit = 10.0M
        ),
        balance_sheet=BalanceSheet(
            unrestricted_cash=15.0e6,
            cash_and_equivalents=15.0e6,
            term_loans=80.0e6,
            revolving_loans=10.0e6
        ),
        prior_three_quarters_ebitda=[30.0e6, 30.0e6, 30.0e6],
        claimed_add_backs=[
            ClaimedAddBack(
                addback_id="rdw_cb_over_extraordinary",
                category="Extraordinary Charges",
                claimed_amount=5.0e6,
                description="Extraordinary legal and patent dispute settlements",
                supporting_evidence="Signed legal settlement agreement and CFO certificate"
            ),
            ClaimedAddBack(
                addback_id="rdw_cb_over_restructuring",
                category="Restructuring Charges",
                claimed_amount=5.0e6,
                description="Facility shutdown and integration program",
                supporting_evidence="Board approved restructuring plan and invoices"
            ),
            ClaimedAddBack(
                addback_id="rdw_cb_over_synergies",
                category="Synergies",
                claimed_amount=3.0e6,
                description="Operational improvement and cost synergies",
                supporting_evidence="Responsible Officer certificate realizable within 24 months"
            )
        ]
    )

    plan_b = mapper.suggest_mappings(pkg_overrun, ruleset)
    plan_b.approve("Senior Reviewer")

    audit_b = auditor.audit_addbacks(pkg_overrun.package_id, pkg_overrun.claimed_add_backs, ruleset)
    audit_b.approve("Senior Reviewer")

    wp_b = engine.calculate_compliance(pkg_overrun, ruleset, plan_b, audit_b)

    # Assertions for Scenario B
    assert len(wp_b.cap_breakdowns) == 1
    cap_b = wp_b.cap_breakdowns[0]
    assert cap_b.cap_id == "rdw_shared_aggregate_25_post_cap"
    assert round(cap_b.computed_limit, 2) == 10000000.00
    assert round(cap_b.eligible_claimed, 2) == 13000000.00
    assert round(cap_b.allowed_amount, 2) == 10000000.00
    assert round(cap_b.haircut_amount, 2) == 3000000.00
    # Overrun unallocated flag is strictly raised
    assert cap_b.is_aggregate_exceeded_without_waterfall is True
    assert cap_b.flagged_for_human_review is True
    # Overall status MUST be CANNOT_COMPUTE until human allocation is performed
    assert wp_b.overall_status == ComplianceStatus.CANNOT_COMPUTE
    assert any("Aggregate add-back cap was exceeded" in f for f in wp_b.human_review_flags)

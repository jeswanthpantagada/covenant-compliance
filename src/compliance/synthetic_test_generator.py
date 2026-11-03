"""
Synthetic Test Case Generator for Covenant Compliance.

Generates at least 10 distinct, deterministic quarterly compliance packages covering:
1. Comfortably compliant scenario (healthy cushion).
2. Direct covenant breach (fail under all readings).
3. Capped add-back haircut (single fixed or % cap).
4. Circular post-addback EBITDA cap (closed-form algebraic resolution).
5. Contractual cash netting cap ($37.5M ceiling).
6. Unallocated aggregate cap overrun (multiple items exceeding basket without waterfall).
7. Outcome depends on a reading (borderline pass/fail depending on interpretation).
8. Negative EBITDA scenario (flags for human review, avoids automatic default).
9. Zero EBITDA scenario (avoids ZeroDivisionError, flags for review).
10. Missing human approval gate (cannot compute).
11. Ineligible add-back rejection (routine capex disguised as one-time charge).
12. Unit multiplier scaling ($000s vs exact dollars).
"""

import json
from pathlib import Path
from typing import List, Dict, Any

from src.compliance.financial_data import (
    QuarterlyFinancialPackage,
    IncomeStatement,
    BalanceSheet,
    LedgerAccount,
    ClaimedAddBack,
    FinancialStatementType
)
from src.core.ruleset_generator import (
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


def build_synthetic_suite() -> List[Dict[str, Any]]:
    """Builds a list of 12 comprehensive synthetic test scenarios."""
    scenarios: List[Dict[str, Any]] = []

    # Common base rule set
    def make_ruleset(
        ruleset_id="ruleset_syn_standard",
        covenant_ratio=4.50,
        cash_cap=None,
        has_ambiguity=False,
        is_approved=True
    ):
        interps = []
        if has_ambiguity:
            interps.append(
                InterpretationAlternative(
                    ambiguity_id="interp_synergy_cap",
                    clause_reference="Clause (iv) Synergies",
                    issue_description="Ambiguity whether 20% cap applies to pre-EBITDA or post-EBITDA",
                    interpretation_a={"reading": "pre_ebitda", "cap_percent": 0.20},
                    interpretation_b={"reading": "post_ebitda_circular", "cap_percent": 0.20},
                    recommended_reading="interpretation_a"
                )
            )

        return RuleSet(
            ruleset_id=ruleset_id,
            version_id="SYN_v1",
            company="Synthetic Technologies Inc.",
            source_hashes={"syn_doc_1": "hash_syn_123"},
            created_at="2023-01-01T00:00:00Z",
            starting_metric="Consolidated Net Income",
            add_backs=[
                AddBackRule(
                    rule_id="ab_interest",
                    category="Interest Expense",
                    description="Cash interest expense",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=False,
                    verbatim_clause="Interest Expense",
                    citation="[Doc: syn_doc_1 | Section 1.01]"
                ),
                AddBackRule(
                    rule_id="ab_taxes",
                    category="Taxes",
                    description="Taxes on income",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=False,
                    verbatim_clause="Taxes",
                    citation="[Doc: syn_doc_1 | Section 1.01]"
                ),
                AddBackRule(
                    rule_id="ab_da",
                    category="Depreciation & Amortization",
                    description="Depreciation & Amortization",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=False,
                    verbatim_clause="Depreciation & Amortization",
                    citation="[Doc: syn_doc_1 | Section 1.01]"
                ),
                AddBackRule(
                    rule_id="ab_restructuring",
                    category="Restructuring Charges",
                    description="Restructuring costs subject to 25% post-circular cap",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=True,
                    cap=RuleSetCap(
                        cap_id="cap_restruct_25_post",
                        cap_type=CapType.PERCENT_OF_EBITDA_POST,
                        percent_limit=0.25,
                        basis_metric="POST_ADDBACK_EBITDA",
                        is_aggregate=True,
                        shared_with_categories=["Synergies"],
                        exact_clause="25% of Consolidated EBITDA calculated after giving effect",
                        citation="[Doc: syn_doc_1 | Section 1.01]"
                    ),
                    verbatim_clause="Restructuring Charges",
                    citation="[Doc: syn_doc_1 | Section 1.01]"
                ),
                AddBackRule(
                    rule_id="ab_synergies",
                    category="Synergies",
                    description="Pro forma cost savings sharing 25% post-circular cap",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=True,
                    cap=RuleSetCap(
                        cap_id="cap_restruct_25_post",
                        cap_type=CapType.PERCENT_OF_EBITDA_POST,
                        percent_limit=0.25,
                        basis_metric="POST_ADDBACK_EBITDA",
                        is_aggregate=True,
                        shared_with_categories=["Restructuring Charges"],
                        exact_clause="25% of Consolidated EBITDA calculated after giving effect",
                        citation="[Doc: syn_doc_1 | Section 1.01]"
                    ),
                    verbatim_clause="Synergies",
                    citation="[Doc: syn_doc_1 | Section 1.01]"
                ),
                AddBackRule(
                    rule_id="ab_tx_fees",
                    category="Transaction Expenses",
                    description="Transaction expenses capped at greater of $10M and 12.5% pre-EBITDA",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=True,
                    cap=RuleSetCap(
                        cap_id="cap_tx_fees",
                        cap_type=CapType.GREATER_OF,
                        dollar_limit=10000000.0,
                        percent_limit=0.125,
                        basis_metric="PRE_ADDBACK_EBITDA",
                        exact_clause="greater of $10,000,000 and 12.5% of pre-EBITDA",
                        citation="[Doc: syn_doc_1 | Section 1.01]"
                    ),
                    verbatim_clause="Transaction Expenses",
                    citation="[Doc: syn_doc_1 | Section 1.01]"
                )
            ],
            deductions=[],
            net_debt_rule=NetDebtRule(
                debt_components=["Term Loans", "Revolving Loans", "Capital Leases"],
                excluded_debt=[],
                cash_netting_type="CAPPED_CASH_NETTING" if cash_cap else "UNCAPPED_CASH_NETTING",
                cash_cap_amount=cash_cap,
                cash_conditions=["unrestricted cash"],
                verbatim_clause="Total Debt less cash",
                citation="[Doc: syn_doc_1 | Section 7.11]"
            ),
            covenant_rule=CovenantRule(
                covenant_name="Total Net Leverage Ratio",
                governing_section="Section 7.11",
                metric_formula="Consolidated Net Debt / LTM EBITDA",
                testing_frequency="Quarterly",
                schedule=[
                    CovenantScheduleTier(
                        tier_id="tier_1",
                        period_description=f"Shall not exceed {covenant_ratio}:1.00",
                        ratio_ceiling=covenant_ratio
                    )
                ],
                verbatim_clause=f"Section 7.11 Total Net Leverage Ratio shall not exceed {covenant_ratio}:1.00",
                citation="[Doc: syn_doc_1 | Section 7.11]"
            ),
            interpretations=interps,
            human_review_items=[],
            is_approved=is_approved
        )

    # Standard ledger accounts helper
    def make_standard_ledger(int_exp=5.0e6, tax_exp=3.0e6, da_exp=12.0e6, term_loans=200.0e6, cash=30.0e6):
        return [
            LedgerAccount(account_code="1010", account_name="Operating Cash Account", statement_type=FinancialStatementType.BALANCE_SHEET, balance=cash),
            LedgerAccount(account_code="2100", account_name="Term Loan Facility A", statement_type=FinancialStatementType.BALANCE_SHEET, balance=term_loans),
            LedgerAccount(account_code="6100", account_name="Interest Expense on Term Loan", statement_type=FinancialStatementType.INCOME_STATEMENT, balance=int_exp),
            LedgerAccount(account_code="6200", account_name="Corporate Income Tax Expense", statement_type=FinancialStatementType.INCOME_STATEMENT, balance=tax_exp),
            LedgerAccount(account_code="6500", account_name="Depreciation & Amortization Expense", statement_type=FinancialStatementType.INCOME_STATEMENT, balance=da_exp)
        ]

    # CASE 1: Pass Comfortably
    p1 = QuarterlyFinancialPackage(
        package_id="case_01_pass_comfortably",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=100.0e6, net_income=15.0e6, interest_expense=5.0e6, tax_expense=3.0e6, depreciation_amortization=7.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=20.0e6, term_loans=150.0e6),
        ledger_accounts=make_standard_ledger(5.0e6, 3.0e6, 7.0e6, 150.0e6, 20.0e6),
        claimed_add_backs=[
            ClaimedAddBack(addback_id="cb1", category="Restructuring Charges", claimed_amount=2.0e6, description="Severance costs for warehouse consolidation", supporting_evidence="Severance agreements and payroll ledger entries")
        ],
        prior_three_quarters_ebitda=[30.0e6, 32.0e6, 31.0e6]  # LTM approx 125M vs 130M Net Debt -> 1.04x vs 4.50x
    )
    scenarios.append({
        "case_name": "Case 1: Comfortably Compliant",
        "expected_status": "Pass under all readings",
        "package": p1,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 2: Breach High Debt (Fail Under All Readings)
    p2 = QuarterlyFinancialPackage(
        package_id="case_02_breach_high_debt",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=50.0e6, net_income=-5.0e6, interest_expense=15.0e6, tax_expense=1.0e6, depreciation_amortization=4.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=5.0e6, term_loans=400.0e6, revolving_loans=50.0e6),
        ledger_accounts=make_standard_ledger(15.0e6, 1.0e6, 4.0e6, 400.0e6, 5.0e6),
        claimed_add_backs=[],
        prior_three_quarters_ebitda=[15.0e6, 15.0e6, 15.0e6]  # LTM ~60M vs 445M Net Debt -> 7.41x vs 4.50x
    )
    scenarios.append({
        "case_name": "Case 2: Direct Covenant Breach",
        "expected_status": "Fail under all readings",
        "package": p2,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 3: Capped Add-back Haircut
    p3 = QuarterlyFinancialPackage(
        package_id="case_03_capped_addback_haircut",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=80.0e6, net_income=10.0e6, interest_expense=4.0e6, tax_expense=2.0e6, depreciation_amortization=4.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=10.0e6, term_loans=180.0e6),
        ledger_accounts=make_standard_ledger(4.0e6, 2.0e6, 4.0e6, 180.0e6, 10.0e6),
        claimed_add_backs=[
            # Claiming 25M restructuring on 20M baseline (Cap = (0.25/0.75)*20M = 6.67M)
            ClaimedAddBack(addback_id="cb3", category="Restructuring Charges", claimed_amount=25.0e6, description="Massive restructuring overhaul", supporting_evidence="Board minutes and vendor invoices")
        ],
        prior_three_quarters_ebitda=[25.0e6, 25.0e6, 25.0e6]
    )
    scenarios.append({
        "case_name": "Case 3: Capped Add-back Haircut",
        "expected_status": "Pass under all readings",
        "package": p3,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 4: Circular Post-EBITDA Cap Exact Algebraic Check
    p4 = QuarterlyFinancialPackage(
        package_id="case_04_circular_post_ebitda_cap",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=90.0e6, net_income=12.0e6, interest_expense=6.0e6, tax_expense=2.0e6, depreciation_amortization=10.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=15.0e6, term_loans=220.0e6),
        ledger_accounts=make_standard_ledger(6.0e6, 2.0e6, 10.0e6, 220.0e6, 15.0e6),
        claimed_add_backs=[
            # Baseline = 30M. Circular 25% post-cap limit = (0.25 / 0.75) * 30M = exactly $10,000,000. Claiming $15M -> exactly 10M allowed.
            ClaimedAddBack(addback_id="cb4", category="Restructuring Charges", claimed_amount=15.0e6, description="Facility closure charges", supporting_evidence="Detailed closing ledger")
        ],
        prior_three_quarters_ebitda=[35.0e6, 35.0e6, 35.0e6]
    )
    scenarios.append({
        "case_name": "Case 4: Circular Post-EBITDA Cap Algebraic Exactness",
        "expected_status": "Pass under all readings",
        "package": p4,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 5: Contractual Cash Netting Cap ($37.5M ceiling)
    p5 = QuarterlyFinancialPackage(
        package_id="case_05_contractual_cash_netting_cap",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=70.0e6, net_income=8.0e6, interest_expense=4.0e6, tax_expense=2.0e6, depreciation_amortization=6.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=60.0e6, term_loans=200.0e6),  # $60M cash on hand, but capped at $37.5M
        ledger_accounts=make_standard_ledger(4.0e6, 2.0e6, 6.0e6, 200.0e6, 60.0e6),
        claimed_add_backs=[],
        prior_three_quarters_ebitda=[20.0e6, 20.0e6, 20.0e6]
    )
    scenarios.append({
        "case_name": "Case 5: Contractual Cash Netting Cap ($37.5M)",
        "expected_status": "Pass under all readings",
        "package": p5,
        "ruleset": make_ruleset(covenant_ratio=4.50, cash_cap=37500000.0)
    })

    # CASE 6: Unallocated Aggregate Cap Overrun
    p6 = QuarterlyFinancialPackage(
        package_id="case_06_unallocated_aggregate_cap_overrun",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=60.0e6, net_income=6.0e6, interest_expense=4.0e6, tax_expense=2.0e6, depreciation_amortization=8.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=10.0e6, term_loans=210.0e6),
        ledger_accounts=make_standard_ledger(4.0e6, 2.0e6, 8.0e6, 210.0e6, 10.0e6),
        claimed_add_backs=[
            # Two items sharing the 25% post-circular cap (Limit = (0.25/0.75)*20M = 6.67M). Both claim 5.0M -> Total 10.0M > 6.67M!
            ClaimedAddBack(addback_id="cb6_a", category="Restructuring Charges", claimed_amount=5.0e6, description="Severance charges", supporting_evidence="Approved severance schedule"),
            ClaimedAddBack(addback_id="cb6_b", category="Synergies", claimed_amount=5.0e6, description="Cost savings synergies", supporting_evidence="Third-party synergy audit report")
        ],
        prior_three_quarters_ebitda=[25.0e6, 25.0e6, 25.0e6]
    )
    scenarios.append({
        "case_name": "Case 6: Unallocated Aggregate Cap Overrun",
        "expected_status": "Cannot compute",
        "package": p6,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 7: Outcome Depends on a Reading
    # Under Reading A (post-circular cap 25% = 6.67M allowed, LTM = 106.67M, Net Debt = 480M -> 4.500x Pass)
    # Under Reading B (pre-cap 20% = 4.0M allowed, LTM = 104.0M, Net Debt = 480M -> 4.615x FAIL)
    p7 = QuarterlyFinancialPackage(
        package_id="case_07_outcome_depends",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=70.0e6, net_income=6.0e6, interest_expense=4.0e6, tax_expense=2.0e6, depreciation_amortization=8.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=10.0e6, term_loans=490.0e6),  # Net Debt = 480M
        ledger_accounts=make_standard_ledger(4.0e6, 2.0e6, 8.0e6, 490.0e6, 10.0e6),
        claimed_add_backs=[
            ClaimedAddBack(addback_id="cb7", category="Restructuring Charges", claimed_amount=8.0e6, description="Restructuring project", supporting_evidence="Audited invoices")
        ],
        prior_three_quarters_ebitda=[26.67e6, 26.67e6, 26.67e6]
    )
    scenarios.append({
        "case_name": "Case 7: Outcome Depends on a Reading",
        "expected_status": "Outcome depends on a reading",
        "package": p7,
        "ruleset": make_ruleset(covenant_ratio=4.55, has_ambiguity=True)
    })

    # CASE 8: Negative EBITDA Scenario
    p8 = QuarterlyFinancialPackage(
        package_id="case_08_negative_ebitda",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=20.0e6, net_income=-40.0e6, interest_expense=3.0e6, tax_expense=0.0, depreciation_amortization=2.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=5.0e6, term_loans=100.0e6),
        ledger_accounts=make_standard_ledger(3.0e6, 0.0, 2.0e6, 100.0e6, 5.0e6),
        claimed_add_backs=[],
        prior_three_quarters_ebitda=[-10.0e6, -5.0e6, -5.0e6]  # LTM is strongly negative (-55M)
    )
    scenarios.append({
        "case_name": "Case 8: Negative EBITDA Scenario",
        "expected_status": "Cannot compute",
        "package": p8,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 9: Zero EBITDA Scenario
    p9 = QuarterlyFinancialPackage(
        package_id="case_09_zero_ebitda",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=20.0e6, net_income=-10.0e6, interest_expense=4.0e6, tax_expense=1.0e6, depreciation_amortization=5.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=5.0e6, term_loans=100.0e6),
        ledger_accounts=make_standard_ledger(4.0e6, 1.0e6, 5.0e6, 100.0e6, 5.0e6),
        claimed_add_backs=[],
        prior_three_quarters_ebitda=[0.0, 0.0, 0.0]  # LTM EBITDA is exactly 0.00
    )
    scenarios.append({
        "case_name": "Case 9: Zero EBITDA Scenario",
        "expected_status": "Cannot compute",
        "package": p9,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 10: Missing Approvals (Cannot Compute)
    p10 = QuarterlyFinancialPackage(
        package_id="case_10_missing_approvals",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=80.0e6, net_income=10.0e6, interest_expense=4.0e6, tax_expense=2.0e6, depreciation_amortization=4.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=10.0e6, term_loans=150.0e6),
        ledger_accounts=make_standard_ledger(4.0e6, 2.0e6, 4.0e6, 150.0e6, 10.0e6),
        claimed_add_backs=[],
        prior_three_quarters_ebitda=[20.0e6, 20.0e6, 20.0e6]
    )
    scenarios.append({
        "case_name": "Case 10: Missing Approvals Gate Block",
        "expected_status": "Cannot compute",
        "package": p10,
        "ruleset": make_ruleset(covenant_ratio=4.50, is_approved=False)  # Unapproved RuleSet
    })

    # CASE 11: Ineligible Disguised Capex Rejection
    p11 = QuarterlyFinancialPackage(
        package_id="case_11_ineligible_addback",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        income_statement=IncomeStatement(revenues=80.0e6, net_income=10.0e6, interest_expense=4.0e6, tax_expense=2.0e6, depreciation_amortization=4.0e6),
        balance_sheet=BalanceSheet(unrestricted_cash=10.0e6, term_loans=150.0e6),
        ledger_accounts=make_standard_ledger(4.0e6, 2.0e6, 4.0e6, 150.0e6, 10.0e6),
        claimed_add_backs=[
            ClaimedAddBack(addback_id="cb11", category="Restructuring Charges", claimed_amount=10.0e6, description="Routine maintenance and capex equipment replacement", supporting_evidence="Capital asset register")
        ],
        prior_three_quarters_ebitda=[20.0e6, 20.0e6, 20.0e6]
    )
    scenarios.append({
        "case_name": "Case 11: Ineligible Add-back Rejection",
        "expected_status": "Pass under all readings",
        "package": p11,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    # CASE 12: Unit Scaling in Thousands (unit_multiplier=1000)
    p12 = QuarterlyFinancialPackage(
        package_id="case_12_unit_scaling",
        company="Synthetic Technologies Inc.",
        quarter="Q3 2023",
        period_end_date="2023-09-30",
        unit_multiplier=1000.0,  # Reported in thousands
        income_statement=IncomeStatement(revenues=80000.0, net_income=10000.0, interest_expense=4000.0, tax_expense=2000.0, depreciation_amortization=4000.0),
        balance_sheet=BalanceSheet(unrestricted_cash=10000.0, term_loans=150000.0),
        ledger_accounts=make_standard_ledger(4000.0, 2000.0, 4000.0, 150000.0, 10000.0),
        claimed_add_backs=[],
        prior_three_quarters_ebitda=[20000000.0, 20000000.0, 20000000.0]  # prior in exact dollars
    )
    scenarios.append({
        "case_name": "Case 12: Unit Multiplier Scaling ($000s)",
        "expected_status": "Pass under all readings",
        "package": p12,
        "ruleset": make_ruleset(covenant_ratio=4.50)
    })

    return scenarios

"""
Deterministic Covenant Compliance Calculation Engine.

Performs 100% deterministic Python arithmetic (zero LLM math):
- Consolidated EBITDA calculation with algebraic circular cap resolution.
- Consolidated Net Debt with capped or uncapped cash netting.
- LTM EBITDA rolling 4-quarter aggregation.
- Total Net Leverage Ratio, Ratio Headroom, Absolute EBITDA Buffer, and EBITDA Cushion (%).
- Strict enforcement of human review when aggregate caps are exceeded without explicit waterfalls.
- Special handling for zero or negative EBITDA (flags for human review without declaring default).
- Evaluates alternative contractual readings to produce one of the 4 compliance statuses:
  1. PASS_UNDER_ALL_READINGS
  2. FAIL_UNDER_ALL_READINGS
  3. OUTCOME_DEPENDS_ON_A_READING
  4. CANNOT_COMPUTE
- Generates a fully auditable compliance workpaper traceable to ledger lines and credit agreement clauses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple
from pydantic import BaseModel, Field

from src.core.ruleset_generator import RuleSet, CapType, RuleSetCap
from src.compliance.financial_data import QuarterlyFinancialPackage
from src.compliance.account_mapper import AccountMappingPlan
from src.compliance.addback_auditor import AddBackAuditReport, AddBackEligibilityStatus


class ComplianceStatus(str, Enum):
    PASS_UNDER_ALL_READINGS = "Pass under all readings"
    FAIL_UNDER_ALL_READINGS = "Fail under all readings"
    OUTCOME_DEPENDS_ON_A_READING = "Outcome depends on a reading"
    CANNOT_COMPUTE = "Cannot compute"


class CapCalculationDetail(BaseModel):
    """Detailed mathematical breakdown of an add-back cap calculation."""
    cap_id: str
    cap_type: str
    eligible_claimed: float
    computed_limit: float
    allowed_amount: float
    haircut_amount: float
    formula_used: str
    is_aggregate_exceeded_without_waterfall: bool = False
    flagged_for_human_review: bool = False


class ReadingCalculationDetail(BaseModel):
    """Compliance calculation metrics evaluated under a specific contractual reading."""
    reading_name: str
    baseline_ebitda: float
    total_capped_addbacks_allowed: float
    current_quarter_ebitda: float
    ltm_ebitda: float
    total_funded_debt: float
    unrestricted_cash: float
    cash_netted: float
    consolidated_net_debt: float
    actual_leverage_ratio: Optional[float] = None
    covenant_threshold: float
    ratio_headroom: Optional[float] = None
    absolute_ebitda_buffer: Optional[float] = None
    ebitda_cushion_pct: Optional[float] = None
    is_compliant: Optional[bool] = None
    notes: List[str] = Field(default_factory=list)


class WorkpaperLineItem(BaseModel):
    """Single line item in the auditable compliance workpaper."""
    category: str
    line_name: str
    reported_value: float
    allowed_value: float
    variance: float
    ledger_account_code: Optional[str] = None
    contract_citation: str
    notes: str


class ComplianceWorkpaper(BaseModel):
    """Complete auditable compliance workpaper with financial and legal provenance."""
    workpaper_id: str
    package_id: str
    ruleset_id: str
    company: str
    quarter: str
    period_end_date: str
    generated_at: str
    overall_status: ComplianceStatus
    summary_metrics: Dict[str, Any] = Field(default_factory=dict)
    readings_details: Dict[str, ReadingCalculationDetail] = Field(default_factory=dict)
    cap_breakdowns: List[CapCalculationDetail] = Field(default_factory=list)
    ebitda_waterfall_lines: List[WorkpaperLineItem] = Field(default_factory=list)
    net_debt_lines: List[WorkpaperLineItem] = Field(default_factory=list)
    audit_notes: List[str] = Field(default_factory=list)
    human_review_flags: List[str] = Field(default_factory=list)


class ComplianceCalculationEngine:
    """
    Executes quarterly covenant compliance calculations.
    """

    def calculate_compliance(
        self,
        package: QuarterlyFinancialPackage,
        ruleset: RuleSet,
        mapping_plan: AccountMappingPlan,
        audit_report: AddBackAuditReport,
        forced_alternative_reading: Optional[str] = None
    ) -> ComplianceWorkpaper:
        now_iso = datetime.now(timezone.utc).isoformat()
        notes: List[str] = []
        review_flags: List[str] = []

        # -------------------------------------------------------------
        # STEP 1: VALIDATION & APPROVAL GATES
        # -------------------------------------------------------------
        pkg_val = package.validate_package()
        if not pkg_val.is_valid:
            return self._build_cannot_compute(
                package=package,
                ruleset=ruleset,
                reasons=[f"Financial package validation failed: {pkg_val.validation_errors}"]
            )

        if not ruleset.is_approved:
            return self._build_cannot_compute(
                package=package,
                ruleset=ruleset,
                reasons=["RuleSet has not received human reviewer approval. Calculation blocked."]
            )

        if not mapping_plan.is_approved:
            return self._build_cannot_compute(
                package=package,
                ruleset=ruleset,
                reasons=["Account mapping plan has not received human reviewer approval. Calculation blocked."]
            )

        if not audit_report.is_approved:
            return self._build_cannot_compute(
                package=package,
                ruleset=ruleset,
                reasons=["Add-back audit report has not received human reviewer approval. Calculation blocked."]
            )

        # -------------------------------------------------------------
        # STEP 2: CALCULATE UNDER PRIMARY READING & ALTERNATIVE READINGS
        # -------------------------------------------------------------
        # Determine covenant limit for this quarter
        covenant_threshold = self._get_covenant_threshold(ruleset, package.period_end_date)

        # Run primary calculation
        primary_detail, cap_breakdowns, ebitda_lines, net_debt_lines = self._compute_reading(
            reading_name="Primary / Base Interpretation",
            package=package,
            ruleset=ruleset,
            mapping_plan=mapping_plan,
            audit_report=audit_report,
            covenant_threshold=covenant_threshold,
            use_algebraic_circular_cap=True
        )

        readings_map: Dict[str, ReadingCalculationDetail] = {"Primary": primary_detail}

        # Check if alternative interpretations exist
        has_alternatives = len(ruleset.interpretations) > 0
        if has_alternatives:
            # Run alternative calculation (e.g. Iterative or Pre-Addback reading)
            alt_detail, _, _, _ = self._compute_reading(
                reading_name="Alternative Interpretation",
                package=package,
                ruleset=ruleset,
                mapping_plan=mapping_plan,
                audit_report=audit_report,
                covenant_threshold=covenant_threshold,
                use_algebraic_circular_cap=False
            )
            readings_map["Alternative"] = alt_detail

        # -------------------------------------------------------------
        # STEP 3: DETERMINE 4-CASE COMPLIANCE STATUS
        # -------------------------------------------------------------
        # Check for unallocated aggregate overrun
        has_unallocated_overrun = any(c.is_aggregate_exceeded_without_waterfall for c in cap_breakdowns)
        if has_unallocated_overrun:
            review_flags.append(
                "Aggregate add-back cap was exceeded by multiple categories without an explicit contractual waterfall. "
                "Requires human reviewer allocation before final certification."
            )

        # Check for negative / zero EBITDA
        if primary_detail.ltm_ebitda <= 0:
            review_flags.append(
                f"LTM Consolidated EBITDA is non-positive (${primary_detail.ltm_ebitda:,.2f}). "
                f"Flagged for human review. Default not automatically declared."
            )

        # Assess pass/fail across all readings
        overall_status = ComplianceStatus.CANNOT_COMPUTE
        if has_unallocated_overrun:
            # Unallocated aggregate cap overrun requires human review allocation before compliance status can be computed
            overall_status = ComplianceStatus.CANNOT_COMPUTE
        elif primary_detail.is_compliant is None:
            overall_status = ComplianceStatus.CANNOT_COMPUTE
        elif not has_alternatives:
            overall_status = (
                ComplianceStatus.PASS_UNDER_ALL_READINGS
                if primary_detail.is_compliant
                else ComplianceStatus.FAIL_UNDER_ALL_READINGS
            )
        else:
            primary_pass = primary_detail.is_compliant
            alt_pass = readings_map["Alternative"].is_compliant
            if primary_pass and alt_pass:
                overall_status = ComplianceStatus.PASS_UNDER_ALL_READINGS
            elif not primary_pass and not alt_pass:
                overall_status = ComplianceStatus.FAIL_UNDER_ALL_READINGS
            else:
                overall_status = ComplianceStatus.OUTCOME_DEPENDS_ON_A_READING

        # -------------------------------------------------------------
        # STEP 4: ASSEMBLE WORKPAPER
        # -------------------------------------------------------------
        summary = {
            "Consolidated_Net_Income": package.get_scaled_net_income(),
            "Baseline_EBITDA": primary_detail.baseline_ebitda,
            "Capped_Addbacks_Allowed": primary_detail.total_capped_addbacks_allowed,
            "Current_Quarter_EBITDA": primary_detail.current_quarter_ebitda,
            "LTM_EBITDA": primary_detail.ltm_ebitda,
            "Total_Funded_Debt": primary_detail.total_funded_debt,
            "Unrestricted_Cash": primary_detail.unrestricted_cash,
            "Cash_Netted": primary_detail.cash_netted,
            "Consolidated_Net_Debt": primary_detail.consolidated_net_debt,
            "Actual_Total_Net_Leverage_Ratio": primary_detail.actual_leverage_ratio,
            "Covenant_Ceiling": covenant_threshold,
            "Ratio_Headroom": primary_detail.ratio_headroom,
            "Absolute_EBITDA_Buffer": primary_detail.absolute_ebitda_buffer,
            "EBITDA_Cushion_Pct": primary_detail.ebitda_cushion_pct,
            "Is_Compliant": primary_detail.is_compliant
        }

        return ComplianceWorkpaper(
            workpaper_id=f"wp_{package.package_id}_{ruleset.ruleset_id}",
            package_id=package.package_id,
            ruleset_id=ruleset.ruleset_id,
            company=package.company,
            quarter=package.quarter,
            period_end_date=package.period_end_date,
            generated_at=now_iso,
            overall_status=overall_status,
            summary_metrics=summary,
            readings_details=readings_map,
            cap_breakdowns=cap_breakdowns,
            ebitda_waterfall_lines=ebitda_lines,
            net_debt_lines=net_debt_lines,
            audit_notes=notes,
            human_review_flags=review_flags
        )

    def _compute_reading(
        self,
        reading_name: str,
        package: QuarterlyFinancialPackage,
        ruleset: RuleSet,
        mapping_plan: AccountMappingPlan,
        audit_report: AddBackAuditReport,
        covenant_threshold: float,
        use_algebraic_circular_cap: bool
    ) -> Tuple[ReadingCalculationDetail, List[CapCalculationDetail], List[WorkpaperLineItem], List[WorkpaperLineItem]]:
        ebitda_lines: List[WorkpaperLineItem] = []
        net_debt_lines: List[WorkpaperLineItem] = []
        cap_breakdowns: List[CapCalculationDetail] = []
        reading_notes: List[str] = []

        # 1. Starting Net Income
        net_income = package.get_scaled_net_income()
        ebitda_lines.append(
            WorkpaperLineItem(
                category="Starting Metric",
                line_name="Consolidated Net Income",
                reported_value=net_income,
                allowed_value=net_income,
                variance=0.0,
                ledger_account_code="GAAP_NI",
                contract_citation="Section 1.01 Consolidated EBITDA",
                notes="Starting point for Consolidated EBITDA per GAAP income statement."
            )
        )

        # 2. Standard Uncapped Add-Backs from Ledger/Statements (Interest, Taxes, D&A)
        # Pull reported interest, tax, D&A from income statement or approved mappings
        interest_exp = package.income_statement.interest_expense * package.unit_multiplier
        tax_exp = package.income_statement.tax_expense * package.unit_multiplier
        da_exp = package.income_statement.depreciation_amortization * package.unit_multiplier

        # Add to lines
        for cat, val, cite in [
            ("Interest Expense", interest_exp, "Consolidated Interest Expense"),
            ("Taxes", tax_exp, "Provision for Taxes"),
            ("Depreciation & Amortization", da_exp, "Depreciation & Amortization")
        ]:
            if val != 0.0:
                ebitda_lines.append(
                    WorkpaperLineItem(
                        category="Uncapped Add-back",
                        line_name=cat,
                        reported_value=val,
                        allowed_value=val,
                        variance=0.0,
                        contract_citation=cite,
                        notes="Standard uncapped statutory add-back."
                    )
                )

        baseline_ebitda = net_income + interest_exp + tax_exp + da_exp

        # 3. Capped & Qualitative Add-Backs from Audited Claimed Items
        # Group approved eligible items by cap
        eligible_items = [i for i in audit_report.audited_items if i.status == AddBackEligibilityStatus.ELIGIBLE]

        # Map ruleset caps
        cap_dict: Dict[str, RuleSetCap] = {}
        items_by_cap: Dict[str, List[AuditedAddBack]] = {}
        uncapped_eligible_items: List[AuditedAddBack] = []

        for item in eligible_items:
            matching_rule = next((r for r in ruleset.add_backs if r.category.lower() == item.category.lower()), None)
            if matching_rule and matching_rule.is_subject_to_cap and matching_rule.cap:
                c_id = matching_rule.cap.cap_id
                cap_dict[c_id] = matching_rule.cap
                if c_id not in items_by_cap:
                    items_by_cap[c_id] = []
                items_by_cap[c_id].append(item)
            else:
                uncapped_eligible_items.append(item)

        total_capped_allowed = 0.0

        for item in uncapped_eligible_items:
            ebitda_lines.append(
                WorkpaperLineItem(
                    category="Uncapped Discretionary Add-back",
                    line_name=item.category,
                    reported_value=item.claimed_amount,
                    allowed_value=item.eligible_amount,
                    variance=item.claimed_amount - item.eligible_amount,
                    contract_citation=item.citation,
                    notes=item.audit_rationale
                )
            )
            total_capped_allowed += item.eligible_amount

        # Evaluate each cap basket deterministically
        for c_id, cap in cap_dict.items():
            items = items_by_cap.get(c_id, [])
            total_eligible_in_cap = sum(i.eligible_amount for i in items)
            total_claimed_in_cap = sum(i.claimed_amount for i in items)

            # Compute cap limit based on cap_type
            limit = 0.0
            formula = ""
            haircut = 0.0
            allowed = 0.0
            is_overrun_unallocated = False

            if cap.cap_type == CapType.FIXED_DOLLAR:
                limit = cap.dollar_limit or 0.0
                formula = f"min(Eligible, ${limit:,.2f})"

            elif cap.cap_type == CapType.PERCENT_OF_EBITDA_PRE:
                pct = cap.percent_limit or 0.0
                limit = max(0.0, baseline_ebitda * pct)
                formula = f"{pct*100}% of Baseline EBITDA (${baseline_ebitda:,.2f})"

            elif cap.cap_type == CapType.PERCENT_OF_EBITDA_POST:
                pct = cap.percent_limit or 0.25
                if use_algebraic_circular_cap:
                    # Closed-form solution: Capped <= (pct / (1 - pct)) * Baseline
                    multiplier = pct / (1.0 - pct)
                    limit = max(0.0, baseline_ebitda * multiplier)
                    formula = f"Closed-form circular resolution: ({pct:.2f} / (1 - {pct:.2f})) * Baseline EBITDA = {multiplier:.4f} * ${baseline_ebitda:,.2f}"
                else:
                    # Alternative reading: 25% of baseline prior to add-back
                    limit = max(0.0, baseline_ebitda * pct)
                    formula = f"Alternative reading: {pct*100}% of Baseline EBITDA"

            elif cap.cap_type == CapType.GREATER_OF:
                d_lim = cap.dollar_limit or 0.0
                p_lim = max(0.0, baseline_ebitda * (cap.percent_limit or 0.0))
                limit = max(d_lim, p_lim)
                formula = f"greater of ${d_lim:,.2f} and {cap.percent_limit*100}% of Baseline EBITDA (${p_lim:,.2f})"

            # Apply Limit to eligible amount
            if total_eligible_in_cap <= limit:
                allowed = total_eligible_in_cap
                haircut = 0.0
            else:
                allowed = limit
                haircut = total_eligible_in_cap - limit
                if cap.is_aggregate and len(items) > 1:
                    is_overrun_unallocated = True

            total_capped_allowed += allowed

            cap_breakdowns.append(
                CapCalculationDetail(
                    cap_id=c_id,
                    cap_type=cap.cap_type.value,
                    eligible_claimed=total_eligible_in_cap,
                    computed_limit=limit,
                    allowed_amount=allowed,
                    haircut_amount=haircut,
                    formula_used=formula,
                    is_aggregate_exceeded_without_waterfall=is_overrun_unallocated,
                    flagged_for_human_review=is_overrun_unallocated
                )
            )

            # Record line items
            for it in items:
                # Pro-rata for display if aggregate overrun, but marked for review
                it_allowed = (it.eligible_amount / total_eligible_in_cap) * allowed if total_eligible_in_cap > 0 else 0.0
                ebitda_lines.append(
                    WorkpaperLineItem(
                        category="Capped Add-back",
                        line_name=it.category,
                        reported_value=it.claimed_amount,
                        allowed_value=it_allowed,
                        variance=it.claimed_amount - it_allowed,
                        contract_citation=it.citation,
                        notes=f"Governed by cap '{c_id}'. Haircut: ${it.claimed_amount - it_allowed:,.2f}"
                    )
                )

        current_quarter_ebitda = baseline_ebitda + total_capped_allowed

        # 4. LTM Consolidated EBITDA Rolling Sum
        ltm_ebitda = current_quarter_ebitda
        if package.prior_three_quarters_ebitda:
            ltm_ebitda += sum(package.prior_three_quarters_ebitda)

        # 5. Consolidated Net Debt Calculation
        total_funded_debt = package.get_scaled_total_debt()
        unrestricted_cash = package.get_scaled_unrestricted_cash()

        net_debt_rule = ruleset.net_debt_rule
        allowed_cash_netting = unrestricted_cash

        if net_debt_rule.cash_netting_type == "CAPPED_CASH_NETTING" and net_debt_rule.cash_cap_amount is not None:
            allowed_cash_netting = min(unrestricted_cash, net_debt_rule.cash_cap_amount)

        consolidated_net_debt = max(0.0, total_funded_debt - allowed_cash_netting)

        net_debt_lines.append(
            WorkpaperLineItem(
                category="Debt",
                line_name="Total Funded Debt Obligations",
                reported_value=total_funded_debt,
                allowed_value=total_funded_debt,
                variance=0.0,
                contract_citation=net_debt_rule.citation,
                notes="Term loans, revolvers, and capital leases."
            )
        )
        net_debt_lines.append(
            WorkpaperLineItem(
                category="Cash Netting",
                line_name="Unrestricted Cash Deduction",
                reported_value=unrestricted_cash,
                allowed_value=-allowed_cash_netting,
                variance=unrestricted_cash - allowed_cash_netting,
                contract_citation=net_debt_rule.citation,
                notes=f"Cash netting model: {net_debt_rule.cash_netting_type} (Max cap: {net_debt_rule.cash_cap_amount})."
            )
        )

        # 6. Total Net Leverage Ratio & Metrics
        actual_ratio: Optional[float] = None
        ratio_headroom: Optional[float] = None
        absolute_buffer: Optional[float] = None
        ebitda_cushion_pct: Optional[float] = None
        is_compliant: Optional[bool] = None

        if ltm_ebitda <= 0:
            reading_notes.append("LTM EBITDA is zero or negative. Ratio cannot be meaningfully calculated.")
            # Do NOT declare default automatically! Set is_compliant = None for human review
            is_compliant = None
        else:
            actual_ratio = round(consolidated_net_debt / ltm_ebitda, 4)
            is_compliant = actual_ratio <= covenant_threshold
            ratio_headroom = round(covenant_threshold - actual_ratio, 4)

            # Absolute buffer = LTM EBITDA - (Net Debt / Covenant Ceiling)
            max_tolerable_debt = ltm_ebitda * covenant_threshold
            min_required_ebitda = consolidated_net_debt / covenant_threshold if covenant_threshold > 0 else 0.0
            absolute_buffer = round(ltm_ebitda - min_required_ebitda, 2)

            # EBITDA cushion (%) = percentage drop in EBITDA that would cause breach
            if ltm_ebitda > 0:
                ebitda_cushion_pct = round((absolute_buffer / ltm_ebitda) * 100.0, 2)

        return (
            ReadingCalculationDetail(
                reading_name=reading_name,
                baseline_ebitda=round(baseline_ebitda, 2),
                total_capped_addbacks_allowed=round(total_capped_allowed, 2),
                current_quarter_ebitda=round(current_quarter_ebitda, 2),
                ltm_ebitda=round(ltm_ebitda, 2),
                total_funded_debt=round(total_funded_debt, 2),
                unrestricted_cash=round(unrestricted_cash, 2),
                cash_netted=round(allowed_cash_netting, 2),
                consolidated_net_debt=round(consolidated_net_debt, 2),
                actual_leverage_ratio=actual_ratio,
                covenant_threshold=covenant_threshold,
                ratio_headroom=ratio_headroom,
                absolute_ebitda_buffer=absolute_buffer,
                ebitda_cushion_pct=ebitda_cushion_pct,
                is_compliant=is_compliant,
                notes=reading_notes
            ),
            cap_breakdowns,
            ebitda_lines,
            net_debt_lines
        )

    def _get_covenant_threshold(self, ruleset: RuleSet, period_end_date: str) -> float:
        """Finds active covenant tier for the period end date."""
        for tier in ruleset.covenant_rule.schedule:
            if tier.effective_start_date and period_end_date < tier.effective_start_date:
                continue
            if tier.effective_end_date and period_end_date > tier.effective_end_date:
                continue
            return tier.ratio_ceiling
        # Fallback to first tier
        if ruleset.covenant_rule.schedule:
            return ruleset.covenant_rule.schedule[0].ratio_ceiling
        return 5.00

    def _build_cannot_compute(
        self,
        package: QuarterlyFinancialPackage,
        ruleset: RuleSet,
        reasons: List[str]
    ) -> ComplianceWorkpaper:
        now_iso = datetime.now(timezone.utc).isoformat()
        return ComplianceWorkpaper(
            workpaper_id=f"wp_blocked_{package.package_id}",
            package_id=package.package_id,
            ruleset_id=ruleset.ruleset_id,
            company=package.company,
            quarter=package.quarter,
            period_end_date=package.period_end_date,
            generated_at=now_iso,
            overall_status=ComplianceStatus.CANNOT_COMPUTE,
            summary_metrics={},
            readings_details={},
            cap_breakdowns=[],
            ebitda_waterfall_lines=[],
            net_debt_lines=[],
            audit_notes=reasons,
            human_review_flags=reasons
        )

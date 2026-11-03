"""
Financial Data Models and Validation for Quarterly Compliance Checking.

Models:
- LedgerAccount: Individual general ledger account and balances.
- IncomeStatement: Standard quarterly GAAP income statement.
- BalanceSheet: Quarterly balance sheet with debt and cash breakdowns.
- ClaimedAddBack: Add-back adjustments submitted by borrower for the quarter.
- QuarterlyFinancialPackage: Complete quarterly compliance data submission.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

from src.utils.dates import parse_iso_date, normalize_date_or_quarter


class FinancialStatementType(str, Enum):
    INCOME_STATEMENT = "INCOME_STATEMENT"
    BALANCE_SHEET = "BALANCE_SHEET"
    CASH_FLOW = "CASH_FLOW"


class LedgerAccount(BaseModel):
    """A general ledger account code and line item."""
    account_code: str
    account_name: str
    statement_type: FinancialStatementType
    balance: float
    description: Optional[str] = None
    sub_entries: List[Dict[str, Any]] = Field(default_factory=list)


class IncomeStatement(BaseModel):
    """Quarterly Income Statement lines (GAAP)."""
    revenues: float = 0.0
    operating_expenses: float = 0.0
    operating_income: float = 0.0
    interest_expense: float = 0.0
    tax_expense: float = 0.0
    depreciation_amortization: float = 0.0
    net_income: float  # Starting point for Consolidated EBITDA


class BalanceSheet(BaseModel):
    """Quarterly Balance Sheet lines focusing on Debt and Cash."""
    cash_and_equivalents: float = 0.0
    restricted_cash: float = 0.0
    unrestricted_cash: float = 0.0
    term_loans: float = 0.0
    revolving_loans: float = 0.0
    capital_leases: float = 0.0
    letters_of_credit_drawn: float = 0.0
    other_funded_debt: float = 0.0

    @property
    def total_funded_debt(self) -> float:
        """Sum of all funded debt obligations."""
        return (
            self.term_loans +
            self.revolving_loans +
            self.capital_leases +
            self.letters_of_credit_drawn +
            self.other_funded_debt
        )


class ClaimedAddBack(BaseModel):
    """A borrower-submitted adjustment to Consolidated EBITDA."""
    addback_id: str
    category: str  # e.g., 'Restructuring Charges', 'Synergies', 'Transaction Expenses'
    claimed_amount: float
    description: str
    supporting_evidence: str
    contract_clause_reference: Optional[str] = None
    quarter: Optional[str] = None
    ledger_account_code: Optional[str] = None


class FinancialPackageValidationResult(BaseModel):
    """Outcome of validating a quarterly financial package before calculation."""
    is_valid: bool
    missing_fields: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class QuarterlyFinancialPackage(BaseModel):
    """
    Complete package of financial deliverables for a compliance testing quarter.
    """
    package_id: str
    company: str
    quarter: str  # e.g., 'Q3 2023', '2023-Q3'
    period_end_date: str  # YYYY-MM-DD
    currency: str = "USD"
    unit_multiplier: float = 1.0  # 1.0 = exact dollars; 1000.0 = $000s; 1000000.0 = $M
    income_statement: IncomeStatement
    balance_sheet: BalanceSheet
    ledger_accounts: List[LedgerAccount] = Field(default_factory=list)
    claimed_add_backs: List[ClaimedAddBack] = Field(default_factory=list)
    prior_three_quarters_ebitda: List[float] = Field(default_factory=list)  # [Q-3, Q-2, Q-1] for LTM
    is_approved_by_analyst: bool = False

    def get_scaled_net_income(self) -> float:
        return self.income_statement.net_income * self.unit_multiplier

    def get_scaled_total_debt(self) -> float:
        return self.balance_sheet.total_funded_debt * self.unit_multiplier

    def get_scaled_unrestricted_cash(self) -> float:
        return self.balance_sheet.unrestricted_cash * self.unit_multiplier

    def validate_package(self) -> FinancialPackageValidationResult:
        """
        Validates completeness, units, currency, and date formats.
        """
        missing: List[str] = []
        errors: List[str] = []
        warnings: List[str] = []

        if not self.company:
            missing.append("company")
        if not self.period_end_date:
            missing.append("period_end_date")
        else:
            iso = parse_iso_date(self.period_end_date)
            if not iso:
                errors.append(f"Invalid period_end_date format '{self.period_end_date}'. Must be YYYY-MM-DD.")

        if self.currency.upper() != "USD":
            warnings.append(f"Package currency is '{self.currency}'. Expected USD.")

        if self.unit_multiplier <= 0:
            errors.append(f"Unit multiplier must be positive, got {self.unit_multiplier}.")

        if len(self.prior_three_quarters_ebitda) < 3:
            warnings.append(
                f"Package contains {len(self.prior_three_quarters_ebitda)} prior quarters of EBITDA; "
                f"expected 3 for standard 4-quarter LTM run-rate."
            )

        # Check negative cash
        if self.balance_sheet.unrestricted_cash < 0:
            errors.append("Unrestricted cash cannot be negative.")

        is_valid = (len(missing) == 0 and len(errors) == 0)
        return FinancialPackageValidationResult(
            is_valid=is_valid,
            missing_fields=missing,
            validation_errors=errors,
            warnings=warnings
        )

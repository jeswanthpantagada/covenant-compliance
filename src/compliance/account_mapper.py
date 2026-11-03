"""
General Ledger Account Mapping Engine.

Suggests mappings from raw ledger accounts to the approved Rule Set's financial categories:
- Starting Metric (Consolidated Net Income)
- Add-back categories (Interest Expense, Taxes, D&A, Restructuring, Synergies, Transaction Expenses)
- Debt components (Term Loans, Revolving Facilities, Capital Leases)
- Cash components (Unrestricted Cash, Restricted Cash)

Enforces mandatory Human Review Approval Gate:
Calculations cannot proceed until account mappings have been explicitly approved by a human.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

from src.core.ruleset_generator import RuleSet
from src.compliance.financial_data import LedgerAccount, QuarterlyFinancialPackage, FinancialStatementType


class MappingConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNMAPPED = "UNMAPPED"


class AccountMappingEntry(BaseModel):
    """Mapping of a single ledger account to a Rule Set financial category."""
    account_code: str
    account_name: str
    statement_type: FinancialStatementType
    balance: float
    mapped_target: str  # e.g. 'Interest Expense', 'Taxes', 'Term Loans', 'Unrestricted Cash', 'Operating Expenses'
    target_rule_type: str  # 'STARTING_METRIC' | 'ADDBACK' | 'DEDUCTION' | 'DEBT' | 'CASH' | 'EXCLUDED'
    confidence: MappingConfidence = MappingConfidence.MEDIUM
    rationale: str
    is_user_modified: bool = False


class AccountMappingPlan(BaseModel):
    """Complete account mapping plan requiring explicit human reviewer approval."""
    plan_id: str
    package_id: str
    ruleset_id: str
    entries: List[AccountMappingEntry] = Field(default_factory=list)
    unmapped_accounts: List[str] = Field(default_factory=list)
    is_approved: bool = False
    approved_by: Optional[str] = None
    approval_timestamp: Optional[str] = None

    def approve(self, reviewer_name: str) -> None:
        """Approves the account mapping plan, unlocking the calculation engine."""
        self.is_approved = True
        self.approved_by = reviewer_name
        self.approval_timestamp = datetime.now(timezone.utc).isoformat()

    def get_entries_by_target(self, target: str) -> List[AccountMappingEntry]:
        t_lower = target.lower()
        return [e for e in self.entries if e.mapped_target.lower() == t_lower]


class AccountMapper:
    """
    Analyzes ledger accounts and matches them against the approved Rule Set.
    Uses pattern-matching keywords and taxonomy rules.
    """

    def suggest_mappings(
        self,
        package: QuarterlyFinancialPackage,
        ruleset: RuleSet
    ) -> AccountMappingPlan:
        entries: List[AccountMappingEntry] = []
        unmapped: List[str] = []

        # Available add-back categories in RuleSet
        addback_categories = {ab.category.lower(): ab.category for ab in ruleset.add_backs}

        for acc in package.ledger_accounts:
            name_lower = acc.account_name.lower()
            code = acc.account_code

            # 1. Cash Accounts
            if "cash" in name_lower or "checking" in name_lower or "deposit" in name_lower:
                if "restricted" in name_lower:
                    entries.append(
                        AccountMappingEntry(
                            account_code=code,
                            account_name=acc.account_name,
                            statement_type=acc.statement_type,
                            balance=acc.balance,
                            mapped_target="Restricted Cash",
                            target_rule_type="CASH",
                            confidence=MappingConfidence.HIGH,
                            rationale="Identified as restricted cash; excluded from cash netting per agreement conditions."
                        )
                    )
                else:
                    entries.append(
                        AccountMappingEntry(
                            account_code=code,
                            account_name=acc.account_name,
                            statement_type=acc.statement_type,
                            balance=acc.balance,
                            mapped_target="Unrestricted Cash",
                            target_rule_type="CASH",
                            confidence=MappingConfidence.HIGH,
                            rationale="Mapped to unrestricted cash for debt netting under NetDebtRule."
                        )
                    )
                continue

            # 2. Debt Accounts
            if any(k in name_lower for k in ["term loan", "term debt", "credit facility", "revolving loan", "revolver", "capital lease", "notes payable"]):
                target = "Term Loans" if "term" in name_lower else ("Revolving Loans" if "revolv" in name_lower else "Total Debt")
                entries.append(
                    AccountMappingEntry(
                        account_code=code,
                        account_name=acc.account_name,
                        statement_type=acc.statement_type,
                        balance=acc.balance,
                        mapped_target=target,
                        target_rule_type="DEBT",
                        confidence=MappingConfidence.HIGH,
                        rationale=f"Funded debt obligation mapped to {target} in Net Debt."
                    )
                )
                continue

            # 3. Add-Back Expense Accounts
            mapped_ab = False
            for cat_lower, cat_canon in addback_categories.items():
                # Direct or keyword match
                if cat_lower in name_lower or (cat_lower == "taxes" and ("tax provision" in name_lower or "income tax" in name_lower)) or \
                   (cat_lower == "interest expense" and "interest" in name_lower) or \
                   (cat_lower in ["depreciation & amortization", "depreciation and amortization"] and ("depreciation" in name_lower or "amortization" in name_lower)):
                    entries.append(
                        AccountMappingEntry(
                            account_code=code,
                            account_name=acc.account_name,
                            statement_type=acc.statement_type,
                            balance=acc.balance,
                            mapped_target=cat_canon,
                            target_rule_type="ADDBACK",
                            confidence=MappingConfidence.HIGH,
                            rationale=f"Account corresponds to permitted add-back category '{cat_canon}'."
                        )
                    )
                    mapped_ab = True
                    break

            if mapped_ab:
                continue

            # 4. Standard Operational Accounts
            if "revenue" in name_lower or "sales" in name_lower:
                entries.append(
                    AccountMappingEntry(
                        account_code=code,
                        account_name=acc.account_name,
                        statement_type=acc.statement_type,
                        balance=acc.balance,
                        mapped_target="Revenues",
                        target_rule_type="EXCLUDED",
                        confidence=MappingConfidence.HIGH,
                        rationale="Top-line revenue."
                    )
                )
            elif "operating expense" in name_lower or "cogs" in name_lower or "cost of goods" in name_lower or "general & administrative" in name_lower:
                entries.append(
                    AccountMappingEntry(
                        account_code=code,
                        account_name=acc.account_name,
                        statement_type=acc.statement_type,
                        balance=acc.balance,
                        mapped_target="Operating Expenses",
                        target_rule_type="EXCLUDED",
                        confidence=MappingConfidence.MEDIUM,
                        rationale="Standard operating expenses."
                    )
                )
            else:
                unmapped.append(code)
                entries.append(
                    AccountMappingEntry(
                        account_code=code,
                        account_name=acc.account_name,
                        statement_type=acc.statement_type,
                        balance=acc.balance,
                        mapped_target="Unmapped",
                        target_rule_type="EXCLUDED",
                        confidence=MappingConfidence.UNMAPPED,
                        rationale="Requires human reviewer manual classification."
                    )
                )

        return AccountMappingPlan(
            plan_id=f"map_{package.package_id}_{ruleset.ruleset_id}",
            package_id=package.package_id,
            ruleset_id=ruleset.ruleset_id,
            entries=entries,
            unmapped_accounts=unmapped,
            is_approved=False
        )

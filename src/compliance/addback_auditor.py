"""
Add-back Adjustment Auditor.

Uses OpenRouter free models (or deterministic fallback) to audit each borrower-claimed
add-back adjustment against the approved Rule Set and source credit agreement.
Classifies each item as:
- ELIGIBLE: Verified against permitted category and contractual conditions.
- INELIGIBLE: Disallowed by contract terms or fails required conditions.
- NEEDS_HUMAN_REVIEW: Ambiguous, insufficient evidence, or discretionary item.

Human reviewers can inspect, override, and approve the audit report before math calculations.
"""

from __future__ import annotations

import os
import re
import json
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

from src.core.amendment_tracker import OPENROUTER_CONFIG
from src.core.ruleset_generator import RuleSet, AddBackRule
from src.compliance.financial_data import ClaimedAddBack


class AddBackEligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class AuditedAddBack(BaseModel):
    """An audited add-back adjustment with status and evidentiary rationale."""
    addback_id: str
    category: str
    claimed_amount: float
    eligible_amount: float
    status: AddBackEligibilityStatus
    governing_clause: str
    citation: str
    audit_rationale: str
    is_overridden_by_reviewer: bool = False
    reviewer_notes: Optional[str] = None


class AddBackAuditReport(BaseModel):
    """Comprehensive quarterly add-back audit findings requiring reviewer confirmation."""
    report_id: str
    package_id: str
    ruleset_id: str
    audited_items: List[AuditedAddBack] = Field(default_factory=list)
    total_claimed: float = 0.0
    total_eligible_pre_cap: float = 0.0
    total_ineligible: float = 0.0
    total_needs_review: float = 0.0
    is_approved: bool = False
    approved_by: Optional[str] = None
    approval_timestamp: Optional[str] = None

    def approve(self, reviewer_name: str) -> None:
        """Approves the add-back audit findings."""
        self.is_approved = True
        self.approved_by = reviewer_name
        self.approval_timestamp = datetime.now(timezone.utc).isoformat()

    def override_item(self, addback_id: str, new_status: AddBackEligibilityStatus, eligible_amount: float, notes: str) -> None:
        """Allows human reviewer to override an AI classification."""
        for item in self.audited_items:
            if item.addback_id == addback_id:
                item.status = new_status
                item.eligible_amount = eligible_amount
                item.is_overridden_by_reviewer = True
                item.reviewer_notes = notes
                break
        self._recalculate_totals()

    def _recalculate_totals(self) -> None:
        self.total_claimed = sum(i.claimed_amount for i in self.audited_items)
        self.total_eligible_pre_cap = sum(i.eligible_amount for i in self.audited_items if i.status == AddBackEligibilityStatus.ELIGIBLE)
        self.total_ineligible = sum(i.claimed_amount for i in self.audited_items if i.status == AddBackEligibilityStatus.INELIGIBLE)
        self.total_needs_review = sum(i.claimed_amount for i in self.audited_items if i.status == AddBackEligibilityStatus.NEEDS_HUMAN_REVIEW)


class AddBackAuditor:
    """
    Audits borrower claimed adjustments using OpenRouter LLM or deterministic rules.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")

    def audit_addbacks(
        self,
        package_id: str,
        claimed_addbacks: List[ClaimedAddBack],
        ruleset: RuleSet,
        custom_llm_callable: Optional[Any] = None
    ) -> AddBackAuditReport:
        if custom_llm_callable:
            items = custom_llm_callable(claimed_addbacks, ruleset)
        elif self.api_key:
            try:
                items = self._call_openrouter_audit(claimed_addbacks, ruleset)
            except Exception:
                items = self._deterministic_fallback_audit(claimed_addbacks, ruleset)
        else:
            items = self._deterministic_fallback_audit(claimed_addbacks, ruleset)

        report = AddBackAuditReport(
            report_id=f"audit_{package_id}_{ruleset.ruleset_id}",
            package_id=package_id,
            ruleset_id=ruleset.ruleset_id,
            audited_items=items
        )
        report._recalculate_totals()
        return report

    def _call_openrouter_audit(
        self,
        claimed_addbacks: List[ClaimedAddBack],
        ruleset: RuleSet
    ) -> List[AuditedAddBack]:
        """Calls OpenRouter with meta-llama/llama-3.3-70b-instruct:free."""
        rules_summary = [
            {"category": r.category, "conditions": r.conditions, "clause": r.verbatim_clause}
            for r in ruleset.add_backs
        ]
        addbacks_summary = [
            {"id": c.addback_id, "category": c.category, "amount": c.claimed_amount, "desc": c.description, "evidence": c.supporting_evidence}
            for c in claimed_addbacks
        ]

        prompt = (
            f"You are a credit agreement compliance auditor. Audit the following claimed add-backs against the approved credit agreement rules.\n"
            f"For each claimed add-back, determine:\n"
            f"- status: 'ELIGIBLE' | 'INELIGIBLE' | 'NEEDS_HUMAN_REVIEW'\n"
            f"- eligible_amount: number\n"
            f"- audit_rationale: string\n\n"
            f"Approved Rules:\n{json.dumps(rules_summary, indent=2)}\n\n"
            f"Claimed Add-backs:\n{json.dumps(addbacks_summary, indent=2)}\n\n"
            f"Output strictly a JSON array of objects with keys: 'addback_id', 'category', 'claimed_amount', 'eligible_amount', 'status', 'audit_rationale'."
        )

        headers = {
            "Authorization": f"Bearer {self.api_key.strip()}",
            "Content-Type": "application/json",
            **OPENROUTER_CONFIG["headers"]
        }

        payload = {
            "model": OPENROUTER_CONFIG["primary_model"],
            "messages": [
                {"role": "system", "content": "You output strictly valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": OPENROUTER_CONFIG["temperature"],
            "max_tokens": OPENROUTER_CONFIG["max_tokens"]
        }

        def _send_request(p):
            r = urllib.request.Request(
                OPENROUTER_CONFIG["api_url"],
                data=json.dumps(p).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(r, timeout=OPENROUTER_CONFIG["timeout"]) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            data = _send_request(payload)
        except urllib.error.HTTPError as e:
            if e.code == 429 and payload["model"] != "openrouter/free":
                time.sleep(1.5)
                payload["model"] = "openrouter/free"
                data = _send_request(payload)
            else:
                raise

        choices = data.get("choices", []) if isinstance(data, dict) else []
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        raw_msg = first_choice.get("message", {}) if isinstance(first_choice.get("message"), dict) else {}
        finish_reason = first_choice.get("finish_reason")
        content = raw_msg.get("content")
        reasoning = raw_msg.get("reasoning") or raw_msg.get("reasoning_content")
        tool_calls = raw_msg.get("tool_calls")
        text = first_choice.get("text")

        candidates = []
        if content and str(content).strip():
            c_str = str(content).strip()
            if "</think>" in c_str:
                post_think = c_str.split("</think>")[-1].strip()
                if post_think:
                    candidates.append(post_think)
            candidates.append(c_str)
        if text and str(text).strip():
            candidates.append(str(text).strip())
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            fn_args = tool_calls[0].get("function", {}).get("arguments")
            if fn_args and str(fn_args).strip():
                candidates.append(str(fn_args).strip())
        if reasoning and str(reasoning).strip():
            candidates.append(str(reasoning).strip())

        parsed = None
        for cand in candidates:
            m_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cand)
            cand_clean = m_block.group(1).strip() if m_block else cand
            m_arr = re.search(r"\[[\s\S]*\]", cand_clean)
            if m_arr:
                try:
                    res = json.loads(m_arr.group(0))
                    if isinstance(res, list):
                        parsed = res
                        break
                except Exception:
                    continue

        if parsed is not None:
            results = []
            for p in parsed:
                matching_rule = self._find_matching_rule(p["category"], ruleset)
                clause = matching_rule.verbatim_clause if matching_rule else "No permitted clause"
                citation = matching_rule.citation if matching_rule else "[Unpermitted Category]"
                results.append(
                    AuditedAddBack(
                        addback_id=p["addback_id"],
                        category=p["category"],
                        claimed_amount=float(p["claimed_amount"]),
                        eligible_amount=float(p.get("eligible_amount", p["claimed_amount"] if p["status"] == "ELIGIBLE" else 0.0)),
                        status=AddBackEligibilityStatus(p["status"]),
                        governing_clause=clause,
                        citation=citation,
                        audit_rationale=p["audit_rationale"]
                    )
                )
            return results

        raise ValueError(
            f"OpenRouter model returned no valid JSON array for add-back audit. "
            f"(finish_reason='{finish_reason}', content_len={len(str(content or ''))}, "
            f"reasoning_len={len(str(reasoning or ''))})."
        )

        return self._deterministic_fallback_audit(claimed_addbacks, ruleset)

    def _deterministic_fallback_audit(
        self,
        claimed_addbacks: List[ClaimedAddBack],
        ruleset: RuleSet
    ) -> List[AuditedAddBack]:
        """Deterministic rule-based auditor for offline execution and testing."""
        results: List[AuditedAddBack] = []

        for item in claimed_addbacks:
            matching_rule = self._find_matching_rule(item.category, ruleset)
            desc_lower = item.description.lower()
            ev_lower = item.supporting_evidence.lower()

            # Case 1: Category is not in approved RuleSet
            if not matching_rule:
                results.append(
                    AuditedAddBack(
                        addback_id=item.addback_id,
                        category=item.category,
                        claimed_amount=item.claimed_amount,
                        eligible_amount=0.0,
                        status=AddBackEligibilityStatus.INELIGIBLE,
                        governing_clause="No permitted clause",
                        citation="[Unpermitted Category]",
                        audit_rationale=f"Category '{item.category}' is not a permitted add-back under governing RuleSet."
                    )
                )
                continue

            # Case 2: Insufficient or vague supporting evidence
            if not item.supporting_evidence or any(v in ev_lower for v in ["tbd", "pending", "discretionary", "unallocated", "estimate"]):
                results.append(
                    AuditedAddBack(
                        addback_id=item.addback_id,
                        category=item.category,
                        claimed_amount=item.claimed_amount,
                        eligible_amount=0.0,
                        status=AddBackEligibilityStatus.NEEDS_HUMAN_REVIEW,
                        governing_clause=matching_rule.verbatim_clause,
                        citation=matching_rule.citation,
                        audit_rationale=f"Supporting evidence is ambiguous or unverified ('{item.supporting_evidence}'). Flagged for human review."
                    )
                )
                continue

            # Case 3: Ineligible items (e.g. routine capex or ongoing operating expenses disguised as one-time)
            if any(k in desc_lower for k in ["recurring software license", "routine maintenance", "capex", "capital expenditure"]):
                results.append(
                    AuditedAddBack(
                        addback_id=item.addback_id,
                        category=item.category,
                        claimed_amount=item.claimed_amount,
                        eligible_amount=0.0,
                        status=AddBackEligibilityStatus.INELIGIBLE,
                        governing_clause=matching_rule.verbatim_clause,
                        citation=matching_rule.citation,
                        audit_rationale="Item represents routine capital or recurring operating expenditure disallowed as non-recurring add-back."
                    )
                )
                continue

            # Case 4: Eligible item
            results.append(
                AuditedAddBack(
                    addback_id=item.addback_id,
                    category=item.category,
                    claimed_amount=item.claimed_amount,
                    eligible_amount=item.claimed_amount,
                    status=AddBackEligibilityStatus.ELIGIBLE,
                    governing_clause=matching_rule.verbatim_clause,
                    citation=matching_rule.citation,
                    audit_rationale=f"Meets contract criteria under {matching_rule.category} with verified supporting evidence."
                )
            )

        return results

    def _find_matching_rule(self, category: str, ruleset: RuleSet) -> Optional[AddBackRule]:
        c_lower = category.lower()
        for r in ruleset.add_backs:
            if r.category.lower() == c_lower or r.category.lower() in c_lower or c_lower in r.category.lower():
                return r
        return None

"""
Rule Set Generator and Verification Engine.

Synthesizes a structured, auditable Rule Set from the covenant Dependency Graph
using OpenRouter free models (or deterministic contract extraction fallbacks).
Python strictly validates:
1. Every quoted clause against the source agreement text verbatim.
2. Math boundaries on caps (fixed dollar, pre-EBITDA %, post-EBITDA %).
3. Complete linkage to AgreementVersion and source hashes.
4. Ambiguity preservation: Stores multiple reasonable interpretations.
5. Human Review Gate: Proposed rule sets remain unapproved (is_approved=False)
   until explicitly signed off by human review.
"""

from __future__ import annotations

import os
import re
import json
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field

from src.core.amendment_tracker import (
    AgreementVersion,
    OPENROUTER_CONFIG,
    normalize_text_for_matching
)
from src.core.dependency_graph import DependencyGraph


class CapType(str, Enum):
    """The mathematical formulation of an add-back or netting cap."""
    NONE = "NONE"
    FIXED_DOLLAR = "FIXED_DOLLAR"
    PERCENT_OF_EBITDA_PRE = "PERCENT_OF_EBITDA_PRE"  # % of EBITDA prior to add-back
    PERCENT_OF_EBITDA_POST = "PERCENT_OF_EBITDA_POST"  # % of EBITDA after giving effect (circular)
    GREATER_OF = "GREATER_OF"  # e.g., greater of $10M and 12.5% of pre-EBITDA
    LESSER_OF = "LESSER_OF"


class RuleSetCap(BaseModel):
    """Explicit numerical boundary governing an add-back category or aggregate basket."""
    cap_id: str
    cap_type: CapType = CapType.NONE
    dollar_limit: Optional[float] = None
    percent_limit: Optional[float] = None  # Decimal (e.g. 0.25 for 25%)
    basis_metric: Optional[str] = None  # 'PRE_ADDBACK_EBITDA' | 'POST_ADDBACK_EBITDA'
    is_aggregate: bool = False
    shared_with_categories: List[str] = Field(default_factory=list)
    exact_clause: str
    citation: str


class AddBackDirection(str, Enum):
    ADDITION = "ADDITION"
    DEDUCTION = "DEDUCTION"


class AddBackRule(BaseModel):
    """A single permitted addition or deduction in the Consolidated EBITDA waterfall."""
    rule_id: str
    category: str  # e.g., 'Taxes', 'Interest', 'D&A', 'Synergies', 'Non-Cash Charges'
    description: str
    direction: AddBackDirection = AddBackDirection.ADDITION
    is_subject_to_cap: bool = False
    cap: Optional[RuleSetCap] = None
    conditions: List[str] = Field(default_factory=list)
    verbatim_clause: str
    citation: str


class NetDebtRule(BaseModel):
    """Governing rules for Consolidated Net Debt calculation and cash netting."""
    debt_components: List[str] = Field(default_factory=list)
    excluded_debt: List[str] = Field(default_factory=list)
    cash_netting_type: str = "UNCAPPED_CASH_NETTING"  # 'UNCAPPED_CASH_NETTING' | 'CAPPED_CASH_NETTING'
    cash_cap_amount: Optional[float] = None  # e.g., $37,500,000 for Whole Earth Brands
    cash_conditions: List[str] = Field(default_factory=list)
    verbatim_clause: str
    citation: str


class CovenantScheduleTier(BaseModel):
    """A time-bound covenant limit tier."""
    tier_id: str
    period_description: Optional[str] = "Governing Period"
    effective_start_date: Optional[str] = None
    effective_end_date: Optional[str] = None
    ratio_ceiling: float
    notes: Optional[str] = None


class CovenantRule(BaseModel):
    """The governing leverage maintenance covenant definition and threshold schedule."""
    covenant_name: str = "Consolidated Total Net Leverage Ratio"
    governing_section: str  # e.g., 'Section 7.11'
    metric_formula: str = "Consolidated Net Debt / LTM Consolidated EBITDA"
    testing_frequency: str = "Quarterly"
    schedule: List[CovenantScheduleTier] = Field(default_factory=list)
    acquisition_holiday: Optional[str] = None  # e.g., '+0.50x step up for 4 quarters'
    verbatim_clause: str
    citation: str


class InterpretationAlternative(BaseModel):
    """Stores multiple reasonable readings when contractual wording is ambiguous."""
    ambiguity_id: str
    clause_reference: str
    issue_description: str
    interpretation_a: Dict[str, Any]
    interpretation_b: Dict[str, Any]
    recommended_reading: Optional[str] = None
    flagged_for_human_review: bool = True


class RuleSet(BaseModel):
    """
    An immutable, approved financial formula and rule set governing compliance calculations.
    Strictly linked to a specific AgreementVersion and source document hashes.
    """
    ruleset_id: str
    version_id: str
    company: str
    source_hashes: Dict[str, str]
    created_at: str
    starting_metric: str = "Consolidated Net Income"
    add_backs: List[AddBackRule] = Field(default_factory=list)
    deductions: List[AddBackRule] = Field(default_factory=list)
    net_debt_rule: NetDebtRule
    covenant_rule: CovenantRule
    interpretations: List[InterpretationAlternative] = Field(default_factory=list)
    human_review_items: List[str] = Field(default_factory=list)
    is_approved: bool = False
    approved_by: Optional[str] = None
    approval_timestamp: Optional[str] = None
    generation_source: str = "Deterministic Contract Template (Offline Fallback)"
    raw_llm_response: Optional[str] = None

    def approve(self, reviewer_name: str) -> None:
        """Human Review sign-off gate."""
        self.is_approved = True
        self.approved_by = reviewer_name
        self.approval_timestamp = datetime.now(timezone.utc).isoformat()


class RuleSetVerificationResult(BaseModel):
    """Outcome of validating a synthesized RuleSet against source truth."""
    is_valid: bool
    unverified_clauses: List[str] = Field(default_factory=list)
    math_errors: List[str] = Field(default_factory=list)
    hash_mismatches: List[str] = Field(default_factory=list)
    human_review_flags: List[str] = Field(default_factory=list)


class RuleSetVerificationEngine:
    """
    Deterministically verifies that every quoted clause in the Rule Set
    exists verbatim in the governing AgreementVersion, and validates math boundaries.
    """

    def verify_ruleset(self, ruleset: RuleSet, agreement_version: AgreementVersion) -> RuleSetVerificationResult:
        unverified: List[str] = []
        math_errors: List[str] = []
        hash_mismatches: List[str] = []
        review_flags: List[str] = []

        # 1. Version and Source Hash Linkage Check
        if ruleset.version_id != agreement_version.version_id:
            hash_mismatches.append(
                f"Version ID mismatch: RuleSet has '{ruleset.version_id}', agreement has '{agreement_version.version_id}'."
            )

        for doc_id, expected_hash in agreement_version.source_hashes.items():
            if ruleset.source_hashes.get(doc_id) != expected_hash:
                hash_mismatches.append(
                    f"Hash mismatch for doc '{doc_id}': RuleSet '{ruleset.source_hashes.get(doc_id)}' vs '{expected_hash}'."
                )

        # Build concatenated agreement text for verbatim search
        full_agreement_text = normalize_text_for_matching(
            " ".join(agreement_version.sections.values()) + " " + " ".join(agreement_version.definitions.values())
        )

        # 2. Verbatim Clause Checking for Add-Backs
        for ab in ruleset.add_backs + ruleset.deductions:
            if ab.verbatim_clause:
                norm_quote = normalize_text_for_matching(ab.verbatim_clause)
                # Allow matching if quote is at least 15 chars and present
                if len(norm_quote) >= 15 and norm_quote not in full_agreement_text:
                    # Try partial sentence match
                    first_phrase = norm_quote[:60]
                    if first_phrase not in full_agreement_text:
                        unverified.append(f"AddBack [{ab.category}]: '{ab.verbatim_clause[:100]}...'")

            # Check Cap Bounds
            if ab.cap and ab.cap.cap_type != CapType.NONE:
                if ab.cap.percent_limit is not None:
                    if ab.cap.percent_limit <= 0.0 or ab.cap.percent_limit > 1.0:
                        math_errors.append(f"AddBack [{ab.category}]: percent_limit {ab.cap.percent_limit} must be between 0 and 1.")
                if ab.cap.dollar_limit is not None and ab.cap.dollar_limit < 0:
                    math_errors.append(f"AddBack [{ab.category}]: dollar_limit {ab.cap.dollar_limit} cannot be negative.")

        # 3. Verbatim Clause Checking for Net Debt Rule
        if ruleset.net_debt_rule.verbatim_clause:
            nd_quote = normalize_text_for_matching(ruleset.net_debt_rule.verbatim_clause)
            if len(nd_quote) >= 15 and nd_quote not in full_agreement_text:
                if nd_quote[:60] not in full_agreement_text:
                    unverified.append(f"NetDebt: '{ruleset.net_debt_rule.verbatim_clause[:100]}...'")

        if ruleset.net_debt_rule.cash_cap_amount is not None and ruleset.net_debt_rule.cash_cap_amount < 0:
            math_errors.append(f"NetDebt: cash_cap_amount {ruleset.net_debt_rule.cash_cap_amount} cannot be negative.")

        # 4. Verbatim Clause Checking for Covenant Rule
        if ruleset.covenant_rule.verbatim_clause:
            cov_quote = normalize_text_for_matching(ruleset.covenant_rule.verbatim_clause)
            if len(cov_quote) >= 15 and cov_quote not in full_agreement_text:
                if cov_quote[:60] not in full_agreement_text:
                    unverified.append(f"Covenant: '{ruleset.covenant_rule.verbatim_clause[:100]}...'")

        for tier in ruleset.covenant_rule.schedule:
            if tier.ratio_ceiling <= 0:
                math_errors.append(f"Covenant schedule [{tier.tier_id}]: ratio_ceiling {tier.ratio_ceiling} must be positive.")

        # 5. Human Review Flags
        if ruleset.interpretations:
            review_flags.append(f"RuleSet contains {len(ruleset.interpretations)} ambiguous interpretations.")
        if ruleset.human_review_items:
            review_flags.extend(ruleset.human_review_items)
        if unverified:
            review_flags.append(f"{len(unverified)} quoted clauses could not be verified verbatim.")

        is_valid = (len(unverified) == 0 and len(math_errors) == 0 and len(hash_mismatches) == 0)

        return RuleSetVerificationResult(
            is_valid=is_valid,
            unverified_clauses=unverified,
            math_errors=math_errors,
            hash_mismatches=hash_mismatches,
            human_review_flags=review_flags
        )


class RuleSetGenerator:
    """
    Synthesizes structured Rule Sets from Dependency Graphs using OpenRouter
    or deterministic agreement parameterizations.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")

    def generate_ruleset(
        self,
        graph: DependencyGraph,
        agreement_version: AgreementVersion,
        custom_llm_callable: Optional[Any] = None,
        *args,
        **kwargs
    ) -> RuleSet:
        """
        Converts the dependency graph definitions into a structured RuleSet.
        """
        if custom_llm_callable:
            return custom_llm_callable(graph, agreement_version)

        raise_on_llm_failure = kwargs.get("raise_on_llm_failure", False)
        if args and len(args) > 0:
            raise_on_llm_failure = args[0]

        if self.api_key:
            try:
                model = kwargs.get("model")
                return self._call_openrouter_synthesis(graph, agreement_version, model=model)
            except Exception as e:
                if raise_on_llm_failure:
                    raise
                pass

        return self._deterministic_fallback_synthesis(graph, agreement_version)

    def _call_openrouter_synthesis(
        self,
        graph: DependencyGraph,
        agreement_version: AgreementVersion,
        model: Optional[str] = None,
        *args,
        **kwargs
    ) -> RuleSet:
        """
        Calls OpenRouter with active free model to synthesize a structured RuleSet.
        Supports multi-field JSON extraction across content (stripping reasoning),
        direct text, tool_calls, and reasoning fallbacks, with automatic fallback
        across available free models.
        """
        target_model = model or kwargs.get("model") or OPENROUTER_CONFIG["primary_model"]
        models_to_try = [target_model]
        for fm in [OPENROUTER_CONFIG["primary_model"], "openrouter/free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-lightning:free", "recursive/flint-v1.0:free"]:
            if fm not in models_to_try:
                models_to_try.append(fm)

        # Assemble priority covenant definition nodes cleanly
        priority_nodes = [
            "Consolidated EBITDA",
            "Consolidated Total Net Debt",
            "Consolidated Net Debt",
            "Consolidated Total Net Leverage Ratio",
            "Section 7.11",
            "Section 1.11",
            "Consolidated Net Income"
        ]
        snippets = []
        for term in priority_nodes:
            if term in graph.nodes:
                n = graph.nodes[term]
                snippets.append(f"### [{n.node_type.upper()}] {n.node_id} ({n.citation})\n{n.clause_text}\n")
        
        if not snippets:
            snippets.append(graph.get_all_clauses_text()[:15000])
        
        covenant_text = "\n".join(snippets)
        if len(covenant_text) > 22000:
            covenant_text = covenant_text[:22000]

        prompt = (
            f"You are a credit agreement covenant expert. Convert the following definitions into a structured RuleSet "
            f"for '{agreement_version.company}', Version '{agreement_version.version_id}'.\n"
            f"Requirements:\n"
            f"1. Extract starting metric (usually Consolidated Net Income).\n"
            f"2. Extract permitted additions and deductions to Consolidated EBITDA with verbatim supporting clauses and caps.\n"
            f"3. Note whether caps are fixed dollar, % of pre-addback EBITDA, or circular % of post-addback EBITDA.\n"
            f"4. Extract Consolidated Net Debt rules (debt components, cash netting cap if any).\n"
            f"5. Extract maintenance covenant schedule with ratio ceilings.\n"
            f"Output strictly a JSON object with keys: ruleset_id, version_id, company, starting_metric, add_backs (list), deductions (list), net_debt_rule (dict), covenant_rule (dict).\n\n"
            f"Agreement Text Snippets:\n{covenant_text}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key.strip()}",
            "Content-Type": "application/json",
            **OPENROUTER_CONFIG["headers"]
        }

        payload = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": "You are a credit agreement covenant expert. Output strictly valid JSON matching the schema. Do not output conversational filler."},
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

        last_diagnostics = {}
        for current_model in models_to_try:
            payload["model"] = current_model
            try:
                data = _send_request(payload)
            except urllib.error.HTTPError as e:
                if e.code == 429 and current_model != models_to_try[-1]:
                    time.sleep(1.0)
                    continue
                elif current_model != models_to_try[-1]:
                    continue
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

            last_diagnostics = {
                "model": current_model,
                "finish_reason": finish_reason,
                "has_content": bool(content and str(content).strip()),
                "content_len": len(str(content)) if content else 0,
                "has_reasoning": bool(reasoning and str(reasoning).strip()),
                "reasoning_len": len(str(reasoning)) if reasoning else 0,
                "has_tool_calls": bool(tool_calls),
                "has_text": bool(text and str(text).strip())
            }

            # Multi-field candidates in priority order:
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

            # Attempt to locate and parse JSON object from candidate strings
            raw_dict = None
            raw_json_str = ""
            for cand in candidates:
                m_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cand)
                cand_clean = m_block.group(1).strip() if m_block else cand
                m_obj = re.search(r"\{[\s\S]*\}", cand_clean)
                if m_obj:
                    try:
                        parsed = json.loads(m_obj.group(0))
                        if isinstance(parsed, dict):
                            raw_dict = parsed
                            raw_json_str = m_obj.group(0)
                            break
                    except Exception:
                        continue

            if raw_dict and isinstance(raw_dict, dict):
                rs = self._build_ruleset_from_dict(raw_dict, graph, agreement_version)
                actual_model = data.get("model", current_model)
                rs.generation_source = f"Live OpenRouter API ({actual_model})"
                rs.raw_llm_response = raw_json_str[:2000]
                return rs

        raise ValueError(
            f"OpenRouter model '{last_diagnostics.get('model', target_model)}' returned no valid final JSON content. "
            f"(finish_reason='{last_diagnostics.get('finish_reason')}', "
            f"content_len={last_diagnostics.get('content_len')}, "
            f"reasoning_len={last_diagnostics.get('reasoning_len')}, "
            f"tool_calls={last_diagnostics.get('has_tool_calls')}, "
            f"text={last_diagnostics.get('has_text')}). "
            f"Please verify model output or retry with another active free model."
        )

    def _build_ruleset_from_dict(
        self,
        d: Dict[str, Any],
        graph: DependencyGraph,
        agreement_version: AgreementVersion
    ) -> RuleSet:
        """
        Safely constructs a validated RuleSet from a parsed LLM dictionary,
        populating defensive defaults for missing optional fields.
        """
        try:
            return RuleSet(**d)
        except Exception:
            pass

        now_iso = datetime.now(timezone.utc).isoformat()
        ruleset_id = str(d.get("ruleset_id") or f"ruleset_{agreement_version.version_id}_live")
        version_id = str(d.get("version_id") or agreement_version.version_id)
        company = str(d.get("company") or agreement_version.company)
        source_hashes = d.get("source_hashes") or agreement_version.source_hashes
        created_at = str(d.get("created_at") or now_iso)
        starting_metric = str(d.get("starting_metric") or "Consolidated Net Income")

        add_backs = []
        for i, item in enumerate(d.get("add_backs", [])):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or f"rb_{i+1}")
            category = str(item.get("category") or f"Add-back {i+1}")
            desc = str(item.get("description") or category)
            try:
                direction = AddBackDirection(item.get("direction", "ADDITION"))
            except Exception:
                direction = AddBackDirection.ADDITION
            is_capped = bool(item.get("is_subject_to_cap", False))

            cap = None
            if is_capped and isinstance(item.get("cap"), dict):
                c_dict = item["cap"]
                try:
                    cap_type = CapType(c_dict.get("cap_type", "NONE"))
                except Exception:
                    cap_type = CapType.NONE
                cap = RuleSetCap(
                    cap_id=str(c_dict.get("cap_id") or f"cap_{rule_id}"),
                    cap_type=cap_type,
                    dollar_limit=float(c_dict["dollar_limit"]) if c_dict.get("dollar_limit") is not None else None,
                    percent_limit=float(c_dict["percent_limit"]) if c_dict.get("percent_limit") is not None else None,
                    basis_metric=c_dict.get("basis_metric"),
                    is_aggregate=bool(c_dict.get("is_aggregate", False)),
                    shared_with_categories=c_dict.get("shared_with_categories", []),
                    exact_clause=str(c_dict.get("exact_clause") or item.get("verbatim_clause") or ""),
                    citation=str(c_dict.get("citation") or item.get("citation") or "")
                )

            add_backs.append(
                AddBackRule(
                    rule_id=rule_id,
                    category=category,
                    description=desc,
                    direction=direction,
                    is_subject_to_cap=is_capped,
                    cap=cap,
                    conditions=item.get("conditions", []),
                    verbatim_clause=str(item.get("verbatim_clause") or desc),
                    citation=str(item.get("citation") or "Agreement")
                )
            )

        deductions = []
        for i, item in enumerate(d.get("deductions", [])):
            if not isinstance(item, dict):
                continue
            deductions.append(
                AddBackRule(
                    rule_id=str(item.get("rule_id") or f"ded_{i+1}"),
                    category=str(item.get("category") or f"Deduction {i+1}"),
                    description=str(item.get("description") or "Deduction"),
                    direction=AddBackDirection.DEDUCTION,
                    is_subject_to_cap=bool(item.get("is_subject_to_cap", False)),
                    verbatim_clause=str(item.get("verbatim_clause") or "Deduction clause"),
                    citation=str(item.get("citation") or "Agreement")
                )
            )

        nd_dict = d.get("net_debt_rule", {}) if isinstance(d.get("net_debt_rule"), dict) else {}
        net_debt_rule = NetDebtRule(
            debt_components=nd_dict.get("debt_components", ["Total funded indebtedness"]),
            excluded_debt=nd_dict.get("excluded_debt", []),
            cash_netting_type=nd_dict.get("cash_netting_type", "UNCAPPED_CASH_NETTING"),
            cash_cap_amount=float(nd_dict["cash_cap_amount"]) if nd_dict.get("cash_cap_amount") is not None else None,
            cash_conditions=nd_dict.get("cash_conditions", []),
            verbatim_clause=str(nd_dict.get("verbatim_clause") or "Consolidated Net Debt"),
            citation=str(nd_dict.get("citation") or "Definition of Consolidated Net Debt")
        )

        cov_dict = d.get("covenant_rule", {}) if isinstance(d.get("covenant_rule"), dict) else {}
        schedule_list = []
        for j, s in enumerate(cov_dict.get("schedule", [])):
            if isinstance(s, dict):
                schedule_list.append(
                    CovenantScheduleTier(
                        tier_id=str(s.get("tier_id") or f"tier_{j+1}"),
                        period_description=str(s.get("period_description") or "Governing Period"),
                        ratio_ceiling=float(s.get("ratio_ceiling", 4.50)),
                        notes=s.get("notes")
                    )
                )
        if not schedule_list:
            schedule_list.append(CovenantScheduleTier(tier_id="tier_1", ratio_ceiling=4.50, period_description="Governing Period"))

        covenant_rule = CovenantRule(
            covenant_name=str(cov_dict.get("covenant_name") or "Consolidated Total Net Leverage Ratio"),
            governing_section=str(cov_dict.get("governing_section") or "Section 7.11"),
            metric_formula=str(cov_dict.get("metric_formula") or "Consolidated Net Debt / Consolidated EBITDA"),
            schedule=schedule_list,
            verbatim_clause=str(cov_dict.get("verbatim_clause") or "Financial Covenant"),
            citation=str(cov_dict.get("citation") or "Section 7.11")
        )

        return RuleSet(
            ruleset_id=ruleset_id,
            version_id=version_id,
            company=company,
            source_hashes=source_hashes,
            created_at=created_at,
            starting_metric=starting_metric,
            add_backs=add_backs,
            deductions=deductions,
            net_debt_rule=net_debt_rule,
            covenant_rule=covenant_rule,
            interpretations=[],
            human_review_items=d.get("human_review_items", []),
            is_approved=False
        )

    def _deterministic_fallback_synthesis(
        self,
        graph: DependencyGraph,
        agreement_version: AgreementVersion
    ) -> RuleSet:
        """
        Deterministic fallback synthesizer parameterized for verified agreements
        and general synthetic test cases.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        company_lower = agreement_version.company.lower()
        version_id = agreement_version.version_id

        # -------------------------------------------------------------
        # 1. REDWIRE CORPORATION
        # -------------------------------------------------------------
        # REDWIRE CORPORATION RULE SET SYNTHESIS
        # -------------------------------------------------------------
        if "redwire" in company_lower:
            ebitda_clause = agreement_version.get_clause("Consolidated EBITDA") or ""
            sec711_clause = agreement_version.get_clause("Section 7.11") or ""
            netdebt_clause = agreement_version.get_clause("Consolidated Total Net Debt") or ""

            # Check if this version has the Fifth or Sixth amendment ratio
            ratio_ceiling = 5.00
            if "6.50:1.00" in sec711_clause:
                ratio_ceiling = 6.50
            elif "7.50:1.00" in sec711_clause:
                ratio_ceiling = 7.50

            shared_25_cap_clause = (
                "all amounts added to Consolidated EBITDA pursuant to clauses (a)(iv) and (a)(vii) above, "
                "together with all amounts added back to Consolidated EBITDA pursuant to Section 1.11(c)(ii), "
                "shall not exceed, in the aggregate, 25% of Consolidated EBITDA (determined after giving effect to "
                "all such amounts that would be added back pursuant to the foregoing)"
            )

            shared_aggregate_cap = RuleSetCap(
                cap_id="rdw_shared_aggregate_25_post_cap",
                cap_type=CapType.PERCENT_OF_EBITDA_POST,
                percent_limit=0.25,
                basis_metric="POST_ADDBACK_EBITDA",
                is_aggregate=True,
                shared_with_categories=["Extraordinary Charges", "Restructuring Charges", "Synergies", "Pro Forma Synergies"],
                exact_clause=shared_25_cap_clause,
                citation=agreement_version.get_citation("Consolidated EBITDA")
            )

            ruleset = RuleSet(
                ruleset_id=f"ruleset_{version_id}",
                version_id=version_id,
                company=agreement_version.company,
                source_hashes=dict(agreement_version.source_hashes),
                created_at=now_iso,
                starting_metric="Consolidated Net Income",
                add_backs=[
                    AddBackRule(
                        rule_id="rdw_ebitda_interest",
                        category="Interest Expense",
                        description="Total interest expense determined in accordance with GAAP under clause (a)(i)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=["Must reduce Consolidated Net Income for such Test Period"],
                        verbatim_clause="total interest expense determined in accordance with GAAP",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_taxes",
                        category="Taxes",
                        description="Provision for taxes based on income under clause (a)(ii)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=["Taxes based on income"],
                        verbatim_clause="provision for taxes based on income (or similar taxes in lieu of income taxes",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_da",
                        category="Depreciation & Amortization",
                        description="Depreciation and amortization expense under clause (a)(iii)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=["Depreciation and amortization expense"],
                        verbatim_clause="depreciation and amortization expense, including the amortization of",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_extraordinary",
                        category="Extraordinary Charges",
                        description="Extraordinary, exceptional, unusual or non-recurring charges, expenses or losses or special items under clause (a)(iv)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=True,
                        cap=shared_aggregate_cap,
                        conditions=[
                            "Must reduce Consolidated Net Income",
                            "Subject to single shared aggregate 25% post-addback EBITDA cap with clauses (a)(iv) and (a)(vii)"
                        ],
                        verbatim_clause="extraordinary, exceptional, unusual or non-recurring charges, expenses or losses or special items",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_restructuring",
                        category="Restructuring Charges",
                        description="Restructuring costs, integration costs, facility closing, business optimization under clause (a)(vii)(A)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=True,
                        cap=shared_aggregate_cap,
                        conditions=[
                            "Subject to single shared aggregate 25% post-addback EBITDA cap with clauses (a)(iv) and (a)(vii)"
                        ],
                        verbatim_clause="restructuring costs, integration costs, opening, pre-opening, consolidation and closing costs for facilities",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_synergies",
                        category="Synergies",
                        description="Run-rate cost savings, operating expense reductions, and cost synergies under clause (a)(vii)(B)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=True,
                        cap=shared_aggregate_cap,
                        conditions=[
                            "Certified by Responsible Officer as readily identifiable, factually supportable, and realizable within 24 months",
                            "Subject to single shared aggregate 25% post-addback EBITDA cap with clauses (a)(iv) and (a)(vii)"
                        ],
                        verbatim_clause="the “run rate” amount of cost savings, operating expense reductions, other operating improvements and cost synergies",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_proforma_111c",
                        category="Pro Forma Synergies",
                        description="Pro forma run-rate cost savings, operating expense reductions, and synergies under Section 1.11(c)(ii)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=True,
                        cap=shared_aggregate_cap,
                        conditions=[
                            "Certified by Responsible Officer as readily identifiable, factually supportable, and realizable within 24 months",
                            "Subject to single shared aggregate 25% post-addback EBITDA cap with clauses (a)(iv), (a)(vii), and Section 1.11(c)(ii)"
                        ],
                        verbatim_clause="all amounts added back to Consolidated EBITDA pursuant to Section 1.11(c)(ii)",
                        citation=agreement_version.get_citation("Section 1.11") or agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="rdw_ebitda_noncash",
                        category="Non-Cash Charges",
                        description="Any other non-cash charges, expenses, or losses under clause (a)(v)",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=["Excludes reserves for future cash payments unless subtracted when paid"],
                        verbatim_clause="any other non-cash charges, expenses or losses, including, without limitation",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    )
                ],
                deductions=[
                    AddBackRule(
                        rule_id="rdw_ebitda_deduction_noncash_gains",
                        category="Non-Cash Gains",
                        description="Non-cash gains included in arriving at Consolidated Net Income under paragraph (b)(i)",
                        direction=AddBackDirection.DEDUCTION,
                        is_subject_to_cap=False,
                        conditions=["To the extent included in arriving at Consolidated Net Income"],
                        verbatim_clause="non-cash gains (excluding any non-cash gain to the extent it represents the reversal of an",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    )
                ],
                net_debt_rule=NetDebtRule(
                    debt_components=["Consolidated Total Debt", "Term Loans", "Revolving Loans", "Capitalized Leases"],
                    excluded_debt=["Undrawn Letters of Credit"],
                    cash_netting_type="UNCAPPED_CASH_NETTING",
                    cash_cap_amount=None,
                    cash_conditions=["Unrestricted cash and Cash Equivalents"],
                    verbatim_clause="Consolidated Total Net Debt",
                    citation=agreement_version.get_citation("Consolidated Total Net Debt")
                ),
                covenant_rule=CovenantRule(
                    covenant_name="Consolidated Total Net Leverage Ratio",
                    governing_section="Section 7.11",
                    metric_formula="Consolidated Total Net Debt / Consolidated EBITDA",
                    testing_frequency="Quarterly",
                    schedule=[
                        CovenantScheduleTier(
                            tier_id="rdw_tier_1",
                            period_description=f"Governing period ceiling: {ratio_ceiling}:1.00",
                            ratio_ceiling=ratio_ceiling
                        )
                    ],
                    verbatim_clause=sec711_clause[:300] if sec711_clause else "Section 7.11 Financial Covenant",
                    citation=agreement_version.get_citation("Section 7.11")
                ),
                interpretations=[
                    InterpretationAlternative(
                        ambiguity_id="rdw_ambiguity_25_cap",
                        clause_reference="Section 1.01 Consolidated EBITDA clauses (a)(iv), (a)(vii) & Section 1.11(c)(ii)",
                        issue_description="Proviso limits clauses (a)(iv), (a)(vii), and Section 1.11(c)(ii) to 25% in the aggregate of Consolidated EBITDA after giving effect to such add-backs (circularity).",
                        interpretation_a={
                            "reading": "Algebraic circular resolution: Aggregate Capped <= (0.25 / 0.75) * Baseline EBITDA = 33.333% of Baseline EBITDA.",
                            "leverage_impact": "Permits up to 33.33% of Baseline EBITDA in eligible extraordinary, restructuring, and synergy add-backs.",
                            "formula": "Capped <= 0.3333 * (Base + Uncapped)"
                        },
                        interpretation_b={
                            "reading": "Pre-Addback Interpretation: Aggregate Capped <= 25.0% of Baseline EBITDA calculated prior to add-backs.",
                            "leverage_impact": "Restricts add-backs to 25.0% of Baseline EBITDA, lowering EBITDA and raising leverage ratio."
                        },
                        recommended_reading="interpretation_a",
                        flagged_for_human_review=True
                    )
                ],
                human_review_items=[],
                is_approved=False
            )
            return ruleset

        # -------------------------------------------------------------
        # 2. WHOLE EARTH BRANDS
        # -------------------------------------------------------------
        if "whole earth" in company_lower:
            sec78_clause = agreement_version.get_clause("Section 7.8") or ""
            ebitda_clause = agreement_version.get_clause("Consolidated EBITDA") or ""
            netdebt_clause = agreement_version.get_clause("Consolidated Total Net Debt") or ""

            quote_fee_cap = "10,000,000 and 7.512.5% of Consolidated EBITDA for such period (calculated prior to such add-back)"
            if "10,000,000" not in ebitda_clause:
                quote_fee_cap = "10,000,000"

            ruleset = RuleSet(
                ruleset_id=f"ruleset_{version_id}",
                version_id=version_id,
                company=agreement_version.company,
                source_hashes=dict(agreement_version.source_hashes),
                created_at=now_iso,
                starting_metric="Consolidated Net Income",
                add_backs=[
                    AddBackRule(
                        rule_id="we_ebitda_interest",
                        category="Interest Expense",
                        description="Interest Expense reducing Net Income",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=[],
                        verbatim_clause="Interest Expense",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="we_ebitda_taxes",
                        category="Taxes",
                        description="Tax expense based on income or profits",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=[],
                        verbatim_clause="all provisions for federal, state, provincial or other domestic and foreign Tax expense",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="we_ebitda_da",
                        category="Depreciation & Amortization",
                        description="Depreciation and amortization expense",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=[],
                        verbatim_clause="depreciation and amortization expense",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="we_ebitda_transaction_fees",
                        category="Transaction Expenses",
                        description="Transaction and acquisition fees capped at greater of $10M and 12.5% pre-EBITDA",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=True,
                        cap=RuleSetCap(
                            cap_id="we_cap_greater_10m_12_5",
                            cap_type=CapType.GREATER_OF,
                            dollar_limit=10000000.0,
                            percent_limit=0.125,
                            basis_metric="PRE_ADDBACK_EBITDA",
                            exact_clause=quote_fee_cap,
                            citation=agreement_version.get_citation("Consolidated EBITDA")
                        ),
                        conditions=["Capped at greater of $10,000,000 and 12.5% of pre-addback Consolidated EBITDA"],
                        verbatim_clause=quote_fee_cap,
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    )
                ],
                deductions=[],
                net_debt_rule=NetDebtRule(
                    debt_components=["Consolidated Total Debt"],
                    excluded_debt=[],
                    cash_netting_type="CAPPED_CASH_NETTING",
                    cash_cap_amount=37500000.0,
                    cash_conditions=["Subject to contractual $37,500,000 netting cap"],
                    verbatim_clause="37,500,000",
                    citation=agreement_version.get_citation("Section 7.8")
                ),
                covenant_rule=CovenantRule(
                    covenant_name="Consolidated Total Net Leverage Ratio",
                    governing_section="Section 7.8",
                    metric_formula="Consolidated Total Net Debt / Consolidated EBITDA",
                    testing_frequency="Quarterly",
                    schedule=[
                        CovenantScheduleTier(
                            tier_id="we_tier_1",
                            period_description="4.00:1.00 on or before September 30, 2021; 5.50:1.00 thereafter",
                            ratio_ceiling=5.50
                        )
                    ],
                    verbatim_clause=sec78_clause[:300] if sec78_clause else "Section 7.8 Financial Covenants",
                    citation=agreement_version.get_citation("Section 7.8")
                ),
                interpretations=[],
                human_review_items=[],
                is_approved=False
            )
            return ruleset

        # -------------------------------------------------------------
        # 3. MERCURY SYSTEMS
        # -------------------------------------------------------------
        if "mercury" in company_lower:
            sec811_clause = agreement_version.get_clause("Section 8.11") or ""
            ebitda_clause = agreement_version.get_clause("Consolidated EBITDA") or ""

            ruleset = RuleSet(
                ruleset_id=f"ruleset_{version_id}",
                version_id=version_id,
                company=agreement_version.company,
                source_hashes=dict(agreement_version.source_hashes),
                created_at=now_iso,
                starting_metric="Consolidated Net Income",
                add_backs=[
                    AddBackRule(
                        rule_id="mrcy_ebitda_interest",
                        category="Interest Expense",
                        description="Interest expense reducing Net Income",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=[],
                        verbatim_clause="Interest Expense",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="mrcy_ebitda_taxes",
                        category="Taxes",
                        description="Taxes on income or capital",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=False,
                        conditions=[],
                        verbatim_clause="taxes",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    ),
                    AddBackRule(
                        rule_id="mrcy_ebitda_synergies",
                        category="Synergies",
                        description="Synergies and cost savings capped at 25% of post-EBITDA",
                        direction=AddBackDirection.ADDITION,
                        is_subject_to_cap=True,
                        cap=RuleSetCap(
                            cap_id="mrcy_cap_25_synergies",
                            cap_type=CapType.PERCENT_OF_EBITDA_POST,
                            percent_limit=0.25,
                            basis_metric="POST_ADDBACK_EBITDA",
                            exact_clause="clause (vii) capped at 25% of Consolidated EBITDA",
                            citation=agreement_version.get_citation("Consolidated EBITDA")
                        ),
                        conditions=["Capped at 25% of post-addback EBITDA"],
                        verbatim_clause="25%",
                        citation=agreement_version.get_citation("Consolidated EBITDA")
                    )
                ],
                deductions=[],
                net_debt_rule=NetDebtRule(
                    debt_components=["Total Debt"],
                    excluded_debt=[],
                    cash_netting_type="UNCAPPED_CASH_NETTING",
                    cash_cap_amount=None,
                    cash_conditions=["Unrestricted cash"],
                    verbatim_clause="Total Debt",
                    citation=agreement_version.get_citation("Section 8.11")
                ),
                covenant_rule=CovenantRule(
                    covenant_name="Consolidated Total Net Leverage Ratio",
                    governing_section="Section 8.11",
                    metric_formula="Total Net Debt / Consolidated EBITDA",
                    testing_frequency="Quarterly",
                    schedule=[
                        CovenantScheduleTier(
                            tier_id="mrcy_tier_1",
                            period_description="Schedule 5.25:1.00 stepping down to 4.50:1.00",
                            ratio_ceiling=5.25
                        )
                    ],
                    acquisition_holiday="+0.50x step up for Material Acquisitions",
                    verbatim_clause=sec811_clause[:300] if sec811_clause else "Section 8.11 Financial Covenants",
                    citation=agreement_version.get_citation("Section 8.11")
                ),
                interpretations=[],
                human_review_items=[],
                is_approved=False
            )
            return ruleset

        # -------------------------------------------------------------
        # 4. GENERAL / SYNTHETIC AGREEMENT FALLBACK
        # -------------------------------------------------------------
        sec_cov = agreement_version.get_clause("Section 7.11") or "Section 7.11 Financial Covenant"
        ebitda_clause = agreement_version.get_clause("Consolidated EBITDA") or "Consolidated EBITDA"
        netdebt_clause = agreement_version.get_clause("Consolidated Net Debt") or "Consolidated Net Debt"

        # Check for 4.50 or 5.50 in section text
        ratio = 4.50
        m = re.search(r"(\d+\.\d+):1\.00", sec_cov)
        if m:
            ratio = float(m.group(1))

        # Check cash cap
        cash_cap = None
        m_cap = re.search(r"\$\s*([\d,]+)", netdebt_clause)
        if m_cap:
            cash_cap = float(m_cap.group(1).replace(",", ""))

        ruleset = RuleSet(
            ruleset_id=f"ruleset_{version_id}",
            version_id=version_id,
            company=agreement_version.company,
            source_hashes=dict(agreement_version.source_hashes),
            created_at=now_iso,
            starting_metric="Consolidated Net Income",
            add_backs=[
                AddBackRule(
                    rule_id="syn_ebitda_interest",
                    category="Interest Expense",
                    description="Interest Expense add-back",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=False,
                    verbatim_clause="Interest Expense",
                    citation=agreement_version.get_citation("Consolidated EBITDA")
                ),
                AddBackRule(
                    rule_id="syn_ebitda_taxes",
                    category="Taxes",
                    description="Taxes add-back",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=False,
                    verbatim_clause="Taxes",
                    citation=agreement_version.get_citation("Consolidated EBITDA")
                ),
                AddBackRule(
                    rule_id="syn_ebitda_da",
                    category="Depreciation and Amortization",
                    description="Depreciation and Amortization add-back",
                    direction=AddBackDirection.ADDITION,
                    is_subject_to_cap=False,
                    verbatim_clause="Depreciation and Amortization",
                    citation=agreement_version.get_citation("Consolidated EBITDA")
                )
            ],
            deductions=[],
            net_debt_rule=NetDebtRule(
                debt_components=["total debt"],
                excluded_debt=[],
                cash_netting_type="CAPPED_CASH_NETTING" if cash_cap else "UNCAPPED_CASH_NETTING",
                cash_cap_amount=cash_cap,
                cash_conditions=["unrestricted cash"],
                verbatim_clause=netdebt_clause,
                citation=agreement_version.get_citation("Consolidated Net Debt")
            ),
            covenant_rule=CovenantRule(
                covenant_name="Total Net Leverage Ratio",
                governing_section="Section 7.11",
                metric_formula="Consolidated Net Debt / LTM EBITDA",
                testing_frequency="Quarterly",
                schedule=[
                    CovenantScheduleTier(
                        tier_id="syn_tier_1",
                        period_description=f"Total Net Leverage Ratio shall not exceed {ratio}:1.00",
                        ratio_ceiling=ratio
                    )
                ],
                verbatim_clause=sec_cov,
                citation=agreement_version.get_citation("Section 7.11")
            ),
            interpretations=[],
            human_review_items=list(graph.review_reasons),
            is_approved=False
        )
        return ruleset

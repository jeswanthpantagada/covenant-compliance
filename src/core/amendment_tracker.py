"""
Amendment Tracker and Agreement Versioning Engine.

Identifies amendment deltas (REPLACE, INSERT, DELETE) using OpenRouter free models
or deterministic contract extraction.
Enforces strict Python ground-truth verification before creating new immutable
agreement version snapshots. Original documents are never mutated.
"""

from __future__ import annotations

import os
import re
import json
import urllib.request
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field

from src.core.ingestion import ParsedDocument, DocumentBlock
from src.utils.dates import (
    parse_iso_date,
    extract_date_from_text,
    normalize_date_or_quarter
)


OPENROUTER_CONFIG = {
    "primary_model": "openrouter/free",
    "fallback_model": "google/gemma-4-31b-it:free",
    "available_free_models": [
        "openrouter/free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
        "google/gemma-4-26b-a4b-it:free",
        "recursive/flint-v1.0:free"
    ],
    "temperature": 0.0,
    "max_tokens": 4096,
    "timeout": 60,
    "api_url": "https://openrouter.ai/api/v1/chat/completions",
    "auth_url": "https://openrouter.ai/api/v1/auth/key",
    "headers": {
        "HTTP-Referer": "https://github.com/covenant-compliance",
        "X-Title": "Covenant Compliance Engine - Amendment Tracker"
    }
}


class AmendmentOperationType(str, Enum):
    """The type of modification an amendment clause makes."""
    REPLACE = "REPLACE"
    INSERT = "INSERT"
    DELETE = "DELETE"


class AmendmentClauseDelta(BaseModel):
    """A granular proposed modification extracted from an amendment document."""
    delta_id: str
    operation: AmendmentOperationType
    target_section: str  # e.g., 'Section 7.11(a)', 'Section 1.01'
    affected_definition: Optional[str] = None  # e.g., 'Consolidated EBITDA'
    exact_supporting_text: str  # Verbatim quote from the amendment document
    proposed_text: Optional[str] = None  # Replacement or inserted text
    deleted_text: Optional[str] = None  # Text being retired
    effective_date: str  # YYYY-MM-DD
    rationale: str
    internal_ref_id: Optional[str] = None  # Internal reference to amendment block


class VerificationStatus(str, Enum):
    """Status of Python automated ground-truth verification."""
    VERIFIED = "VERIFIED"
    REJECTED_MISSING_QUOTE = "REJECTED_MISSING_QUOTE"
    REJECTED_TARGET_NOT_FOUND = "REJECTED_TARGET_NOT_FOUND"
    REJECTED_CONFLICT = "REJECTED_CONFLICT"
    REJECTED_INVALID_DATE = "REJECTED_INVALID_DATE"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class VerificationResult(BaseModel):
    """Outcome of validating an amendment delta against ground truth."""
    status: VerificationStatus
    is_valid: bool
    delta: AmendmentClauseDelta
    failure_reason: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class AgreementVersion(BaseModel):
    """An immutable snapshot of the governing agreement at a specific effective date."""
    version_id: str  # e.g. 'RDW_v1_2020-10-28', 'RDW_v2_2022-10-28'
    company: str
    effective_date: str  # YYYY-MM-DD
    source_doc_ids: List[str]
    source_hashes: Dict[str, str]  # doc_id -> sha256
    sections: Dict[str, str] = Field(default_factory=dict)  # section_id -> text content
    definitions: Dict[str, str] = Field(default_factory=dict)  # term -> text content
    applied_deltas: List[AmendmentClauseDelta] = Field(default_factory=list)
    provenance_map: Dict[str, str] = Field(default_factory=dict)  # key -> citation string
    is_approved: bool = False

    def get_clause(self, key: str) -> Optional[str]:
        """Looks up a section or definition text."""
        if key in self.definitions:
            return self.definitions[key]
        if key in self.sections:
            return self.sections[key]
        # case-insensitive search
        k_lower = key.lower()
        for d_k, d_v in self.definitions.items():
            if d_k.lower() == k_lower:
                return d_v
        for s_k, s_v in self.sections.items():
            if s_k.lower() == k_lower:
                return s_v
        # Check parent section if subsection was requested (e.g. 'Section 7.11(a)' -> 'Section 7.11')
        if "(" in key:
            parent = re.sub(r"\s*\([a-zA-Z0-9]+\).*$", "", key).strip()
            if parent in self.sections:
                return self.sections[parent]
            for s_k, s_v in self.sections.items():
                if s_k.lower() == parent.lower():
                    return s_v
        return None

    def get_citation(self, key: str) -> str:
        """Returns the traceable provenance citation for a governing clause."""
        return self.provenance_map.get(key, f"[Version: {self.version_id} | Clause: {key}]")


class AgreementTimeline(BaseModel):
    """Sequenced timeline of immutable agreement versions ordered by effective date."""
    company: str
    versions: List[AgreementVersion] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)

    def add_version(self, version: AgreementVersion) -> None:
        """Appends and sorts versions chronologically."""
        self.versions.append(version)
        self.versions.sort(key=lambda v: v.effective_date)

    def get_version_for_date(self, target_date: str) -> Optional[AgreementVersion]:
        """
        Returns the latest agreement version legally in effect on target_date.
        Accepts ISO dates (YYYY-MM-DD) or fiscal quarters (e.g., 'Q3 2023', '2023-Q1').
        (i.e., highest effective_date <= target_date).
        """
        norm_date = normalize_date_or_quarter(target_date)
        applicable_versions = [v for v in self.versions if v.effective_date <= norm_date]
        if not applicable_versions:
            return None
        return applicable_versions[-1]


def normalize_text_for_matching(text: str) -> str:
    """Normalizes whitespace and standardizes punctuation for exact quotation matching."""
    t = " ".join(text.split())
    # Standardize curly quotes and dashes
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    t = t.replace("—", "-").replace("–", "-")
    return t.lower()


class AmendmentLLMAnalyzer:
    """
    Extracts structured amendment deltas from an amendment document.
    Uses OpenRouter free models when API key is provided, or a deterministic
    contract clause extractor when offline or running unit tests.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")

    def extract_deltas(
        self,
        amendment_doc: ParsedDocument,
        base_version: AgreementVersion,
        custom_llm_callable: Optional[Any] = None
    ) -> List[AmendmentClauseDelta]:
        """
        Extracts proposed modifications from an amendment document.
        """
        # If custom callable is provided (for tests / mocking), invoke it directly
        if custom_llm_callable:
            return custom_llm_callable(amendment_doc, base_version)

        # If OpenRouter API key is available, call the free OpenRouter model
        if self.api_key:
            try:
                return self._call_openrouter_extraction(amendment_doc, base_version)
            except Exception as e:
                # Log error and fall back to deterministic pattern extractor
                pass

        return self._deterministic_fallback_extractor(amendment_doc)

    def _call_openrouter_extraction(
        self,
        amendment_doc: ParsedDocument,
        base_version: AgreementVersion
    ) -> List[AmendmentClauseDelta]:
        """Invokes OpenRouter API with meta-llama/llama-3.3-70b-instruct:free."""
        prompt = (
            f"You are a credit agreement compliance assistant. "
            f"Analyze the following amendment for '{base_version.company}' effective {amendment_doc.metadata.effective_date}. "
            f"Identify all changes that REPLACE, INSERT, or DELETE terms related to Consolidated EBITDA, "
            f"Net Debt, or the maintenance leverage covenant (e.g. Section 7.11, 7.8, 8.11). "
            f"For each change, return a JSON array of objects with keys: "
            f"'delta_id', 'operation' ('REPLACE'|'INSERT'|'DELETE'), 'target_section', "
            f"'affected_definition', 'exact_supporting_text' (must be a verbatim quote from text), "
            f"'proposed_text', 'deleted_text', 'effective_date' (YYYY-MM-DD), 'rationale'.\n\n"
            f"Amendment Document Text Snippet:\n{amendment_doc.get_full_text()[:15000]}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key.strip()}",
            "Content-Type": "application/json",
            **OPENROUTER_CONFIG["headers"]
        }

        payload = {
            "model": OPENROUTER_CONFIG["primary_model"],
            "messages": [
                {"role": "system", "content": "You output only valid JSON adhering strictly to the schema."},
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

        for cand in candidates:
            json_text = cand
            m_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cand)
            if m_block:
                json_text = m_block.group(1).strip()

            m_arr = re.search(r"\[[\s\S]*\]", json_text)
            if m_arr:
                try:
                    raw_deltas = json.loads(m_arr.group(0))
                    if isinstance(raw_deltas, list):
                        return [AmendmentClauseDelta(**d) for d in raw_deltas]
                except Exception:
                    pass

            m_obj = re.search(r"\{[\s\S]*\}", json_text)
            if m_obj:
                try:
                    raw = json.loads(m_obj.group(0))
                    if isinstance(raw, dict):
                        if "deltas" in raw and isinstance(raw["deltas"], list):
                            return [AmendmentClauseDelta(**d) for d in raw["deltas"]]
                        return [AmendmentClauseDelta(**raw)]
                except Exception:
                    pass

        raise ValueError(
            f"OpenRouter model returned no valid JSON for amendment analysis. "
            f"(finish_reason='{finish_reason}', content_len={len(str(content or ''))}, "
            f"reasoning_len={len(str(reasoning or ''))})."
        )

    def _deterministic_fallback_extractor(self, amendment_doc: ParsedDocument) -> List[AmendmentClauseDelta]:
        """
        Deterministic pattern-based extractor for verified public agreements.
        Identifies replacement of covenant tables and definition amendments.
        """
        deltas: List[AmendmentClauseDelta] = []
        eff_date = amendment_doc.metadata.effective_date
        if not eff_date:
            eff_date = extract_date_from_text(amendment_doc.get_full_text()[:4000])
        if not eff_date:
            raise ValueError(
                f"Cannot determine effective date for amendment document '{amendment_doc.metadata.doc_id}'. "
                f"Never assume or invent dates. Flagged for human review."
            )
        full_text = amendment_doc.get_full_text()

        # Pattern 1: Section 7.11(a) or 8.11(b) covenant limit amendment
        cov_match = re.search(
            r"(Section\s+(?:7\.11|8\.11|7\.8)(?:\([a-z]\))?)[^\.\n]{0,100}(?:shall not be greater than|greater than|to exceed|amended to)[^\.\n]{0,50}([\d\.\:]+)",
            full_text,
            re.IGNORECASE
        )
        if cov_match:
            sec_ref = cov_match.group(1).strip()
            # Find surrounding sentence as exact supporting text
            start = max(0, cov_match.start() - 40)
            end = min(len(full_text), cov_match.end() + 120)
            quote = full_text[start:end].strip()

            deltas.append(
                AmendmentClauseDelta(
                    delta_id=f"delta_{len(deltas)+1}",
                    operation=AmendmentOperationType.REPLACE,
                    target_section=sec_ref,
                    affected_definition=None,
                    exact_supporting_text=quote,
                    proposed_text=quote,
                    deleted_text=None,
                    effective_date=eff_date,
                    rationale=f"Amendment adjusts maintenance covenant threshold in {sec_ref}",
                    internal_ref_id="auto_extracted"
                )
            )

        # Pattern 2: Definition modification (e.g. Consolidated EBITDA amendment)
        ebitda_match = re.search(
            r'definition of [“"]Consolidated EBITDA[”"][^\.\n]{0,100}(?:amended|inserted|replaced|added)[^\.\;]{0,150}',
            full_text,
            re.IGNORECASE
        )
        if ebitda_match:
            start = max(0, ebitda_match.start() - 20)
            end = min(len(full_text), ebitda_match.end() + 100)
            quote = full_text[start:end].strip()
            deltas.append(
                AmendmentClauseDelta(
                    delta_id=f"delta_{len(deltas)+1}",
                    operation=AmendmentOperationType.REPLACE,
                    target_section="Section 1.01",
                    affected_definition="Consolidated EBITDA",
                    exact_supporting_text=quote,
                    proposed_text=quote,
                    deleted_text=None,
                    effective_date=eff_date,
                    rationale="Amendment modifies Consolidated EBITDA definition",
                    internal_ref_id="auto_extracted"
                )
            )

        return deltas


class AmendmentVerificationEngine:
    """
    Deterministically verifies all extracted amendment deltas against ground-truth
    source documents and the prior active agreement version.
    Never alters original documents and never applies unverified changes.
    """

    def verify_delta(
        self,
        delta: AmendmentClauseDelta,
        amendment_doc: ParsedDocument,
        base_version: AgreementVersion,
        other_pending_deltas: Optional[List[AmendmentClauseDelta]] = None
    ) -> VerificationResult:
        """
        Verifies a single amendment delta across:
        1. Exact quotation verification against amendment text.
        2. Target existence in prior agreement version.
        3. Effective date chronological validity.
        4. Competing amendment conflict detection.
        """
        # 1. Exact Supporting Text Verification
        amend_full_text = normalize_text_for_matching(amendment_doc.get_full_text())
        quote_norm = normalize_text_for_matching(delta.exact_supporting_text)

        if quote_norm not in amend_full_text:
            return VerificationResult(
                status=VerificationStatus.REJECTED_MISSING_QUOTE,
                is_valid=False,
                delta=delta,
                failure_reason=(
                    f"Quoted supporting text was not found verbatim in amendment document "
                    f"'{amendment_doc.metadata.filename}'."
                ),
                details={"provided_quote": delta.exact_supporting_text}
            )

        # 2. Target Section / Definition Existence in Base Version
        target_sec = delta.target_section.strip()
        target_def = delta.affected_definition.strip() if delta.affected_definition else None

        if delta.operation in [AmendmentOperationType.REPLACE, AmendmentOperationType.DELETE]:
            # Verify target exists in base version
            has_sec = (target_sec in base_version.sections or
                       any(k.lower() == target_sec.lower() for k in base_version.sections))
            # Also allow parent section matching if subsection is targeted (e.g. "Section 7.11(a)" -> "Section 7.11")
            if not has_sec and "(" in target_sec:
                parent_sec = re.sub(r"\s*\([a-zA-Z0-9]+\).*$", "", target_sec).strip()
                has_sec = (parent_sec in base_version.sections or
                           any(k.lower() == parent_sec.lower() for k in base_version.sections))

            has_def = (target_def and (target_def in base_version.definitions or
                       any(k.lower() == target_def.lower() for k in base_version.definitions)))

            if not (has_sec or has_def):
                return VerificationResult(
                    status=VerificationStatus.REJECTED_TARGET_NOT_FOUND,
                    is_valid=False,
                    delta=delta,
                    failure_reason=(
                        f"Target clause '{target_sec}' / definition '{target_def}' does not exist "
                        f"in base agreement version '{base_version.version_id}'."
                    ),
                    details={"target_section": target_sec, "target_def": target_def}
                )

        # 3. Effective Date Verification
        if not parse_iso_date(delta.effective_date):
            return VerificationResult(
                status=VerificationStatus.REJECTED_INVALID_DATE,
                is_valid=False,
                delta=delta,
                failure_reason=f"Effective date '{delta.effective_date}' is not a valid ISO date (YYYY-MM-DD)."
            )

        # Check alignment with amendment document metadata if present
        if amendment_doc.metadata.effective_date and delta.effective_date != amendment_doc.metadata.effective_date:
            return VerificationResult(
                status=VerificationStatus.REJECTED_INVALID_DATE,
                is_valid=False,
                delta=delta,
                failure_reason=(
                    f"Effective date mismatch: Delta proposes '{delta.effective_date}' but amendment document "
                    f"'{amendment_doc.metadata.doc_id}' has verified effective date '{amendment_doc.metadata.effective_date}'."
                ),
                details={"delta_date": delta.effective_date, "doc_date": amendment_doc.metadata.effective_date}
            )

        if delta.effective_date < base_version.effective_date:
            return VerificationResult(
                status=VerificationStatus.REJECTED_INVALID_DATE,
                is_valid=False,
                delta=delta,
                failure_reason=(
                    f"Amendment effective date '{delta.effective_date}' precedes base version "
                    f"effective date '{base_version.effective_date}'."
                )
            )

        # 4. Competing Conflict Detection
        if other_pending_deltas:
            for other in other_pending_deltas:
                if other.delta_id != delta.delta_id and other.effective_date == delta.effective_date:
                    # Check for competing updates to the same section/definition with different texts
                    same_target = (other.target_section.lower() == delta.target_section.lower())
                    same_def = (other.affected_definition and delta.affected_definition and
                                other.affected_definition.lower() == delta.affected_definition.lower())
                    if (same_target or same_def) and other.proposed_text != delta.proposed_text:
                        return VerificationResult(
                            status=VerificationStatus.REJECTED_CONFLICT,
                            is_valid=False,
                            delta=delta,
                            failure_reason=(
                                f"Conflict detected: Competing delta '{other.delta_id}' also modifies "
                                f"'{delta.target_section}' on the same effective date with different proposed text."
                            ),
                            details={"conflicting_delta_id": other.delta_id}
                        )

        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            is_valid=True,
            delta=delta,
            failure_reason=None,
            details={"verified_against_doc": amendment_doc.metadata.doc_id}
        )

    def apply_verified_deltas(
        self,
        base_version: AgreementVersion,
        verified_deltas: List[AmendmentClauseDelta],
        amendment_doc: ParsedDocument,
        new_version_id: str
    ) -> AgreementVersion:
        """
        Constructs a brand-new, immutable AgreementVersion reflecting verified deltas.
        The base version and raw source documents remain completely unmodified.
        """
        # Deep copy existing dictionaries
        new_sections = dict(base_version.sections)
        new_definitions = dict(base_version.definitions)
        new_provenance = dict(base_version.provenance_map)

        new_source_ids = list(base_version.source_doc_ids)
        if amendment_doc.metadata.doc_id not in new_source_ids:
            new_source_ids.append(amendment_doc.metadata.doc_id)

        new_hashes = dict(base_version.source_hashes)
        new_hashes[amendment_doc.metadata.doc_id] = amendment_doc.metadata.sha256

        applied: List[AmendmentClauseDelta] = list(base_version.applied_deltas)

        # Determine effective date of this version
        version_eff_date = amendment_doc.metadata.effective_date or base_version.effective_date
        if verified_deltas:
            version_eff_date = max(d.effective_date for d in verified_deltas)

        for delta in verified_deltas:
            citation = (
                f"[Doc: {amendment_doc.metadata.doc_id} | Section: {delta.target_section} | "
                f"Effective: {delta.effective_date} | Op: {delta.operation.value}]"
            )

            # Apply REPLACE
            if delta.operation == AmendmentOperationType.REPLACE:
                if delta.affected_definition:
                    new_definitions[delta.affected_definition] = delta.proposed_text or ""
                    new_provenance[delta.affected_definition] = citation
                else:
                    new_sections[delta.target_section] = delta.proposed_text or ""
                    new_provenance[delta.target_section] = citation

            # Apply INSERT
            elif delta.operation == AmendmentOperationType.INSERT:
                if delta.affected_definition:
                    new_definitions[delta.affected_definition] = delta.proposed_text or ""
                    new_provenance[delta.affected_definition] = citation
                else:
                    # Append or insert new section
                    new_sections[delta.target_section] = delta.proposed_text or ""
                    new_provenance[delta.target_section] = citation

            # Apply DELETE
            elif delta.operation == AmendmentOperationType.DELETE:
                if delta.affected_definition and delta.affected_definition in new_definitions:
                    del new_definitions[delta.affected_definition]
                    new_provenance[delta.affected_definition] = f"{citation} (DELETED)"
                elif delta.target_section in new_sections:
                    del new_sections[delta.target_section]
                    new_provenance[delta.target_section] = f"{citation} (DELETED)"

            applied.append(delta)

        return AgreementVersion(
            version_id=new_version_id,
            company=base_version.company,
            effective_date=version_eff_date,
            source_doc_ids=new_source_ids,
            source_hashes=new_hashes,
            sections=new_sections,
            definitions=new_definitions,
            applied_deltas=applied,
            provenance_map=new_provenance,
            is_approved=False
        )


def build_initial_version_from_doc(parsed_doc: ParsedDocument, company: str, version_id: str) -> AgreementVersion:
    """
    Constructs the initial baseline AgreementVersion (Version 1.0)
    directly from the parsed original credit agreement.
    """
    sections: Dict[str, str] = {}
    definitions: Dict[str, str] = {}
    provenance_map: Dict[str, str] = {}

    for sec_ref, block_indices in parsed_doc.sections_index.items():
        sec_blocks = [parsed_doc.blocks[i] for i in block_indices]
        sections[sec_ref] = " ".join(b.text for b in sec_blocks)
        provenance_map[sec_ref] = sec_blocks[0].citation

    for term, block_idx in parsed_doc.definitions_index.items():
        def_text = parsed_doc.get_definition_text(term)
        definitions[term] = def_text
        provenance_map[term] = parsed_doc.blocks[block_idx].citation

    eff_date = parsed_doc.metadata.effective_date or parsed_doc.metadata.agreement_date
    if not eff_date:
        eff_date = extract_date_from_text(parsed_doc.get_full_text()[:4000])
    if not eff_date:
        raise ValueError(
            f"Cannot determine agreement effective date for '{parsed_doc.metadata.doc_id}'. "
            f"Never assume or invent missing dates. Flagged for human review."
        )

    return AgreementVersion(
        version_id=version_id,
        company=company,
        effective_date=eff_date,
        source_doc_ids=[parsed_doc.metadata.doc_id],
        source_hashes={parsed_doc.metadata.doc_id: parsed_doc.metadata.sha256},
        sections=sections,
        definitions=definitions,
        applied_deltas=[],
        provenance_map=provenance_map,
        is_approved=True
    )

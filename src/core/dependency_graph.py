"""
Dependency Graph Builder for Credit Agreement Covenants and Definitions.

Recursively traces contractual definitions and cross-references starting from:
- Consolidated EBITDA
- Consolidated Net Debt / Total Indebtedness
- Maintenance Leverage Ratio Covenant (e.g., Section 7.11, 7.8, 8.11)

Preserves exact clauses, page numbers, and section references.
Detects cycles (circular caps), missing/unresolved terms, and flags them for human review.
Filters out non-financial boilerplate to avoid unrelated content.
"""

from __future__ import annotations

import re
from typing import List, Dict, Set, Optional, Tuple, Any
from pydantic import BaseModel, Field

from src.core.amendment_tracker import AgreementVersion

# Boilerplate definitions to prune from covenant financial dependency graph
BOILERPLATE_STOP_TERMS: Set[str] = {
    "person", "business day", "lender", "lenders", "borrower", "borrowers",
    "lead borrower", "administrative agent", "collateral agent", "agent",
    "agents", "loan documents", "loan document", "credit agreement",
    "guarantor", "guarantors", "subsidiary", "subsidiaries", "responsible officer",
    "dollar", "dollars", "law", "laws", "governmental authority", "affiliate",
    "affiliates", "notice", "notices", "platform", "indemnified taxes", "taxes",
    "applicable law", "security agreement", "pledged debt", "pledged equity",
    "equity interests", "capital stock", "code", "erisa", "sec", "gaap"
}


class DependencyNode(BaseModel):
    """A node in the covenant dependency graph representing a defined term or section."""
    node_id: str  # Term name or section ID (e.g. 'Consolidated EBITDA' or 'Section 7.11')
    node_type: str  # 'definition' or 'section'
    clause_text: str  # Verbatim textual definition or section body
    citation: str  # Traceable provenance citation
    references: List[str] = Field(default_factory=list)  # Outgoing dependencies
    is_seed: bool = False
    is_unresolved: bool = False


class CircularityInfo(BaseModel):
    """Details of a circular reference detected during graph traversal."""
    cycle: List[str]  # Ordered sequence of node_ids forming the cycle
    cycle_type: str  # 'circular_cap' or 'general_circularity'
    mathematical_solution: Optional[str] = None  # Closed-form algebraic solution if applicable
    flagged_for_human_review: bool = True
    explanation: str


class DependencyGraph(BaseModel):
    """The complete dependency graph for covenant rule set synthesis."""
    version_id: str
    company: str
    nodes: Dict[str, DependencyNode] = Field(default_factory=dict)
    edges: List[Tuple[str, str]] = Field(default_factory=list)  # (source, target)
    seed_terms: List[str] = Field(default_factory=list)
    cycles: List[CircularityInfo] = Field(default_factory=list)
    unresolved_terms: List[str] = Field(default_factory=list)
    pruned_terms: List[str] = Field(default_factory=list)
    needs_human_review: bool = False
    review_reasons: List[str] = Field(default_factory=list)

    def get_node(self, node_id: str) -> Optional[DependencyNode]:
        if node_id in self.nodes:
            return self.nodes[node_id]
        n_lower = node_id.lower()
        for k, v in self.nodes.items():
            if k.lower() == n_lower:
                return v
        return None

    def get_all_clauses_text(self) -> str:
        """Concatenates all retrieved verbatim clauses with their citations."""
        snippets = []
        for node in self.nodes.values():
            snippets.append(f"### [{node.node_type.upper()}] {node.node_id}\n{node.citation}\n{node.clause_text}\n")
        return "\n".join(snippets)


class DependencyGraphBuilder:
    """
    Constructs the directed dependency graph starting from core covenant roots.
    Recursively scans clause text for references to other defined terms and sections.
    """

    DEFAULT_SEED_TERMS = [
        "Consolidated EBITDA",
        "Consolidated Net Debt",
        "Consolidated Total Net Debt",
        "Consolidated Total Indebtedness",
        "Total Debt",
        "Consolidated Total Net Leverage Ratio",
        "Total Net Leverage Ratio",
        "Section 7.11",
        "Section 7.8",
        "Section 8.11"
    ]

    def __init__(self, max_depth: int = 4):
        self.max_depth = max_depth

    def build_graph(
        self,
        agreement_version: AgreementVersion,
        seed_terms: Optional[List[str]] = None
    ) -> DependencyGraph:
        """
        Builds the dependency graph starting from seed terms, expanding recursively.
        """
        seeds = seed_terms or self.DEFAULT_SEED_TERMS

        # Normalize definition dictionary for fast lookup
        # key_lower -> original_key
        def_lookup: Dict[str, str] = {k.lower(): k for k in agreement_version.definitions.keys()}
        sec_lookup: Dict[str, str] = {k.lower(): k for k in agreement_version.sections.keys()}

        graph = DependencyGraph(
            version_id=agreement_version.version_id,
            company=agreement_version.company,
            seed_terms=[]
        )

        visited: Set[str] = set()
        queue: List[Tuple[str, int, Optional[str]]] = []  # (term_or_sec, depth, parent)

        # 1. Initialize queue with active seeds found in this agreement
        for seed in seeds:
            matched_key = None
            seed_lower = seed.lower()
            if seed_lower in def_lookup:
                matched_key = def_lookup[seed_lower]
            elif seed_lower in sec_lookup:
                matched_key = sec_lookup[seed_lower]
            else:
                # Try prefix / subsection matching (e.g. "Section 7.11(a)" -> "Section 7.11")
                for sec_k in agreement_version.sections.keys():
                    if seed_lower in sec_k.lower() or sec_k.lower() in seed_lower:
                        matched_key = sec_k
                        break

            if matched_key:
                queue.append((matched_key, 0, None))
                graph.seed_terms.append(matched_key)

        # 2. Breadth-First Recursive Traversal
        while queue:
            current_id, depth, parent = queue.pop(0)

            if parent:
                edge = (parent, current_id)
                if edge not in graph.edges:
                    graph.edges.append(edge)

            if current_id in visited:
                continue
            visited.add(current_id)

            # Retrieve text and citation
            node_type = "definition" if current_id in agreement_version.definitions else "section"
            clause_text = agreement_version.get_clause(current_id)
            citation = agreement_version.get_citation(current_id)

            if not clause_text:
                continue

            # Find references in this clause text
            references, unresolved, pruned = self._find_references(
                text=clause_text,
                current_id=current_id,
                agreement_version=agreement_version,
                def_lookup=def_lookup,
                sec_lookup=sec_lookup
            )

            # Record node
            node = DependencyNode(
                node_id=current_id,
                node_type=node_type,
                clause_text=clause_text,
                citation=citation,
                references=references,
                is_seed=(current_id in graph.seed_terms)
            )
            graph.nodes[current_id] = node

            for p in pruned:
                if p not in graph.pruned_terms:
                    graph.pruned_terms.append(p)

            for u in unresolved:
                if u not in graph.unresolved_terms and u.lower() not in BOILERPLATE_STOP_TERMS:
                    graph.unresolved_terms.append(u)

            # Expand children if depth < max_depth
            if depth < self.max_depth:
                for ref in references:
                    if ref not in visited:
                        queue.append((ref, depth + 1, current_id))
                    else:
                        # Edge to already visited node
                        edge = (current_id, ref)
                        if edge not in graph.edges:
                            graph.edges.append(edge)

        # 3. Detect Circular References / Cycles
        self._detect_cycles(graph)

        # 4. Human Review Flagging
        if graph.unresolved_terms:
            graph.needs_human_review = True
            graph.review_reasons.append(
                f"Missing or unresolved definitions referenced in text: {graph.unresolved_terms}"
            )

        if graph.cycles:
            # Check if cycle is an EBITDA circular cap
            has_ebitda_cycle = any("consolidated ebitda" in [n.lower() for n in c.cycle] for c in graph.cycles)
            if has_ebitda_cycle:
                graph.review_reasons.append(
                    "Detected circular EBITDA add-back cap ('% of Consolidated EBITDA calculated after giving effect to add-backs'). "
                    "Algebraic closed-form formula will be enforced."
                )

        return graph

    def _find_references(
        self,
        text: str,
        current_id: str,
        agreement_version: AgreementVersion,
        def_lookup: Dict[str, str],
        sec_lookup: Dict[str, str]
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Scans clause text for referenced defined terms and sections.
        Returns: (valid_references, unresolved_terms, pruned_terms)
        """
        valid_refs: List[str] = []
        unresolved: List[str] = []
        pruned: List[str] = []

        # 1. Match Section cross-references: 'Section 7.11', 'Section 1.01', etc.
        sec_matches = re.findall(r"(?:Section|SECTION)\s+(\d+\.\d+)", text)
        for s_num in sec_matches:
            sec_name = f"Section {s_num}"
            if sec_name.lower() == current_id.lower():
                continue
            if sec_name.lower() in sec_lookup:
                canonical = sec_lookup[sec_name.lower()]
                if canonical not in valid_refs:
                    valid_refs.append(canonical)
            else:
                # Referenced section is missing or not indexed
                if sec_name not in unresolved:
                    unresolved.append(sec_name)

        # 2. Match quoted defined terms: “Term” or "Term"
        quoted_terms = re.findall(r'[“"״\']([A-Z][A-Za-z0-9\s\-_]{2,50})[”"״\']', text)
        for q_term in quoted_terms:
            term_clean = q_term.strip()
            t_lower = term_clean.lower()

            if t_lower == current_id.lower():
                continue

            if t_lower in BOILERPLATE_STOP_TERMS:
                pruned.append(term_clean)
                continue

            if t_lower in def_lookup:
                canonical = def_lookup[t_lower]
                if canonical not in valid_refs:
                    valid_refs.append(canonical)
            elif any(t_lower == k.lower() for k in agreement_version.definitions):
                # case-insensitive fallback
                matched = next(k for k in agreement_version.definitions if k.lower() == t_lower)
                if matched not in valid_refs:
                    valid_refs.append(matched)

        # 3. Match known defined terms that appear unquoted in text
        # (e.g., 'Consolidated Net Income', 'Consolidated Total Debt', 'Interest Expense')
        for def_lower, def_canonical in def_lookup.items():
            if def_canonical.lower() == current_id.lower():
                continue

            pattern = r'\b' + re.escape(def_canonical) + r'\b'
            if re.search(pattern, text, re.IGNORECASE):
                if def_lower in BOILERPLATE_STOP_TERMS:
                    if def_canonical not in pruned:
                        pruned.append(def_canonical)
                else:
                    # Only check multi-word financial terms or key financial concepts
                    if " " in def_canonical or def_lower in {"ebitda", "indebtedness", "liquidity"}:
                        if def_canonical not in valid_refs:
                            valid_refs.append(def_canonical)

        return valid_refs, unresolved, pruned

    def _detect_cycles(self, graph: DependencyGraph) -> None:
        """
        Detects directed cycles using depth-first search cycle detection.
        """
        adj: Dict[str, List[str]] = {nid: [] for nid in graph.nodes.keys()}
        for src, dst in graph.edges:
            if src in adj and dst in adj:
                adj[src].append(dst)

        visited: Set[str] = set()
        rec_stack: List[str] = []

        def dfs(node: str):
            visited.add(node)
            rec_stack.append(node)

            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    # Cycle found!
                    idx = rec_stack.index(neighbor)
                    cycle_path = rec_stack[idx:] + [neighbor]

                    # Classify circularity
                    cycle_lower = [x.lower() for x in cycle_path]
                    is_ebitda_circ = any("ebitda" in x for x in cycle_lower)

                    circ = CircularityInfo(
                        cycle=cycle_path,
                        cycle_type="circular_cap" if is_ebitda_circ else "general_circularity",
                        mathematical_solution=(
                            "Capped Add-back <= (X% / (1 - X%)) * (Baseline EBITDA + Uncapped Add-backs)"
                            if is_ebitda_circ else None
                        ),
                        flagged_for_human_review=True,
                        explanation=(
                            f"Circular dependency between {' -> '.join(cycle_path)}. "
                            f"{'Applies circular post-addback algebraic cap resolution.' if is_ebitda_circ else 'Flagged for human reviewer confirmation.'}"
                        )
                    )
                    # Avoid duplicate cycles
                    if not any(c.cycle == cycle_path for c in graph.cycles):
                        graph.cycles.append(circ)

            rec_stack.pop()

        for node_id in list(graph.nodes.keys()):
            if node_id not in visited:
                dfs(node_id)

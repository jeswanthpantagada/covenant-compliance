"""
Document Ingestion & Parsing Module for Credit Agreements and Amendments.

Preserves exact provenance (sections, paragraphs, defined terms, and page numbers for PDFs)
without inventing synthetic page numbers for HTML documents.
Treats generated paragraph/block identifiers as internal references, not official legal paragraph numbers.
"""

from __future__ import annotations

import os
import re
import html
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup

from src.utils.hashing import compute_file_sha256


class DocumentMetadata(BaseModel):
    """Metadata describing the ingested document."""
    doc_id: str
    filename: str
    file_type: str  # 'html' or 'pdf'
    sha256: str
    file_size_bytes: int
    company: Optional[str] = None
    title: Optional[str] = None
    role: Optional[str] = None  # 'original_credit_agreement' or 'amendment'
    agreement_date: Optional[str] = None
    effective_date: Optional[str] = None
    filing_date: Optional[str] = None
    form: Optional[str] = None
    exhibit: Optional[str] = None
    accession: Optional[str] = None
    source_url: Optional[str] = None


class DocumentBlock(BaseModel):
    """A granular structural unit (paragraph, header, table, or definition) of an agreement."""
    block_index: int
    block_type: str  # 'heading', 'paragraph', 'definition', 'table'
    text: str
    section_reference: Optional[str] = None  # e.g., 'Section 7.11', 'Section 1.01'
    article_reference: Optional[str] = None  # e.g., 'ARTICLE VII'
    internal_ref_id: str  # Strictly an internal reference ID (e.g. 'internal_block_42'), NOT an official legal paragraph number
    paragraph_reference: Optional[str] = None
    page_number: Optional[int] = None  # Populated only for PDFs; strictly None for HTML
    defined_term: Optional[str] = None  # Extracted term if this block defines one
    source_doc_id: str

    @property
    def citation(self) -> str:
        """Returns a human-readable, auditable citation string."""
        parts = [f"Doc: {self.source_doc_id}"]
        if self.page_number is not None:
            parts.append(f"Page {self.page_number}")
        if self.article_reference:
            parts.append(self.article_reference)
        if self.section_reference:
            parts.append(self.section_reference)
        parts.append(f"InternalRef: {self.internal_ref_id}")
        if self.defined_term:
            parts.append(f'Term "{self.defined_term}"')
        return f"[{' | '.join(parts)}]"


class ParsedDocument(BaseModel):
    """Represents a fully ingested and structurally parsed credit agreement or amendment."""
    metadata: DocumentMetadata
    blocks: List[DocumentBlock] = Field(default_factory=list)
    definitions_index: Dict[str, int] = Field(default_factory=dict)  # term -> block_index
    sections_index: Dict[str, List[int]] = Field(default_factory=dict)  # section -> list of block_index

    def get_full_text(self) -> str:
        """Concatenates all block texts."""
        return "\n\n".join(b.text for b in self.blocks)

    def find_blocks_by_section(self, section_ref: str) -> List[DocumentBlock]:
        """Finds all blocks belonging to a given section (e.g., 'Section 7.11')."""
        target = section_ref.strip().lower()
        matching_indices = set()
        for sec_key, indices in self.sections_index.items():
            if target in sec_key.lower():
                matching_indices.update(indices)
        return [self.blocks[i] for i in sorted(matching_indices)]

    def find_definition(self, term: str) -> Optional[DocumentBlock]:
        """Looks up the block that defines a specific term."""
        norm_term = term.strip().lower()
        for def_key, idx in self.definitions_index.items():
            if def_key.lower() == norm_term:
                return self.blocks[idx]
        return None

    def get_definition_blocks(self, term: str) -> List[DocumentBlock]:
        """
        Retrieves all blocks belonging to the definition of a term,
        starting from the defined term block up until the next definition or section header.
        """
        lead_block = self.find_definition(term)
        if not lead_block:
            return []

        result = [lead_block]
        for i in range(lead_block.block_index + 1, len(self.blocks)):
            b = self.blocks[i]
            # Stop if a new definition or new section heading begins
            if b.defined_term or b.block_type == "heading":
                break
            result.append(b)
        return result

    def get_definition_text(self, term: str) -> str:
        """Returns the complete aggregated text of a definition across all its sub-blocks."""
        blocks = self.get_definition_blocks(term)
        return " ".join(b.text for b in blocks)

    def search_blocks(self, query: str, limit: int = 10) -> List[DocumentBlock]:
        """Searches blocks for a text snippet or keyword."""
        q_lower = query.lower()
        results = [b for b in self.blocks if q_lower in b.text.lower()]
        return results[:limit]


class HTMLDocumentParser:
    """
    Parses SEC HTML credit agreements and amendments.
    Strictly preserves Article headers, Section headers, and internal references.
    Does NOT invent synthetic page numbers.
    """

    ARTICLE_REGEX = re.compile(r'^(?:ARTICLE|Article)\s+([IVXLCDM\d]+)', re.IGNORECASE)
    SECTION_HEADING_REGEX = re.compile(
        r'^(?:SECTION|Section)\s+(\d+(?:\.\d+)?(?:\([a-zA-Z0-9]+\))?)[\.:\-\–\—]?\s*(?:[A-Z\[]|$)',
        re.IGNORECASE
    )
    DEFINED_TERM_REGEX = re.compile(
        r'^(?:[\"“\u201c\u201d\']|&#8220;)?([A-Za-z0-9\s\-\/\(\)]+?)(?:[\"”\u201d\']|&#8221;)?\s+(?:means|shall mean|has the meaning|shall have the meaning)',
        re.IGNORECASE
    )

    def parse(self, file_path: Union[str, Path], metadata: DocumentMetadata) -> ParsedDocument:
        path = Path(file_path)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw_html = f.read()

        soup = BeautifulSoup(raw_html, "html.parser")
        blocks: List[DocumentBlock] = []
        definitions_index: Dict[str, int] = {}
        sections_index: Dict[str, List[int]] = {}

        current_article: Optional[str] = None
        current_section: Optional[str] = None
        block_counter = 0

        # Collect leaf structural containers: tags that don't nest other divs or p
        raw_elements: List[str] = []
        tags = soup.find_all(["div", "p", "table", "h1", "h2", "h3", "h4"])

        for tag in tags:
            # If tag is inside a table, we let table container process it
            if tag.name in ["div", "p"] and tag.find_parent("table"):
                continue

            # Skip non-leaf block containers so children are processed directly
            if tag.name in ["div", "p"] and tag.find(["div", "p", "table"]):
                continue

            txt = " ".join(tag.get_text().split())
            if not txt or len(txt) < 3:
                continue

            # Split text if multiple genuine section headings are embedded in one tag
            sub_parts = re.split(
                r'(?<=[.\s])(?=(?:SECTION|Section)\s+\d+(?:\.\d+)?(?:\([a-zA-Z0-9]+\))?[\.:\-\–\—]?\s+[A-Z\[])',
                txt
            )
            for part in sub_parts:
                clean = part.strip()
                if clean and len(clean) > 2:
                    raw_elements.append(clean)

        for text in raw_elements:
            if not text or len(text) < 3:
                continue

            # Check Article header
            art_match = self.ARTICLE_REGEX.match(text)
            if art_match:
                current_article = f"ARTICLE {art_match.group(1).upper()}"

            # Check Section header
            sec_match = self.SECTION_HEADING_REGEX.match(text)
            if sec_match:
                sec_id = sec_match.group(1).rstrip(".")
                current_section = f"Section {sec_id}"

            # Check Defined Term
            block_type = "paragraph"
            defined_term = None

            if sec_match or art_match:
                block_type = "heading"
            else:
                def_match = self.DEFINED_TERM_REGEX.match(text)
                if def_match:
                    candidate = def_match.group(1).strip()
                    if len(candidate) < 70 and not candidate.lower().startswith("section"):
                        block_type = "definition"
                        defined_term = candidate

            internal_id = f"internal_block_{block_counter}"
            block = DocumentBlock(
                block_index=block_counter,
                block_type=block_type,
                text=text,
                section_reference=current_section,
                article_reference=current_article,
                internal_ref_id=internal_id,
                paragraph_reference=f"para_{block_counter}",
                page_number=None,  # Strictly None for HTML documents
                defined_term=defined_term,
                source_doc_id=metadata.doc_id
            )
            blocks.append(block)

            # Index definitions
            if defined_term:
                definitions_index[defined_term] = block_counter

            # Index sections
            if current_section:
                if current_section not in sections_index:
                    sections_index[current_section] = []
                sections_index[current_section].append(block_counter)

            block_counter += 1

        return ParsedDocument(
            metadata=metadata,
            blocks=blocks,
            definitions_index=definitions_index,
            sections_index=sections_index
        )


class PDFDocumentParser:
    """
    Parses PDF credit agreements and amendments page-by-page.
    Strictly preserves 1-based page numbers and identifies structural section headers.
    """

    def parse(self, file_path: Union[str, Path], metadata: DocumentMetadata) -> ParsedDocument:
        from pypdf import PdfReader

        path = Path(file_path)
        reader = PdfReader(str(path))
        blocks: List[DocumentBlock] = []
        definitions_index: Dict[str, int] = {}
        sections_index: Dict[str, List[int]] = {}

        current_article: Optional[str] = None
        current_section: Optional[str] = None
        block_counter = 0

        for page_idx, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            paragraphs = [p.strip() for p in page_text.split("\n\n") if p.strip()]

            for p_num, para in enumerate(paragraphs, start=1):
                clean_text = " ".join(para.split())
                if not clean_text or len(clean_text) < 3:
                    continue

                # Header detection
                art_match = re.match(r'^(?:ARTICLE|Article)\s+([IVXLCDM\d]+)', clean_text, re.IGNORECASE)
                if art_match:
                    current_article = f"ARTICLE {art_match.group(1).upper()}"

                sec_match = re.match(r'^(?:SECTION|Section)\s+(\d+(?:\.\d+)?)', clean_text, re.IGNORECASE)
                if sec_match:
                    current_section = f"Section {sec_match.group(1)}"

                # Check defined term
                def_match = HTMLDocumentParser.DEFINED_TERM_REGEX.match(clean_text)
                defined_term = None
                block_type = "paragraph"
                if sec_match or art_match:
                    block_type = "heading"
                elif def_match:
                    candidate = def_match.group(1).strip()
                    if len(candidate) < 70:
                        block_type = "definition"
                        defined_term = candidate

                internal_id = f"p{page_idx}_internal_block_{p_num}"
                block = DocumentBlock(
                    block_index=block_counter,
                    block_type=block_type,
                    text=clean_text,
                    section_reference=current_section,
                    article_reference=current_article,
                    internal_ref_id=internal_id,
                    paragraph_reference=f"p{page_idx}_para_{p_num}",
                    page_number=page_idx,  # Exact 1-based page number
                    defined_term=defined_term,
                    source_doc_id=metadata.doc_id
                )
                blocks.append(block)

                if defined_term:
                    definitions_index[defined_term] = block_counter

                if current_section:
                    if current_section not in sections_index:
                        sections_index[current_section] = []
                    sections_index[current_section].append(block_counter)

                block_counter += 1

        return ParsedDocument(
            metadata=metadata,
            blocks=blocks,
            definitions_index=definitions_index,
            sections_index=sections_index
        )


class DocumentIngestionService:
    """
    Unified entry-point for loading and parsing credit agreements and amendments.
    Verifies SHA-256 hash and dispatches to appropriate parser.
    """

    def __init__(self):
        self.html_parser = HTMLDocumentParser()
        self.pdf_parser = PDFDocumentParser()

    def ingest_document(
        self,
        file_path: Union[str, Path],
        doc_id: str,
        expected_sha256: Optional[str] = None,
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> ParsedDocument:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Source file does not exist: {path}")

        actual_sha256 = compute_file_sha256(path)
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise ValueError(
                f"Integrity check failed for {path.name}!\n"
                f"  Expected SHA-256: {expected_sha256}\n"
                f"  Actual SHA-256:   {actual_sha256}"
            )

        file_size = path.stat().st_size
        suffix = path.suffix.lower()
        file_type = "pdf" if suffix == ".pdf" else "html"

        meta_kwargs: Dict[str, Any] = {
            "doc_id": doc_id,
            "filename": path.name,
            "file_type": file_type,
            "sha256": actual_sha256,
            "file_size_bytes": file_size
        }
        if additional_metadata:
            meta_kwargs.update(additional_metadata)

        metadata = DocumentMetadata(**meta_kwargs)

        if file_type == "pdf":
            return self.pdf_parser.parse(path, metadata)
        else:
            return self.html_parser.parse(path, metadata)

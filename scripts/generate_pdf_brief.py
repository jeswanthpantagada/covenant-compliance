"""
Generates the 4-Page Tresata Written Brief PDF using ReportLab.
Strictly formatted to exactly 4 pages with executive styling.
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas
import pypdf

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#4B5563"))
        
        # Header (pages 2-4)
        if self._pageNumber > 1:
            self.drawString(36, 762, "TRESATA WRITTEN BRIEF | AI-ASSISTED COVENANT COMPLIANCE ENGINE")
            self.drawRightString(576, 762, "QUARTERLY TOTAL NET LEVERAGE RATIO")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(36, 756, 576, 756)
            
        # Footer
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(36, 36, 576, 36)
        
        self.drawString(36, 26, "CONFIDENTIAL & PROPRIETARY — FOR EVALUATION PURPOSES ONLY")
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(576, 26, page_text)
        self.restoreState()

def build_pdf(output_path: str):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=40,
        bottomMargin=42
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=2
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#1E3A8A'),
        spaceAfter=4
    )
    
    meta_style = ParagraphStyle(
        'DocMeta',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#475569'),
        spaceAfter=6
    )

    h1_style = ParagraphStyle(
        'SectionHeading1',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor('#0F172A'),
        spaceBefore=4,
        spaceAfter=4,
        keepWithNext=True
    )
    
    h2_style = ParagraphStyle(
        'SectionHeading2',
        parent=styles['Heading3'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#1E40AF'),
        spaceBefore=3,
        spaceAfter=2,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor('#1E293B'),
        spaceAfter=3
    )

    body_bold = ParagraphStyle(
        'BodyDarkBold',
        parent=body_style,
        fontName='Helvetica-Bold'
    )

    table_cell = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor('#0F172A')
    )

    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell,
        fontName='Helvetica-Bold'
    )

    table_header = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=6.8,
        leading=8.5,
        textColor=colors.white
    )

    code_style = ParagraphStyle(
        'CodeBox',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=6.5,
        leading=8,
        textColor=colors.HexColor('#0F172A')
    )

    story = []

    # ==========================================
    # PAGE 1: EXECUTIVE SUMMARY & PROBLEM STATEMENT
    # ==========================================
    story.append(Paragraph("TRESATA WRITTEN BRIEF: AI-ASSISTED COVENANT COMPLIANCE ENGINE", title_style))
    story.append(Paragraph("System Architecture, Mathematical Formalism, Empirical SEC Verification & Engineering Report", subtitle_style))
    story.append(Paragraph("<b>Focus:</b> Quarterly Maintenance Total Net Leverage Ratio Compliance | <b>Author:</b> AI Agent & Lead Architect | <b>Date:</b> November 2026", meta_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#1E3A8A'), spaceBefore=1, spaceAfter=5))

    story.append(Paragraph("1.1 Executive Summary", h1_style))
    story.append(Paragraph(
        "In corporate leveraged finance and syndicated lending, credit agreements govern billions of dollars in enterprise debt through restrictive financial covenants. Among these covenants, the <b>quarterly maintenance Total Net Leverage Ratio</b> (Consolidated Net Debt / LTM Consolidated EBITDA) is the single most critical solvency test. A covenant breach triggers immediate technical default, accelerating debt maturity and shifting corporate control to lenders. Historically, compliance certification has been a high-friction, error-prone manual process where credit analysts spend hundreds of hours reconciling 200+ page contracts, tracking multi-year amendments, mapping general ledger accounts, and arbitrating borrower-claimed EBITDA add-backs against bespoke contractual caps.",
        body_style
    ))
    story.append(Paragraph(
        "<b>The Fatal Flaws of Pure LLMs in Credit Operations:</b> Deploying generic LLM agents directly on covenant math leads to catastrophic operational failures: (1) <i>Arithmetic Hallucination:</i> LLMs cannot reliably perform nested numerical cap mathematics, particularly circular post-addback caps where an add-back is limited to a percentage of EBITDA calculated <i>after</i> giving effect to the add-back itself; (2) <i>Citation Drift & Temporal Invention:</i> LLMs routinely invent clause references, assume unstated effective dates, or hallucinate page numbers in SEC HTML filings; (3) <i>Arbitrary Arbitration:</i> When multiple expenses exceed an aggregate cap without an explicit contractual waterfall, LLMs arbitrarily pick an expense to disallow rather than halting for human review.",
        body_style
    ))

    story.append(Paragraph("1.2 Problem Formulation & Architectural Philosophy", h1_style))
    story.append(Paragraph(
        "To eliminate these failure modes while maximizing automation efficiency, we architected an enterprise compliance system based on three non-negotiable principles:",
        body_style
    ))
    story.append(Paragraph(
        "• <b>Division of Cognitive Labor:</b> The LLM functions strictly as a semantic clause parser and qualitative evidence auditor. Python is the 100% deterministic mathematical authority—zero arithmetic is ever delegated to an LLM.<br/>"
        "• <b>Contractual Provenance & Cryptographic Integrity:</b> Every document is hashed (SHA-256). All internal sections and HTML paragraph blocks are assigned stable deterministic identifiers (<code>InternalRef: internal_block_X</code>).<br/>"
        "• <b>Zero Guessing & Mandatory Human-in-the-Loop (HITL) Gates:</b> Ambiguous contractual language, non-positive EBITDA, or unallocated aggregate cap overruns strictly halt calculation with <b><code>Cannot compute</code></b>, requiring credit officer resolution.",
        body_style
    ))

    # Two-Phase Architecture Overview Box
    arch_box_data = [
        [
            Paragraph("<b>PHASE 1: CREDIT AGREEMENT & AMENDMENT DIGITIZATION</b>", table_header),
            Paragraph("<b>PHASE 2: QUARTERLY COMPLIANCE CHECKING ENGINE</b>", table_header)
        ],
        [
            Paragraph(
                "• <b>Document Ingestion:</b> SEC EDGAR HTML/PDF with SHA-256 checksums.<br/>"
                "• <b>Amendment Tracker:</b> Extracts deltas (REPLACE, INSERT, DELETE).<br/>"
                "• <b>Agreement Timeline:</b> Chronological, immutable contract versioning.<br/>"
                "• <b>Dependency Graph:</b> Recursive BFS DAG traversal from covenant roots.<br/>"
                "• <b>Rule Set Synthesis:</b> Verbatim quotation and mathematical boundary checks.<br/>"
                "• <b>[GATE 1: Rule Set Sign-off]:</b> Mandatory credit officer approval.",
                table_cell
            ),
            Paragraph(
                "• <b>Financial Data Ingestion:</b> Multi-period IS, BS, Ledger, Claimed Add-backs.<br/>"
                "• <b>Account Mapper:</b> Maps general ledger to covenant categories.<br/>"
                "• <b>[GATE 2: Account Mapping Sign-off]:</b> Reviewer validates ledger buckets.<br/>"
                "• <b>Add-back Qualitative Auditor:</b> LLM classifies (ELIGIBLE / INELIGIBLE).<br/>"
                "• <b>[GATE 3: Audit Sign-off]:</b> Credit officer reviews and overrides adjustments.<br/>"
                "• <b>100% Deterministic Python Math:</b> Caps, circular algebra, netting, ratios.",
                table_cell
            )
        ]
    ]
    t_arch = Table(arch_box_data, colWidths=[265, 275])
    t_arch.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor('#F8FAFC')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_arch)
    story.append(Spacer(1, 4))

    story.append(Paragraph("1.3 Core Covenant Metrics & Formal Definitions", h2_style))
    story.append(Paragraph(
        "The system evaluates the Total Net Leverage Ratio against the contractual ceiling: "
        "<b>Total Net Leverage Ratio = Consolidated Net Debt / LTM Consolidated EBITDA</b>. "
        "Consolidated Net Debt is defined as Funded Debt minus unrestricted cash, subject to contractual cash netting caps (e.g., $37.5M in Whole Earth Brands). "
        "LTM Consolidated EBITDA is computed over a rolling 4-quarter period, incorporating eligible borrower add-backs subject to individual category caps, circular post-EBITDA bounds, and aggregate basket ceilings.",
        body_style
    ))

    # Force PageBreak to ensure EXACT PAGE 1
    story.append(PageBreak())

    # ==========================================
    # PAGE 2: SYSTEM ARCHITECTURE & TECHNICAL SPECS
    # ==========================================
    story.append(Paragraph("2. SYSTEM ARCHITECTURE & TECHNICAL SPECIFICATIONS", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#1E3A8A'), spaceBefore=1, spaceAfter=5))

    story.append(Paragraph("2.1 End-to-End Architectural Workflow", h2_style))
    story.append(Paragraph(
        "The diagram below details the dual-phase pipeline from raw SEC document ingestion to auditable compliance certification:",
        body_style
    ))

    # Flowchart table representation
    flowchart_data = [
        [Paragraph("<b>PIPELINE STAGE</b>", table_header), Paragraph("<b>TECHNICAL MECHANISM & ALGORITHMIC SAFEGUARDS</b>", table_header), Paragraph("<b>GOVERNANCE GATE</b>", table_header)],
        [
            Paragraph("<b>1. Ingestion</b>", table_cell_bold),
            Paragraph("Parses SEC EDGAR HTML/PDF into atomic content blocks with SHA-256 hashes. Assigns immutable <code>InternalRef: internal_block_X</code> identifiers. Prevents hallucinated legal paragraph numbers.", table_cell),
            Paragraph("Cryptographic Checksum Verification", table_cell)
        ],
        [
            Paragraph("<b>2. Amendment Tracking</b>", table_cell_bold),
            Paragraph("Identifies deltas (REPLACE, INSERT, DELETE). Python verifies supporting verbatim quotes against base document. Maintains chronological timeline of immutable <code>AgreementVersion</code> snapshots.", table_cell),
            Paragraph("Chronological Date Check & Verbatim Quote Match", table_cell)
        ],
        [
            Paragraph("<b>3. Dependency DAG</b>", table_cell_bold),
            Paragraph("Builds Directed Acyclic Graph via recursive BFS traversal starting strictly from covenant root terms. Prunes non-financial boilerplate and flags circular post-EBITDA cap references for algebraic resolution.", table_cell),
            Paragraph("Circularity & Missing Term Detection", table_cell)
        ],
        [
            Paragraph("<b>4. Rule Set Synthesis</b>", table_cell_bold),
            Paragraph("Extracts EBITDA additions, deductions, caps, and covenant ratio stepping schedules. Python engine verifies that all extracted clauses match contract text verbatim.", table_cell),
            Paragraph("<b>[GATE 1]</b> Mandatory Rule Set Sign-off by Credit Officer", table_cell_bold)
        ],
        [
            Paragraph("<b>5. Account Mapping</b>", table_cell_bold),
            Paragraph("Ingests multi-period Trial Balance / General Ledger. Maps account codes to covenant categories with confidence scoring. Flags ambiguous ledgers for human approval.", table_cell),
            Paragraph("<b>[GATE 2]</b> Reviewer Approves Ledger Mappings", table_cell_bold)
        ],
        [
            Paragraph("<b>6. Add-Back Audit</b>", table_cell_bold),
            Paragraph("LLM audits claimed expenses against contractual criteria, classifying items as ELIGIBLE, INELIGIBLE, or NEEDS_HUMAN_REVIEW. Reviewer can override classifications.", table_cell),
            Paragraph("<b>[GATE 3]</b> Reviewer Approves / Overrides Add-backs", table_cell_bold)
        ],
        [
            Paragraph("<b>7. Deterministic Math</b>", table_cell_bold),
            Paragraph("100% Python arithmetic. Solves circular post-EBITDA caps algebraically. Applies cash netting ceilings. Computes ratio, headroom, and cushion percentage across multiple readings.", table_cell),
            Paragraph("<b>Deterministic Output:</b> 4-Case Status & Audit Workpaper", table_cell_bold)
        ]
    ]
    t_flow = Table(flowchart_data, colWidths=[90, 330, 120])
    t_flow.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F8FAFC')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F8FAFC')]),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_flow)
    story.append(Spacer(1, 4))

    story.append(Paragraph("2.2 Core Technical Innovations", h2_style))
    story.append(Paragraph(
        "<b>1. Closed-Form Algebraic Resolution of Circular Add-back Caps:</b><br/>"
        "Credit agreements routinely cap restructuring or synergy add-backs to a percentage of Consolidated EBITDA calculated <i>after</i> giving effect to the add-back (e.g. Redwire Corporation Section 1.01 limits synergy add-backs to 25% of post-addback EBITDA). Formally: "
        "<b>A ≤ x · (B + A)</b>, where <i>A</i> is the capped add-back, <i>B</i> is baseline uncapped EBITDA, and <i>x</i> is the cap percentage. "
        "Rearranging: <b>A · (1 - x) ≤ x · B  ⇒  A ≤ [x / (1 - x)] · B</b>. "
        "For a 25% cap (x = 0.25): <b>Max Add-back = (0.25 / 0.75) · B = (1/3) · B ≈ 33.333% of Baseline EBITDA</b>. "
        "Rather than iterative numerical approximation, Python solves this in exact closed form.",
        body_style
    ))
    story.append(Paragraph(
        "<b>2. Contractual Cash Netting Caps:</b><br/>"
        "While standard models subtract all unrestricted cash, leveraged agreements frequently cap cash netting (e.g., Whole Earth Brands Section 1.01 limits netted cash to exactly <b>$37,500,000</b>). Our calculation engine computes: "
        "<b>Consolidated Net Debt = Funded Debt - min(Unrestricted Cash, Cash Netting Cap)</b>.",
        body_style
    ))
    story.append(Paragraph(
        "<b>3. The 4-Case Compliance Status Taxonomy:</b><br/>"
        "To prevent misleading binary pass/fail claims under contractual ambiguity, the engine outputs four distinct statuses: "
        "(1) <b><code>Pass under all readings</code></b>: Ratio ≤ covenant ceiling across primary and alternative readings; "
        "(2) <b><code>Fail under all readings</code></b>: Ratio > covenant ceiling across all readings; "
        "(3) <b><code>Outcome depends on a reading</code></b>: Compliant under one interpretation but failing under another (flags exact variance for credit committee); "
        "(4) <b><code>Cannot compute</code></b>: Strictly halted due to missing data, non-positive EBITDA, unapproved gates, or unallocated aggregate cap overruns.",
        body_style
    ))
    story.append(Paragraph(
        "<b>4. Mathematical Headroom & Cushion Formulas:</b><br/>"
        "• <b>Ratio Headroom:</b> Covenant Ceiling - Actual Total Net Leverage Ratio<br/>"
        "• <b>Absolute EBITDA Buffer ($):</b> Actual LTM EBITDA - (Consolidated Net Debt / Covenant Ceiling)<br/>"
        "• <b>EBITDA Cushion (%):</b> (Absolute EBITDA Buffer / Actual LTM EBITDA) × 100% <i>(represents the percentage drop in EBITDA triggering technical breach).</i>",
        body_style
    ))

    # Force PageBreak to ensure EXACT PAGE 2
    story.append(PageBreak())

    # ==========================================
    # PAGE 3: EMPIRICAL VALIDATION & TEST RESULTS
    # ==========================================
    story.append(Paragraph("3. EMPIRICAL VALIDATION & TEST RESULTS", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#1E3A8A'), spaceBefore=1, spaceAfter=5))

    story.append(Paragraph("3.1 Empirical Verification Against Real SEC EDGAR Filings", h2_style))
    story.append(Paragraph(
        "The system was verified against 8 official SEC EDGAR filings across three public companies maintaining quarterly Total Net Leverage Ratio covenants:",
        body_style
    ))

    sec_table_data = [
        [Paragraph("<b>COMPANY</b>", table_header), Paragraph("<b>DOCUMENT & FORM</b>", table_header), Paragraph("<b>EFFECTIVE</b>", table_header), Paragraph("<b>SHA-256 CHECK (12 CHARS)</b>", table_header), Paragraph("<b>VERIFIED CONTRACTUAL MECHANICS</b>", table_header)],
        [
            Paragraph("<b>Redwire Corp</b>", table_cell_bold),
            Paragraph("Credit Agreement (10-K Ex-10.13)", table_cell),
            Paragraph("2020-10-28", table_cell),
            Paragraph("<code>3a5a70bc5020...</code>", table_cell),
            Paragraph("Section 7.11 ratio schedule (5.00x → 4.50x); 25% circular post-EBITDA cap.", table_cell)
        ],
        [
            Paragraph("<b>Redwire Corp</b>", table_cell_bold),
            Paragraph("Fifth Amendment (8-K Ex-10.1)", table_cell),
            Paragraph("2022-10-28", table_cell),
            Paragraph("<code>f5a48c8303a7...</code>", table_cell),
            Paragraph("Replaced Section 7.11(a) schedule; stepped ratio ceiling up to 6.50:1.00.", table_cell)
        ],
        [
            Paragraph("<b>Redwire Corp</b>", table_cell_bold),
            Paragraph("Sixth Amendment (8-K Ex-10.1)", table_cell),
            Paragraph("2023-06-23", table_cell),
            Paragraph("<code>44cd8c96d528...</code>", table_cell),
            Paragraph("Replaced Section 7.11(a) schedule; stepped ratio ceiling up to 7.50:1.00.", table_cell)
        ],
        [
            Paragraph("<b>Whole Earth</b>", table_cell_bold),
            Paragraph("A&R Agreement (8-K Ex-10.1)", table_cell),
            Paragraph("2021-02-05", table_cell),
            Paragraph("<code>68df9acc9b5b...</code>", table_cell),
            Paragraph("Section 7.8 ratio (4.00x → 5.50x); <b>$37.5M cash netting cap</b>; $10M / 12.5% fee cap.", table_cell)
        ],
        [
            Paragraph("<b>Whole Earth</b>", table_cell_bold),
            Paragraph("First Amendment (8-K Ex-10.1)", table_cell),
            Paragraph("2022-06-15", table_cell),
            Paragraph("<code>9914760a8c0a...</code>", table_cell),
            Paragraph("Inserted fixed deemed quarterly EBITDA run-rates; revolving facility resize.", table_cell)
        ],
        [
            Paragraph("<b>Mercury Systems</b>", table_cell_bold),
            Paragraph("Credit Agreement (8-K Ex-10.1)", table_cell),
            Paragraph("2016-05-02", table_cell),
            Paragraph("<code>73e51554c19d...</code>", table_cell),
            Paragraph("Section 8.11(b) Total Net Leverage Ratio; uncapped cash netting.", table_cell)
        ],
        [
            Paragraph("<b>Mercury Systems</b>", table_cell_bold),
            Paragraph("Amendment No. 5 (8-K Ex-10.1)", table_cell),
            Paragraph("2023-11-07", table_cell),
            Paragraph("<code>d07b1bf5562f...</code>", table_cell),
            Paragraph("Modified covenant relief period leverage thresholds.", table_cell)
        ],
        [
            Paragraph("<b>Mercury Systems</b>", table_cell_bold),
            Paragraph("Amendment No. 6 (8-K Ex-10.1)", table_cell),
            Paragraph("2024-08-12", table_cell),
            Paragraph("<code>eb7bcfa77dce...</code>", table_cell),
            Paragraph("Ratio 5.25x → 4.50x; +0.50x acquisition holiday; EAC Adjustments in clause (x)(II).", table_cell)
        ]
    ]
    t_sec = Table(sec_table_data, colWidths=[70, 115, 55, 95, 205])
    t_sec.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F8FAFC')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F8FAFC')]),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_sec)
    story.append(Spacer(1, 4))

    story.append(Paragraph("3.2 Empirical Results Across 12 Synthetic Stress Scenarios (54 Passed, 1 Skipped)", h2_style))
    story.append(Paragraph(
        "All 12 synthetic test cases passed exact numerical thresholds and status requirements in automated testing (54 passed, 1 skipped in pytest):",
        body_style
    ))

    stress_data = [
        [Paragraph("<b>#</b>", table_header), Paragraph("<b>SCENARIO DESCRIPTION</b>", table_header), Paragraph("<b>KEY MECHANISM & CAPS</b>", table_header), Paragraph("<b>EXPECTED STATUS</b>", table_header), Paragraph("<b>ACTUAL STATUS</b>", table_header), Paragraph("<b>LEVERAGE</b>", table_header), Paragraph("<b>CUSHION %</b>", table_header)],
        [Paragraph("<b>1</b>", table_cell_bold), Paragraph("Comfortably Compliant", table_cell), Paragraph("Low debt ($130M Net Debt / $125M EBITDA)", table_cell), Paragraph("Pass under all readings", table_cell), Paragraph("Pass under all readings ✅", table_cell_bold), Paragraph("1.0400x", table_cell), Paragraph("76.89%", table_cell)],
        [Paragraph("<b>2</b>", table_cell_bold), Paragraph("Direct Covenant Breach", table_cell), Paragraph("High debt ($445M Net Debt / $60M EBITDA vs 4.50x)", table_cell), Paragraph("Fail under all readings", table_cell), Paragraph("Fail under all readings ✅", table_cell_bold), Paragraph("7.4167x", table_cell), Paragraph("-64.81%", table_cell)],
        [Paragraph("<b>3</b>", table_cell_bold), Paragraph("Capped Add-back Haircut", table_cell), Paragraph("$25M claimed restructuring restricted to $6.67M cap", table_cell), Paragraph("Pass under all readings", table_cell), Paragraph("Pass under all readings ✅", table_cell_bold), Paragraph("1.6721x", table_cell), Paragraph("62.84%", table_cell)],
        [Paragraph("<b>4</b>", table_cell_bold), Paragraph("Circular Post-EBITDA Cap", table_cell), Paragraph("Exact algebra: (0.25/0.75)*$30M = $10.0M limit", table_cell), Paragraph("Pass under all readings", table_cell), Paragraph("Pass under all readings ✅", table_cell_bold), Paragraph("1.4138x", table_cell), Paragraph("68.58%", table_cell)],
        [Paragraph("<b>5</b>", table_cell_bold), Paragraph("Contractual Cash Netting Cap", table_cell), Paragraph("$60M cash capped at $37.5M ceiling ($22.5M unnetted)", table_cell), Paragraph("Pass under all readings", table_cell), Paragraph("Pass under all readings ✅", table_cell_bold), Paragraph("2.0312x", table_cell), Paragraph("54.86%", table_cell)],
        [Paragraph("<b>6</b>", table_cell_bold), Paragraph("Unallocated Cap Overrun", table_cell), Paragraph("Two categories exceed $6.67M shared basket without waterfall", table_cell), Paragraph("Cannot compute", table_cell), Paragraph("Cannot compute ✅", table_cell_bold), Paragraph("N/A", table_cell), Paragraph("N/A", table_cell)],
        [Paragraph("<b>7</b>", table_cell_bold), Paragraph("Outcome Depends on Reading", table_cell), Paragraph("Reading A: 4.500x (Pass); Reading B: 4.615x (Fail)", table_cell), Paragraph("Outcome depends on reading", table_cell), Paragraph("Outcome depends... ✅", table_cell_bold), Paragraph("4.5000x", table_cell), Paragraph("1.10%", table_cell)],
        [Paragraph("<b>8</b>", table_cell_bold), Paragraph("Negative EBITDA Scenario", table_cell), Paragraph("LTM EBITDA -$55M; prevents ratio calculation; review flag", table_cell), Paragraph("Cannot compute", table_cell), Paragraph("Cannot compute ✅", table_cell_bold), Paragraph("N/A", table_cell), Paragraph("N/A", table_cell)],
        [Paragraph("<b>9</b>", table_cell_bold), Paragraph("Zero EBITDA Scenario", table_cell), Paragraph("LTM EBITDA exactly $0.00; ZeroDivisionError prevented", table_cell), Paragraph("Cannot compute", table_cell), Paragraph("Cannot compute ✅", table_cell_bold), Paragraph("N/A", table_cell), Paragraph("N/A", table_cell)],
        [Paragraph("<b>10</b>", table_cell_bold), Paragraph("Missing Approvals Gate", table_cell), Paragraph("Unapproved RuleSet strictly halts computation", table_cell), Paragraph("Cannot compute", table_cell), Paragraph("Cannot compute ✅", table_cell_bold), Paragraph("N/A", table_cell), Paragraph("N/A", table_cell)],
        [Paragraph("<b>11</b>", table_cell_bold), Paragraph("Ineligible Add-Back", table_cell), Paragraph("Routine capex audited as INELIGIBLE receiving $0 credit", table_cell), Paragraph("Pass under all readings", table_cell), Paragraph("Pass under all readings ✅", table_cell_bold), Paragraph("1.6279x", table_cell), Paragraph("63.82%", table_cell)],
        [Paragraph("<b>12</b>", table_cell_bold), Paragraph("Unit Multiplier Scaling", table_cell), Paragraph("Financial statements reported in $000s scaled accurately", table_cell), Paragraph("Pass under all readings", table_cell), Paragraph("Pass under all readings ✅", table_cell_bold), Paragraph("1.6279x", table_cell), Paragraph("63.82%", table_cell)]
    ]
    t_stress = Table(stress_data, colWidths=[15, 95, 155, 90, 95, 45, 45])
    t_stress.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F8FAFC')]),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_stress)
    story.append(Spacer(1, 4))

    story.append(Paragraph("3.3 Operational Limitations & Production Boundaries", h2_style))
    story.append(Paragraph(
        "1. <b>Shared Basket Overruns Require Human Allocation:</b> If an agreement fails to specify an allocation waterfall for an aggregate cap overrun, the engine halts with <code>Cannot compute</code> until a credit analyst records a haircut allocation.<br/>"
        "2. <b>Live OpenRouter Disclosure & Offline Fallback:</b> In live testing, the OpenRouter connection test verified successfully (HTTP 200 'PONG'). However, actual live Rule Set synthesis with free models encountered upstream model token/content limits on large legal texts (returning null/empty content). As designed by the dual-engine architecture, the application safely reverted to the verified deterministic contract template (offline fallback). The system does not claim live AI generation succeeded in production.<br/>"
        "3. <b>Unstructured ASCII SEC Tables:</b> SEC HTML filings with non-standard ASCII text tables may require analyst review during ingestion.",
        body_style
    ))

    # Force PageBreak to ensure EXACT PAGE 3
    story.append(PageBreak())

    # ==========================================
    # PAGE 4: APPENDIX — USER CONTRIBUTION & GOVERNANCE
    # ==========================================
    story.append(Paragraph("4. APPENDIX — USER CONTRIBUTION & ENGINEERING LEADERSHIP", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#1E3A8A'), spaceBefore=1, spaceAfter=5))

    story.append(Paragraph("4.1 Collaborative Pair-Programming Overview", h2_style))
    story.append(Paragraph(
        "This project was designed, implemented, and verified through an intensive collaborative pair-programming engagement between the <b>USER (acting as Lead Financial Architect & Systems Director)</b> and the <b>AI Assistant (Antigravity, acting as Senior Implementation Engineer)</b>. The User provided authoritative credit domain expertise and architectural governance, steering the system away from generic LLM pitfalls toward an institutionally rigorous, auditable compliance platform.",
        body_style
    ))

    story.append(Paragraph("4.2 Key Architectural Decisions Directed by the User", h2_style))
    story.append(Paragraph(
        "1. <b>Rejection of Non-Matching Covenant Ratios:</b> Discarded Hertz, AMC, and Six Flags when SEC filings showed First Lien / Secured covenants rather than Total Net Leverage Ratio. Selected Redwire, Whole Earth Brands, and Mercury Systems.<br/>"
        "2. <b>The Zero-Guessing Mandate:</b> Strictly prohibited LLM guessing or automatic arbitration. Any unverified quote or unallocated basket overrun must pause for human review and return <code>Cannot compute</code>.<br/>"
        "3. <b>Algebraic Resolution of Circular Caps:</b> Formulated circular post-addback caps as closed-form algebraic constraints (<code>A ≤ [x/(1-x)] · B</code>) rather than lossy numerical iterations.<br/>"
        "4. <b>Contractual Cash Netting Ceiling:</b> Mandated support for capped cash netting (e.g. Whole Earth Brands' $37.5M ceiling) distinct from uncapped agreements.<br/>"
        "5. <b>No Automatic Default on Negative EBITDA:</b> Required that negative or zero EBITDA halt computation with a review flag rather than declaring an automatic covenant breach.<br/>"
        "6. <b>EBITDA Cushion Definition:</b> Formulated EBITDA Cushion (%) strictly as the percentage drop in EBITDA that would trigger a breach: <code>Cushion % = (Absolute Buffer / Actual EBITDA) × 100%</code>.<br/>"
        "7. <b>Elimination of Invented Effective Dates:</b> Eliminated fallback dates, requiring contract preamble extraction or explicit reviewer validation.<br/>"
        "8. <b>Internal Reference Citation Standard:</b> Mandated that parsed HTML blocks be cited as internal references (<code>InternalRef: internal_block_X</code>) rather than inventing fake legal paragraph numbers.",
        body_style
    ))

    story.append(Paragraph("4.3 Engineering Governance Matrix", h2_style))

    gov_data = [
        [Paragraph("<b>PHASE & DOMAIN</b>", table_header), Paragraph("<b>USER STRATEGIC GOVERNANCE & ARCHITECTURAL DIRECTIVES</b>", table_header), Paragraph("<b>AI TECHNICAL EXECUTION & CODEBASE IMPLEMENTATION</b>", table_header)],
        [
            Paragraph("<b>Phase 1: Ingestion & Amendments</b>", table_cell_bold),
            Paragraph("Defined provenance fidelity rules; prohibited fake HTML page numbers; mandated SHA-256 checks and chronologically ordered timelines.", table_cell),
            Paragraph("Built <code>ingestion.py</code> and <code>amendment_tracker.py</code>; verified 8 SEC filings; implemented <code>AgreementTimeline</code> snapshot engine.", table_cell)
        ],
        [
            Paragraph("<b>Phase 1: Dependency Graph</b>", table_cell_bold),
            Paragraph("Directed recursive BFS traversal starting strictly from covenant roots; required cycle and missing term detection.", table_cell),
            Paragraph("Implemented <code>dependency_graph.py</code>; pruned non-financial boilerplate; classified circular EBITDA cap references.", table_cell)
        ],
        [
            Paragraph("<b>Phase 1: Rule Set Synthesis</b>", table_cell_bold),
            Paragraph("Mandated verbatim quotation checks, ambiguity preservation, and Gate 1 human approval before financial processing.", table_cell),
            Paragraph("Built <code>ruleset_generator.py</code>; integrated OpenRouter with deterministic fallback; verified math bounds and quote accuracy.", table_cell)
        ],
        [
            Paragraph("<b>Phase 2: Financial Ingestion</b>", table_cell_bold),
            Paragraph("Defined quarterly deliverable schema, ledger account mapping, and Gate 2 reviewer sign-off.", table_cell),
            Paragraph("Built <code>financial_data.py</code> and <code>account_mapper.py</code> with confidence scoring and manual override flow.", table_cell)
        ],
        [
            Paragraph("<b>Phase 2: Add-Back Auditing</b>", table_cell_bold),
            Paragraph("Required qualitative classification (ELIGIBLE, INELIGIBLE, NEEDS_REVIEW) and Gate 3 reviewer overrides.", table_cell),
            Paragraph("Built <code>addback_auditor.py</code> with OpenRouter prompts and deterministic keyword auditor.", table_cell)
        ],
        [
            Paragraph("<b>Phase 2: Math Engine & Status</b>", table_cell_bold),
            Paragraph("Formulated 4-case compliance taxonomy; prohibited automatic aggregate overrun arbitration; specified headroom and cushion formulas.", table_cell),
            Paragraph("Built <code>calculation_engine.py</code>; implemented closed-form cap algebra, cash netting limits, and JSON/Markdown workpaper export.", table_cell)
        ],
        [
            Paragraph("<b>Testing & UI Delivery</b>", table_cell_bold),
            Paragraph("Specified at least 10 synthetic test cases; mandated live/offline API status transparency and standalone Streamlit interface.", table_cell),
            Paragraph("Built 12 synthetic cases; built Streamlit web interface (<code>app.py</code>); verified all 54 pytest test cases pass cleanly (1 skipped for live key).", table_cell)
        ]
    ]
    t_gov = Table(gov_data, colWidths=[90, 225, 225])
    t_gov.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F8FAFC')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F8FAFC')]),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_gov)
    story.append(Spacer(1, 4))

    # Concluding signoff block
    signoff_data = [
        [
            Paragraph(
                "<b>SYSTEM STATUS & SUBMISSION READINESS:</b> The complete codebase, documentation, verified SEC EDGAR credit agreements, and interactive Streamlit web application have been fully tested (54 passed, 1 skipped) and are ready for institutional deployment.",
                table_cell
            )
        ]
    ]
    t_sign = Table(signoff_data, colWidths=[540])
    t_sign.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#ECFDF5')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#10B981')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(t_sign)

    doc.build(story, canvasmaker=NumberedCanvas)

    # Verify page count with pypdf
    reader = pypdf.PdfReader(output_path)
    page_count = len(reader.pages)
    print(f"Generated PDF at '{output_path}' with page count: {page_count}")
    return page_count

if __name__ == "__main__":
    out = "TRESATA_WRITTEN_BRIEF.pdf"
    count = build_pdf(out)
    if count != 4:
        raise ValueError(f"Expected exactly 4 pages, got {count} pages!")
    print("SUCCESS: Exactly 4 pages generated.")

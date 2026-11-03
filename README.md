# AI-Assisted Credit Agreement Covenant Compliance Engine

An enterprise AI-assisted pair-programming system designed for corporate loan covenant compliance, focusing strictly on the **quarterly maintenance Total Net Leverage Ratio covenant**.

Built with a two-phase architecture:
- **Phase 1:** Credit Agreement & Amendment Digitization, Versioning, Dependency Graph, Rule Set Synthesis & Python Ground-Truth Verification.
- **Phase 2:** Quarterly Financial Package Ingestion, General Ledger Account Mapping (HITL Gate), Add-Back Auditing, 100% Deterministic Cap Arithmetic, 4-Case Compliance Status Determination, and Traceable Audit Workpaper Generation.

---

## 🏛️ Core Design Principles

1. **LLM Scope strictly limited to Semantic Interpretation:** The LLM (via OpenRouter's free router `openrouter/free` or active free models like `google/gemma-4-31b-it:free`) reads natural language contract clauses, suggests ledger classifications, and audits add-back eligibility.
2. **Zero LLM Math (100% Deterministic Python):** All numerical calculations—including circular post-EBITDA percentage caps, aggregate basket allocations, cash netting ceilings, rolling LTM EBITDA, leverage ratios, headroom, absolute buffers, and cushion percentages—are computed strictly in Python.
3. **Mandatory Human-in-the-Loop (HITL) Gates:**
   - **Gate 1:** Rule Set Verification & Approval (blocks calculation if unapproved).
   - **Gate 2:** General Ledger Account Mapping Approval (blocks calculation if unapproved).
   - **Gate 3:** Add-Back Audit Report Approval with Reviewer Override capability.
4. **4-Case Compliance Status:**
   - `Pass under all readings`
   - `Fail under all readings`
   - `Outcome depends on a reading` (ambiguous clauses evaluated across alternative readings)
   - `Cannot compute` (missing financial data, non-positive EBITDA, or pending human review)
5. **No Invented Changes or False Precision:**
   - All paragraph citations use explicit internal references (`InternalRef: internal_block_X`), never fake legal paragraph numbers.
   - HTML documents strictly enforce `page_number = None`.
   - Missing information or unallocated aggregate cap overruns are never guessed; they halt with `Cannot compute` and route to human review.

---

## 📁 Project Directory Structure

```text
covenant_compliance/
├── app.py                             # Interactive Streamlit Web Interface
├── README.md                          # Installation, running instructions, and architecture
├── TRESATA_WRITTEN_BRIEF.pdf          # 4-page publication-grade PDF brief (with 1-page appendix)
├── TRESATA_WRITTEN_BRIEF.md           # 4-page executive brief source markdown
├── requirements.txt                   # Free Python dependencies
├── scripts/
│   └── generate_pdf_brief.py          # PDF generation script ensuring exact 4-page layout
├── data/
│   ├── actual_agreements/             # Verified SEC EDGAR Credit Agreements & Amendments
│   │   ├── sources.json               # SHA-256 verified manifest of 8 EDGAR filings
│   │   ├── redwire/                   # Redwire Base (2020), Amendment 5 (2022), Amendment 6 (2023)
│   │   ├── whole_earth/               # Whole Earth Brands A&R (2021), First Amendment (2022)
│   │   └── mercury_systems/           # Mercury Systems Base (2016), Amendment 5 (2023), Amendment 6 (2024)
│   ├── synthetic_tests/               # 12 stress-test scenario definitions
│   │   └── synthetic_cases.json       # Exported JSON suite of all 12 cases
│   └── rulesets/                      # Serialized approved Rule Set storage
├── src/
│   ├── core/                          # Phase 1 Core Modules
│   │   ├── ingestion.py               # HTML/PDF parsing, provenance & SHA-256 verification
│   │   ├── amendment_tracker.py       # Amendment delta extraction, versioning & timeline
│   │   ├── dependency_graph.py        # Recursive DAG definition traversal & cycle detection
│   │   └── ruleset_generator.py       # RuleSet synthesis, verbatim quote check & Gate 1
│   ├── compliance/                    # Phase 2 Core Modules
│   │   ├── financial_data.py          # Quarterly financial models, ledger & validation
│   │   ├── account_mapper.py          # Ledger account mapping engine & Gate 2
│   │   ├── addback_auditor.py         # OpenRouter add-back auditor, override & Gate 3
│   │   ├── calculation_engine.py      # Deterministic math, 4-case logic & workpapers
│   │   └── synthetic_test_generator.py# 12 synthetic scenario generator
│   └── utils/
│       ├── hashing.py                 # SHA-256 computation and tamper detection
│       └── dates.py                   # ISO date parsing, textual extraction & quarter normalization
└── tests/                             # Comprehensive Automated Test Suite (55 tests: 54 passed, 1 skipped)
    ├── test_ingestion.py              # Tests document extraction, SHA-256 & provenance (7 tests)
    ├── test_amendment_tracker.py      # Tests REPLACE, INSERT, DELETE, dates & conflicts (11 tests)
    ├── test_dependency_graph.py       # Tests recursive DAG, cycles & boilerplate pruning (6 tests)
    ├── test_ruleset_generator.py      # Tests verbatim verification & math bounds (10 tests)
    ├── test_compliance_engine.py      # Tests all 12 synthetic cases & calculations (14 tests)
    └── test_openrouter_integration.py # Tests live probe, multi-field parsing & diagnostics (7 tests)
```

---

## ⚙️ Installation & Setup

### Prerequisites
- macOS, Linux, or Windows with **Python 3.10+** (developed and verified on Python 3.14).

### 1. Clone & Set Up Virtual Environment
```bash
git clone <repository-url>
cd covenant_compliance
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

*(Core dependencies: `pydantic>=2.10`, `beautifulsoup4`, `pypdf`, `reportlab`, `requests`, `pytest`, `streamlit`)*

---

## 🧪 Running the Complete Automated Test Suite

Run all automated unit and integration tests:
```bash
source .venv/bin/activate
pytest tests/ -v
```

### Test Suite Execution Output:
```text
======================== 54 passed, 1 skipped in 7.82s =========================
```

*(1 test is safely skipped when `OPENROUTER_API_KEY` is not exported in the shell environment; all 54 core unit and integration tests run offline).*

### Test Coverage Highlights:
- **`tests/test_ingestion.py` (7 tests):** Verifies all 8 SEC EDGAR documents match their SHA-256 hashes, HTML extracts without fake page numbers, tamper detection raises `IntegrityError`, and PDF page numbers are preserved.
- **`tests/test_amendment_tracker.py` (11 tests):** Verifies `REPLACE`, `INSERT`, and `DELETE` operations, rejects hallucinated quotes (`REJECTED_MISSING_QUOTE`), rejects non-existent targets (`REJECTED_TARGET_NOT_FOUND`), flags competing amendment conflicts (`REJECTED_CONFLICT`), and enforces quarter-by-quarter timeline retrieval.
- **`tests/test_dependency_graph.py` (6 tests):** Verifies recursive BFS traversal from roots, detects circular EBITDA add-back caps, flags unresolved definitions, prunes legal boilerplate (`Person`, `Business Day`), and builds real graphs for Redwire and Whole Earth Brands.
- **`tests/test_ruleset_generator.py` (10 tests):** Verifies verbatim quote matching against source documents, rejects hallucinated clauses, validates cap mathematical bounds, validates version ID and SHA-256 hash linkage, and verifies real Rule Sets for Redwire, Whole Earth, and Mercury Systems.
- **`tests/test_compliance_engine.py` (14 tests):** Verifies all 12 synthetic test cases, including circular post-EBITDA cap algebra, contractual \$37.5M cash netting cap, Redwire's shared aggregate 25% post-EBITDA cap ($A \le \frac{1}{3}B$) across clauses (a)(iv), (a)(vii), and Section 1.11(c)(ii), unallocated aggregate cap overruns (`Cannot compute`), negative/zero EBITDA, and human reviewer override workflows.
- **`tests/test_openrouter_integration.py` (7 tests):** Verifies OpenRouter response parsing across `content` (post-`</think>`), direct choice `text`, `tool_calls` arguments, `reasoning` channel fallback, empty content diagnostic error generation (`finish_reason='length'`), and model keyword argument passing.

---

## 🖥️ Running the Streamlit Web Application

To launch the web interface:
```bash
source .venv/bin/activate
streamlit run app.py
```

Open your browser at `http://localhost:8501`.

### Interface Navigation:
1. **Sidebar Configuration:**
   - **OpenRouter API Key Input:** Enter your free OpenRouter API key.
   - **Connection Tester:** Click *"Test OpenRouter Connection"* to verify latency and model access.
   - **Engine Status Pill:** Clearly displays `🟢 Live OpenRouter API Enabled` or `🟠 Offline Fallback Mode Active`.
2. **Mode 1: Quarterly Compliance Checking (Phase 2):**
   - Select any of the 12 synthetic financial scenarios or inspect real borrower submissions.
   - **Tab 1:** Review suggested ledger mappings and click *"Approve Account Mappings (Gate 2)"*.
   - **Tab 2:** Review audited add-backs, perform reviewer overrides, and click *"Approve Add-Back Audit (Gate 3)"*.
   - **Tab 3:** Click *"Calculate Covenant Compliance"* to view the 4-case compliance status badge, summary metrics (Leverage Ratio, Ratio Headroom, Absolute EBITDA Buffer, EBITDA Cushion %), cap breakdown table, and full audit workpaper with JSON export.
3. **Mode 2: Credit Agreements & Rule Sets (Phase 1):**
   - Inspect Redwire Corp, Whole Earth Brands, or Mercury Systems agreements.
   - Click *"⚡ Regenerate Rule Set (Live OpenRouter)"* to query the active OpenRouter model on demand, bypassing any cached results.
   - Check the generation source badge (`🟢 Live OpenRouter API` vs `🟠 Offline Fallback`) and view raw LLM snippets.
   - Inspect the recursive dependency graph, cycle classifications, and synthesized Rule Set with verbatim clauses.
   - Perform human reviewer sign-off (*Gate 1*).
4. **Mode 3: Synthetic Test Suite (12 Scenarios):**
   - Execute all 12 stress-test scenarios in one click and review the comprehensive compliance results table.

---

## 🔑 OpenRouter API Configuration

The application is configured to use verified active free OpenRouter models:
- **Primary Default Router:** `openrouter/free` (Free Models Router: automatically selects available free models with open capacity, preventing single-provider rate limits)
- **Direct Free Model Options:** `google/gemma-4-31b-it:free`, `nvidia/nemotron-3.5-lightning:free`, `google/gemma-4-26b-a4b-it:free`, `recursive/flint-v1.0:free`
- **Temperature:** `0.0` (zero creativity, strict schema adherence).

### Live vs. Offline Fallback Mode & Honest Live Status Disclosure:
- **Offline Fallback Mode (Default & Production Baseline):** If `OPENROUTER_API_KEY` is not set, or when live model calls fail to produce valid schema JSON, the system uses deterministic pattern extraction and verified contract templates. All calculations, verifications, and unit tests run 100% offline.
- **Live OpenRouter API Mode & Empirical Finding:** 
  - The live OpenRouter connection test succeeds (HTTP 200 with an exact `'PONG'` response) when an API key is entered in the sidebar.
  - However, during live Rule Set generation with free models (`openrouter/free`, `google/gemma-4-31b-it:free`, `nvidia/nemotron-3.5-lightning:free`), live model synthesis encountered upstream model token limits and content suppression on large 20k+ character contract contexts (returning null or empty content fields during reasoning).
  - As designed by the dual-engine architecture, the application safely and transparently reverted to the verified deterministic contract template (`Deterministic Contract Template (Offline Fallback)`).
  - **Important Disclosure:** The system does NOT claim live AI generation succeeded in production; all certified calculations and workpapers rely on the verified offline contract rules.

---

## 📊 Summary of the 12 Synthetic Test Scenarios

| # | Scenario Name | Core Condition Tested | Expected Status | Engine Status |
| :-: | :--- | :--- | :--- | :--- |
| **1** | Comfortably Compliant | Leverage 1.04x vs 4.50x covenant ceiling | `Pass under all readings` | `Pass under all readings` ✅ |
| **2** | Direct Covenant Breach | \$445M Net Debt on \$60M EBITDA (7.41x vs 4.50x) | `Fail under all readings` | `Fail under all readings` ✅ |
| **3** | Capped Add-back Haircut | Claimed \$25M restructuring on \$20M baseline (cap restricts to \$6.67M) | `Pass under all readings` | `Pass under all readings` ✅ |
| **4** | Circular Post-EBITDA Cap | Exact algebraic solution: $\frac{0.25}{0.75} \times \$30\text{M} = \$10,000,000$ limit | `Pass under all readings` | `Pass under all readings` ✅ |
| **5** | Contractual Cash Netting Cap | \$60M cash on hand capped at contractual \$37,500,000 ceiling | `Pass under all readings` | `Pass under all readings` ✅ |
| **6** | Unallocated Aggregate Overrun | Two categories (\$10M total) exceed \$6.67M basket without contractual priority | `Cannot compute` | `Cannot compute` ✅ |
| **7** | Outcome Depends on Reading | Ratio 4.50x under Reading A (Pass); 4.62x under Reading B (Fail) | `Outcome depends on a reading` | `Outcome depends on a reading` ✅ |
| **8** | Negative EBITDA Scenario | LTM EBITDA -\$55M; prevents division; flags for review | `Cannot compute` | `Cannot compute` ✅ |
| **9** | Zero EBITDA Scenario | LTM EBITDA exactly \$0.00; ZeroDivisionError prevented; flags for review | `Cannot compute` | `Cannot compute` ✅ |
| **10**| Missing Approvals Gate | Unapproved RuleSet blocks calculation immediately | `Cannot compute` | `Cannot compute` ✅ |
| **11**| Ineligible Add-Back Rejection | Disguised routine capex audited as `INELIGIBLE` receiving \$0 credit | `Pass under all readings` | `Pass under all readings` ✅ |
| **12**| Unit Multiplier Scaling | Statements reported in thousands (\$000s) scaled accurately to exact dollars | `Pass under all readings` | `Pass under all readings` ✅ |

---

## ⚠️ Known Limitations & Boundaries

1. **Live Free LLM Context Limitations:** Free-tier OpenRouter models currently struggle to emit complete 20-field JSON Rule Sets across full 20,000-character credit agreement contexts, triggering offline fallback. Production institutional deployments should use dedicated frontier models (e.g., Claude 3.5 Sonnet or GPT-4o) with extended output token limits.
2. **Complex Embedded Tables in Non-Standard SEC Filings:** While standard EDGAR HTML tables and sections parse reliably, highly idiosyncratic SEC filings featuring irregular ASCII text formatting may require human review to confirm table column boundaries.
3. **No Automatic Priority Arbitration:** If a contract specifies an aggregate cap across multiple add-back categories without an explicit priority waterfall, the engine halts with `Cannot compute` and requires a human reviewer to allocate the haircut.
4. **Shared 25% Aggregate Basket Mechanics:** Under Redwire's credit agreement, clauses (a)(iv), (a)(vii)(A), (a)(vii)(B), and Section 1.11(c)(ii) pro forma synergies share a single post-EBITDA 25% cap ($A \le \frac{1}{3} B$). Any overrun across these combined categories requires reviewer allocation before calculating leverage.

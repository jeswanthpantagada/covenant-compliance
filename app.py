"""
Streamlit Web Interface for AI-Assisted Covenant Compliance System.

Provides an interactive workflow across both phases:
Phase 1:
- Agreement Versioning & Timeline Explorer
- Dependency Graph Visualization
- Rule Set Verification & Human Review Gate 1 (Sign-off)

Phase 2:
- Quarterly Financial Statement & Ledger Review
- Account Mapping with Human Review Gate 2
- Add-Back Auditing (OpenRouter + Fallback) with Reviewer Override & Gate 3
- Deterministic Calculation Engine (Headroom, Absolute Buffer, Cushion %)
- 4-Case Compliance Status & Traceable Audit Workpaper Export
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
import streamlit as st

from src.core.ingestion import DocumentIngestionService
from src.core.amendment_tracker import build_initial_version_from_doc, AgreementTimeline, OPENROUTER_CONFIG
from src.core.dependency_graph import DependencyGraphBuilder
from src.core.ruleset_generator import RuleSetGenerator, RuleSetVerificationEngine, RuleSet
from src.compliance.financial_data import QuarterlyFinancialPackage
from src.compliance.account_mapper import AccountMapper
from src.compliance.addback_auditor import AddBackAuditor, AddBackEligibilityStatus
from src.compliance.calculation_engine import ComplianceCalculationEngine, ComplianceStatus
from src.compliance.synthetic_test_generator import build_synthetic_suite

# Set page config
st.set_page_config(
    page_title="Covenant Compliance Engine",
    page_icon="⚖️",
    layout="wide"
)

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCES_MANIFEST = PROJECT_ROOT / "data" / "actual_agreements" / "sources.json"


@st.cache_resource
def load_sources_data():
    if SOURCES_MANIFEST.is_file():
        with open(SOURCES_MANIFEST, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@st.cache_resource
def load_synthetic_suite_data():
    return build_synthetic_suite()


def _extract_openrouter_reply(data: dict):
    """
    Safely extracts the final content response from an OpenRouter completion.
    Strictly ignores the 'reasoning' field.
    Returns (True, content_str) if valid final content exists,
    or (False, error_msg) if no final response was returned.
    """
    if not isinstance(data, dict):
        return False, "Received non-dictionary response from OpenRouter API."
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        return False, "Model returned no choices in response."
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return False, "Model returned invalid choice structure."
    msg = first_choice.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if content is not None and str(content).strip():
            return True, str(content).strip()
        else:
            return False, "Model returned no final response (content field was empty or null)."
    elif isinstance(msg, str) and msg.strip():
        return True, msg.strip()
    
    # Direct text field fallback (completions endpoint)
    text = first_choice.get("text")
    if text is not None and str(text).strip():
        return True, str(text).strip()
        
    return False, "Model returned no final response content."


def _extract_openrouter_error(resp) -> tuple[str, bool]:
    """
    Safely extracts error message and determines if it is an upstream provider issue vs account limit.
    Returns (error_message, is_provider_issue).
    """
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            err_msg = ""
            if isinstance(err, dict):
                err_msg = err.get("message") or err.get("code") or str(err)
            elif isinstance(err, str):
                err_msg = err
            elif "message" in data:
                err_msg = str(data["message"])
            else:
                err_msg = str(data)[:150]
                
            err_lower = err_msg.lower()
            is_account = any(k in err_lower for k in ["user", "account", "key", "daily limit", "credit limit", "tier limit"])
            is_provider = any(k in err_lower for k in ["provider", "upstream", "capacity", "concurrency", "busy", "queue", "temporarily unavailable"]) or not is_account
            
            if resp.status_code == 429:
                if is_account:
                    return f"Account Limit (HTTP 429): {err_msg}", False
                else:
                    return f"Provider Capacity Limit (HTTP 429): The upstream provider for this model is temporarily congested with global requests ({err_msg}). Use 'openrouter/free' to auto-route to an available model with capacity.", True
            return err_msg, is_provider
        return str(data)[:150], False
    except Exception:
        text = resp.text[:150] if hasattr(resp, "text") and resp.text else f"HTTP Error {resp.status_code}"
        return text, False


def test_openrouter_connection(api_key: str, model: str = None):
    """
    Pings OpenRouter API with a minimal probe using the provided API key.
    Tests against the specified free model (defaults to OPENROUTER_CONFIG['primary_model']).
    Expects strictly 'PONG' as the final content response.
    Marks successful ONLY when valid final response is received (ignoring reasoning).
    If a specific model encounters provider rate limits (429), automatically retries with 'openrouter/free'.
    Does not expose or log the API key.
    """
    if not api_key or not api_key.strip():
        return False, "No API key provided."
    import requests, time, traceback
    clean_key = api_key.strip()
    target_model = model or OPENROUTER_CONFIG["primary_model"]
    
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json",
        **OPENROUTER_CONFIG["headers"]
    }
    
    # 1. Auth check via OpenRouter auth endpoint
    auth_url = OPENROUTER_CONFIG.get("auth_url", "https://openrouter.ai/api/v1/auth/key")
    try:
        auth_resp = requests.get(auth_url, headers=headers, timeout=10)
        if auth_resp.status_code == 401:
            err, _ = _extract_openrouter_error(auth_resp)
            return False, f"Authentication Failed (HTTP 401): {err}"
    except Exception:
        # If auth endpoint has a transient issue, proceed directly to chat completion probe
        pass

    # 2. Test chat completion probe expecting exactly 'PONG' in final response
    url = OPENROUTER_CONFIG["api_url"]
    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": "You are a test ping bot. Respond with ONLY the single word PONG. Do not add punctuation, reasoning, or any other words."},
            {"role": "user", "content": "Respond with exactly 'PONG'."}
        ],
        "max_tokens": 150,
        "temperature": 0.0,
        "include_reasoning": False
    }
    t0 = time.time()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        elapsed = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            has_content, reply = _extract_openrouter_reply(data)
            if not has_content:
                return False, f"Model Error: {reply} (No final answer was generated. Please choose another active free model or retry)."
            
            # Verify exact PONG requirement
            cleaned_reply = reply.strip().strip("'\"").strip()
            if cleaned_reply.upper() == "PONG":
                actual_model = data.get("model", target_model)
                return True, f"Connected to '{target_model}' (routed to: {actual_model}) in {elapsed:.2f}s | Response: '{cleaned_reply}'"
            else:
                return False, f"Unexpected final response from '{target_model}': '{reply}'. Expected exactly 'PONG'."
        elif resp.status_code == 429:
            err_msg, is_provider = _extract_openrouter_error(resp)
            # If a specific single model was congested, auto-try openrouter/free router!
            if target_model != "openrouter/free" and is_provider:
                time.sleep(1.0)
                fb_payload = {**payload, "model": "openrouter/free"}
                try:
                    fb_resp = requests.post(url, json=fb_payload, headers=headers, timeout=15)
                    fb_elapsed = time.time() - t0
                    if fb_resp.status_code == 200:
                        fb_data = fb_resp.json()
                        fb_has_content, fb_reply = _extract_openrouter_reply(fb_data)
                        if fb_has_content and fb_reply.strip().strip("'\"").strip().upper() == "PONG":
                            actual_model = fb_data.get("model", "auto-selected free model")
                            return True, f"Connected via 'openrouter/free' (routed to: {actual_model}) after '{target_model}' hit provider capacity | Response: 'PONG'"
                except Exception:
                    pass
            return False, err_msg
        else:
            err, _ = _extract_openrouter_error(resp)
            if resp.status_code == 404:
                return False, f"Model Not Found (HTTP 404): {err}. Please select another active free model."
            elif resp.status_code == 401:
                return False, f"Authentication Failed (HTTP 401): {err}"
            return False, f"HTTP Error {resp.status_code}: {err}"
    except requests.exceptions.Timeout:
        return False, "Connection timed out after 15 seconds. Please try again."
    except Exception as e:
        tb = traceback.format_exc()
        if clean_key:
            tb = tb.replace(clean_key, "[REDACTED_API_KEY]")
        print(f"[OpenRouter Connection Probe Traceback]:\n{tb}")
        return False, f"Network request error ({type(e).__name__}): {str(e)}"


# Sidebar: LLM Configuration & Agreement Selector
st.sidebar.title("⚖️ System Configuration")

# Initialize session state for connection verification
import hashlib
if "openrouter_verified" not in st.session_state:
    st.session_state["openrouter_verified"] = False
if "verified_key_hash" not in st.session_state:
    st.session_state["verified_key_hash"] = ""
if "openrouter_conn_msg" not in st.session_state:
    st.session_state["openrouter_conn_msg"] = ""
if "selected_model" not in st.session_state:
    st.session_state["selected_model"] = OPENROUTER_CONFIG["primary_model"]

api_key_input = st.sidebar.text_input(
    "OpenRouter API Key",
    type="password",
    value=os.environ.get("OPENROUTER_API_KEY", ""),
    help="Enter free OpenRouter API key for live LLM extraction/auditing."
)
clean_key = api_key_input.strip()

# Invalidate verification if the key text was changed or cleared
current_key_hash = hashlib.sha256(clean_key.encode("utf-8")).hexdigest() if clean_key else ""
if current_key_hash != st.session_state.get("verified_key_hash"):
    st.session_state["openrouter_verified"] = False
    st.session_state["openrouter_conn_msg"] = ""

# Dropdown of active free models with openrouter/free router as recommended default
free_model_options = [
    "openrouter/free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-26b-a4b-it:free",
    "recursive/flint-v1.0:free"
]
selected_model = st.sidebar.selectbox(
    "Free Model Selection",
    options=free_model_options,
    index=0,
    help="'openrouter/free' automatically load-balances and routes to whichever free model is online and has available capacity, avoiding single-provider rate limits."
)
st.session_state["selected_model"] = selected_model
OPENROUTER_CONFIG["primary_model"] = selected_model

# Test Connection Button
if st.sidebar.button("Test OpenRouter Connection"):
    if not clean_key:
        st.sidebar.error("Please enter your OpenRouter API key above first.")
        st.session_state["openrouter_verified"] = False
    else:
        with st.sidebar.spinner(f"Testing live connection to {selected_model}..."):
            is_ok, msg = test_openrouter_connection(clean_key, selected_model)
            if is_ok:
                st.session_state["openrouter_verified"] = True
                st.session_state["verified_key_hash"] = current_key_hash
                st.session_state["openrouter_conn_msg"] = msg
                os.environ["OPENROUTER_API_KEY"] = clean_key
                st.sidebar.success(f"✅ Connection OK!\n{msg}")
            else:
                st.session_state["openrouter_verified"] = False
                st.session_state["openrouter_conn_msg"] = msg
                st.sidebar.error(f"❌ Connection Failed:\n{msg}")

# Sidebar status indicator
if st.session_state.get("openrouter_verified"):
    st.sidebar.success(f"🟢 **Live API Verified & Enabled**\nActive Model: `{selected_model}`")
elif clean_key:
    st.sidebar.warning("🟡 **Key Entered (Unverified)**\nClick 'Test OpenRouter Connection' to verify.")
else:
    st.sidebar.info("🟠 **Offline Fallback Mode Active**")

st.sidebar.caption(f"**Primary Model:** `{selected_model}`")
st.sidebar.caption(f"**Fallback Model:** `{OPENROUTER_CONFIG['fallback_model']}`")

# Active key for downstream modules (only passed if live connection is verified)
active_api_key = clean_key if st.session_state.get("openrouter_verified") else None

sources = load_sources_data()
synthetic_suite = load_synthetic_suite_data()

app_mode = st.sidebar.radio(
    "Choose Mode:",
    ["Quarterly Compliance Checking (Phase 2)", "Credit Agreement & Rule Sets (Phase 1)", "Synthetic Test Suite (12 Scenarios)"]
)

st.title("🛡️ AI-Assisted Covenant Compliance System")
st.markdown("### Total Net Leverage Ratio Maintenance Covenant Verification")

# Main page banner: ONLY show Live API Enabled after successful connection
if st.session_state.get("openrouter_verified"):
    st.success(
        f"🟢 **LLM Engine Status: Live OpenRouter API Verified & Enabled** (`{st.session_state.get('selected_model')}`)\n\n"
        f"_{st.session_state.get('openrouter_conn_msg')}_"
    )
elif clean_key:
    st.warning(
        f"🟡 **LLM Engine Status: API Key Entered — Connection Not Verified**\n\n"
        f"An API key is present in the sidebar, but the live connection to `{selected_model}` has not been verified yet. "
        "The system is safely operating in **Deterministic Offline Fallback Mode** until verified. "
        "Click **'Test OpenRouter Connection'** in the sidebar to test and activate live inference."
    )
else:
    st.info(
        "🟠 **LLM Engine Status: Offline Fallback Mode Active** (Deterministic Pattern Extraction & Rule Enforcement). "
        "Enter an OpenRouter API key in the sidebar and click **'Test OpenRouter Connection'** to enable live model inference."
    )

# ==============================================================================
# MODE 1: QUARTERLY COMPLIANCE CHECKING (PHASE 2)
# ==============================================================================
if app_mode == "Quarterly Compliance Checking (Phase 2)":
    st.subheader("Quarterly Financial Ingestion, Review & Compliance Execution")

    # Select Synthetic Case or Upload Custom
    scenario_names = [s["case_name"] for s in synthetic_suite]
    selected_case_name = st.selectbox("Select Financial Scenario:", scenario_names)
    selected_scenario = next(s for s in synthetic_suite if s["case_name"] == selected_case_name)

    pkg: QuarterlyFinancialPackage = selected_scenario["package"]
    ruleset: RuleSet = selected_scenario["ruleset"]

    # Reviewer identity
    reviewer_name = st.text_input("Reviewer Name (HITL Gate):", value="Senior Credit Analyst")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Company", pkg.company)
    with col2:
        st.metric("Quarter / Date", f"{pkg.quarter} ({pkg.period_end_date})")
    with col3:
        st.metric("Covenant Limit", f"{ruleset.covenant_rule.schedule[0].ratio_ceiling:.2f}:1.00")

    tabs = st.tabs(["1. Account Mappings (Gate 2)", "2. Add-Back Audit (Gate 3)", "3. Compliance Workpaper"])

    # TAB 1: Account Mapping
    with tabs[0]:
        st.markdown("#### General Ledger Account Mappings")
        mapper = AccountMapper()
        plan = mapper.suggest_mappings(pkg, ruleset)

        mapping_rows = []
        for e in plan.entries:
            mapping_rows.append({
                "Account Code": e.account_code,
                "Account Name": e.account_name,
                "Statement": e.statement_type.value,
                "Reported Balance ($)": f"{e.balance:,.2f}",
                "Mapped Category": e.mapped_target,
                "Confidence": e.confidence.value,
                "Rationale": e.rationale
            })
        st.dataframe(mapping_rows, use_container_width=True)

        if not plan.is_approved:
            if st.button("✅ Approve Account Mappings (Gate 2)", key="btn_app_map"):
                plan.approve(reviewer_name)
                st.session_state[f"map_approved_{pkg.package_id}"] = True
                st.success(f"Account mappings approved by {reviewer_name} at {plan.approval_timestamp}")

        is_map_approved = st.session_state.get(f"map_approved_{pkg.package_id}", plan.is_approved)
        if is_map_approved:
            plan.is_approved = True

    # TAB 2: Add-Back Audit
    with tabs[1]:
        st.markdown("#### Claimed Add-Backs Auditing")
        
        # Cache audit report in session state to prevent redundant LLM requests on widget interactions
        audit_cache_key = f"audit_report_{pkg.package_id}_{ruleset.ruleset_id}_{active_api_key is not None}"
        
        c_head1, c_head2 = st.columns([5, 1])
        with c_head2:
            rerun_audit = st.button("🔄 Re-Audit", help="Triggers fresh LLM audit call.")
            
        if rerun_audit or audit_cache_key not in st.session_state:
            auditor = AddBackAuditor(api_key=active_api_key)
            audit_report = auditor.audit_addbacks(pkg.package_id, pkg.claimed_add_backs, ruleset)
            st.session_state[audit_cache_key] = audit_report
        else:
            audit_report = st.session_state[audit_cache_key]

        if audit_report.audited_items:
            audit_rows = []
            for item in audit_report.audited_items:
                audit_rows.append({
                    "Addback ID": item.addback_id,
                    "Category": item.category,
                    "Claimed ($)": f"{item.claimed_amount:,.2f}",
                    "Eligible ($)": f"{item.eligible_amount:,.2f}",
                    "Status": item.status.value,
                    "Audit Rationale": item.audit_rationale
                })
            st.dataframe(audit_rows, use_container_width=True)
        else:
            st.info("No discretionary add-backs submitted for this quarter.")

        if not audit_report.is_approved:
            if st.button("✅ Approve Add-Back Audit Report (Gate 3)", key="btn_app_audit"):
                audit_report.approve(reviewer_name)
                st.session_state[f"audit_approved_{pkg.package_id}"] = True
                st.success(f"Add-back audit report approved by {reviewer_name}")

        is_audit_approved = st.session_state.get(f"audit_approved_{pkg.package_id}", audit_report.is_approved)
        if is_audit_approved:
            audit_report.is_approved = True

    # TAB 3: Compliance Execution
    with tabs[2]:
        st.markdown("#### Deterministic Compliance Execution & Audit Workpaper")
        engine = ComplianceCalculationEngine()

        if st.button("🚀 Calculate Covenant Compliance", type="primary"):
            wp = engine.calculate_compliance(
                package=pkg,
                ruleset=ruleset,
                mapping_plan=plan,
                audit_report=audit_report
            )

            # Display Status Banner
            status = wp.overall_status
            if status == ComplianceStatus.PASS_UNDER_ALL_READINGS:
                st.success(f"### Result: {status.value}")
            elif status == ComplianceStatus.FAIL_UNDER_ALL_READINGS:
                st.error(f"### Result: {status.value}")
            elif status == ComplianceStatus.OUTCOME_DEPENDS_ON_A_READING:
                st.warning(f"### Result: {status.value}")
            else:
                st.error(f"### Result: {status.value}")

            # Key Metric Cards
            m = wp.summary_metrics
            if m:
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.metric("Consolidated Net Debt", f"${m.get('Consolidated_Net_Debt', 0):,.2f}")
                with c2:
                    st.metric("LTM Consolidated EBITDA", f"${m.get('LTM_EBITDA', 0):,.2f}")
                with c3:
                    ratio = m.get('Actual_Total_Net_Leverage_Ratio')
                    st.metric("Total Net Leverage Ratio", f"{ratio:.4f}x" if ratio is not None else "N/A")
                with c4:
                    hr = m.get('Ratio_Headroom')
                    st.metric("Ratio Headroom", f"{hr:.4f}x" if hr is not None else "N/A")

                c5, c6, c7, c8 = st.columns(4)
                with c5:
                    cush = m.get('EBITDA_Cushion_Pct')
                    st.metric("EBITDA Cushion (%)", f"{cush:.2f}%" if cush is not None else "N/A")
                with c6:
                    buf = m.get('Absolute_EBITDA_Buffer')
                    st.metric("Absolute EBITDA Buffer", f"${buf:,.2f}" if buf is not None else "N/A")
                with c7:
                    st.metric("Cash Netted", f"${m.get('Cash_Netted', 0):,.2f}")
                with c8:
                    st.metric("Capped Addbacks Allowed", f"${m.get('Capped_Addbacks_Allowed', 0):,.2f}")

            # Cap Calculations
            if wp.cap_breakdowns:
                st.markdown("##### Cap Calculations Breakdown")
                cap_rows = []
                for c in wp.cap_breakdowns:
                    cap_rows.append({
                        "Cap ID": c.cap_id,
                        "Type": c.cap_type,
                        "Eligible Claimed": f"${c.eligible_claimed:,.2f}",
                        "Computed Limit": f"${c.computed_limit:,.2f}",
                        "Allowed": f"${c.allowed_amount:,.2f}",
                        "Haircut": f"${c.haircut_amount:,.2f}",
                        "Formula": c.formula_used,
                        "Flagged for Review": "YES" if c.flagged_for_human_review else "NO"
                    })
                st.table(cap_rows)

            # Human Review Flags
            if wp.human_review_flags:
                st.warning(f"**Human Review Notifications:**\n- " + "\n- ".join(wp.human_review_flags))

            # Full Workpaper Table
            st.markdown("##### Detailed EBITDA Waterfall Workpaper")
            wp_rows = []
            for w in wp.ebitda_waterfall_lines:
                wp_rows.append({
                    "Category": w.category,
                    "Line Item": w.line_name,
                    "Reported ($)": f"${w.reported_value:,.2f}",
                    "Allowed ($)": f"${w.allowed_value:,.2f}",
                    "Variance ($)": f"${w.variance:,.2f}",
                    "Contract Provenance": w.contract_citation,
                    "Audit Notes": w.notes
                })
            st.dataframe(wp_rows, use_container_width=True)

            # Download Workpaper JSON
            st.download_button(
                label="📥 Download Audit Workpaper (JSON)",
                data=wp.model_dump_json(indent=2),
                file_name=f"workpaper_{pkg.package_id}.json",
                mime="application/json"
            )


# ==============================================================================
# MODE 2: CREDIT AGREEMENTS & RULE SETS (PHASE 1)
# ==============================================================================
elif app_mode == "Credit Agreement & Rule Sets (Phase 1)":
    st.subheader("Credit Agreement Digitization, Dependency Graph & Rule Set Synthesis")

    company_options = ["Redwire Corporation", "Whole Earth Brands, Inc.", "Mercury Systems, Inc."]
    col_sel, col_btn = st.columns([3, 2])
    with col_sel:
        selected_company = st.selectbox("Select Public Company Credit Agreement:", company_options)

    # Ingestion & In-Memory Version Creation
    svc = DocumentIngestionService()
    doc_meta_map = {
        "Redwire Corporation": "redwire_credit_agreement_2020",
        "Whole Earth Brands, Inc.": "whole_earth_ar_credit_agreement_2021",
        "Mercury Systems, Inc.": "mercury_systems_credit_agreement_2016"
    }
    doc_id = doc_meta_map[selected_company]
    meta = next(d for d in sources if d["doc_id"] == doc_id)

    doc = svc.ingest_document(PROJECT_ROOT / meta["relative_path"], meta["doc_id"], meta["sha256"], meta)
    v1 = build_initial_version_from_doc(doc, meta["company"], f"{meta['ticker']}_v1")

    # Dependency Graph
    builder = DependencyGraphBuilder(max_depth=2)
    graph = builder.build_graph(v1)

    with col_btn:
        st.write("")
        st.write("")
        regen_live = st.button(
            "⚡ Regenerate Rule Set (Live OpenRouter)",
            type="primary",
            help="Bypasses any cached or offline result and queries the active OpenRouter model directly using the agreement definitions."
        )

    # Safe helper for ruleset source
    def _get_ruleset_source(rs: Any) -> str:
        if rs is None:
            return "Deterministic Contract Template (Offline Fallback)"
        return getattr(rs, "generation_source", None) or "Deterministic Contract Template (Offline Fallback)"

    # Rule Set Generation & Verification (with cache-bypass support)
    ruleset_cache_key = f"ruleset_{v1.version_id}"
    ruleset_status_key = f"ruleset_status_{v1.version_id}"

    if regen_live:
        # User explicitly requested live regeneration
        if not clean_key:
            st.error("❌ No OpenRouter API key provided. Please enter and test your API key in the sidebar first.")
            generator = RuleSetGenerator(api_key=None)
            ruleset = generator.generate_ruleset(graph, v1)
            st.session_state[ruleset_status_key] = ("offline", "No API key — using verified offline synthesizer.")
        else:
            with st.spinner(f"Calling live OpenRouter model `{selected_model}` to synthesize Rule Set..."):
                try:
                    generator = RuleSetGenerator(api_key=clean_key)
                    # Directly invoke live synthesis method with user-selected model
                    ruleset = generator._call_openrouter_synthesis(graph, v1, model=selected_model)
                    if hasattr(ruleset, "generation_source"):
                        ruleset.generation_source = f"Live OpenRouter API ({selected_model})"
                    st.session_state[ruleset_cache_key] = ruleset
                    st.session_state[ruleset_status_key] = (
                        "live",
                        f"Successfully synthesized via live OpenRouter model `{selected_model}`"
                    )
                    st.success(f"✅ Rule Set successfully generated using live model: `{selected_model}`!")
                    if getattr(ruleset, "raw_llm_response", None):
                        with st.expander("🔍 View Raw Model Response Snippet", expanded=False):
                            st.code(ruleset.raw_llm_response[:1000], language="json")
                except Exception as e:
                    st.error(f"❌ Live OpenRouter model call failed: {str(e)}. Reverting to verified offline synthesizer.")
                    generator = RuleSetGenerator(api_key=None)
                    ruleset = generator.generate_ruleset(graph, v1)
                    if hasattr(ruleset, "generation_source"):
                        ruleset.generation_source = "Deterministic Contract Template (Offline Fallback)"
                    st.session_state[ruleset_status_key] = (
                        "fallback",
                        f"Live LLM call failed ({str(e)}) — using verified offline synthesizer."
                    )
        st.session_state[ruleset_cache_key] = ruleset

    elif ruleset_cache_key not in st.session_state:
        generator = RuleSetGenerator(api_key=active_api_key)
        ruleset = generator.generate_ruleset(graph, v1, model=selected_model)
        st.session_state[ruleset_cache_key] = ruleset
        if active_api_key and "Live OpenRouter" in _get_ruleset_source(ruleset):
            st.session_state[ruleset_status_key] = (
                "live",
                f"Synthesized via live OpenRouter model `{selected_model}`"
            )
        else:
            st.session_state[ruleset_status_key] = (
                "offline",
                "Loaded via Deterministic Contract Synthesizer (Offline Fallback)"
            )
    else:
        ruleset = st.session_state[ruleset_cache_key]

    # Safe retrieval of generation source string
    cur_source = _get_ruleset_source(ruleset)
    is_live_ruleset = "Live OpenRouter" in cur_source

    # Verification Engine check
    verifier = RuleSetVerificationEngine()
    ver_res = verifier.verify_ruleset(ruleset, v1)

    # Status Banner showing whether live LLM was used
    status_kind, _ = st.session_state.get(ruleset_status_key, ("live" if is_live_ruleset else "offline", cur_source))

    if status_kind == "live" or is_live_ruleset:
        st.success(f"🟢 **Rule Set Generation Mode:** `{cur_source}` | Direct Model Output Verified")
    else:
        st.info(f"🟠 **Rule Set Generation Mode:** `{cur_source}` | Click *'⚡ Regenerate Rule Set (Live OpenRouter)'* above to re-query the live LLM.")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Agreement Date", v1.effective_date)
    with c2:
        st.metric("Graph Nodes Traced", len(graph.nodes))
    with c3:
        st.metric("EBITDA Add-Back Rules", len(ruleset.add_backs))
    with c4:
        st.metric(
            "Synthesis Engine",
            "Live OpenRouter 🟢" if is_live_ruleset else "Offline Fallback 🟠"
        )

    t1, t2, t3 = st.tabs(["Synthesized Rule Set", "Dependency Graph", "Human Review Approval (Gate 1)"])

    with t1:
        st.markdown(f"#### RuleSet ID: `{ruleset.ruleset_id}`")
        st.markdown(f"**Generation Source:** `{cur_source}`")
        st.markdown(f"**Starting Metric:** `{ruleset.starting_metric}`")
        st.markdown(f"**Net Debt Model:** `{ruleset.net_debt_rule.cash_netting_type}` (Cap: `{ruleset.net_debt_rule.cash_cap_amount}`)")
        st.markdown(f"**Covenant Ceiling:** `{ruleset.covenant_rule.schedule[0].ratio_ceiling}:1.00`")
        st.markdown(f"**Python Verbatim Check:** {'PASSED ✅' if ver_res.is_valid else 'FAILED ❌'}")

        raw_llm = getattr(ruleset, "raw_llm_response", None)
        if raw_llm:
            with st.expander("🔍 View Raw LLM Response Snippet", expanded=False):
                st.code(raw_llm, language="json")

        st.markdown("##### Permitted Add-Backs (Additions)")
        rs_rows = []
        for ab in ruleset.add_backs:
            cap_str = f"{ab.cap.cap_type.value} ({ab.cap.percent_limit or ab.cap.dollar_limit})" if ab.is_subject_to_cap and ab.cap else "None"
            rs_rows.append({
                "Rule ID": ab.rule_id,
                "Category": ab.category,
                "Description": ab.description,
                "Cap": cap_str,
                "Verbatim Contract Clause": ab.verbatim_clause[:100] + "...",
                "Citation": ab.citation
            })
        st.dataframe(rs_rows, use_container_width=True)

        if ruleset.deductions:
            st.markdown("##### Permitted Deductions (Subtractions)")
            ded_rows = []
            for d in ruleset.deductions:
                ded_rows.append({
                    "Rule ID": d.rule_id,
                    "Category": d.category,
                    "Description": d.description,
                    "Verbatim Contract Clause": d.verbatim_clause[:100] + "...",
                    "Citation": d.citation
                })
            st.dataframe(ded_rows, use_container_width=True)

        if ruleset.interpretations:
            st.markdown("##### Preserved Contractual Ambiguities & Interpretations")
            for interp in ruleset.interpretations:
                st.info(
                    f"**{interp.clause_reference}**: {interp.issue_description}\n\n"
                    f"- **Reading A**: {interp.interpretation_a.get('reading', '')}\n"
                    f"- **Reading B**: {interp.interpretation_b.get('reading', '')}"
                )

    with t2:
        st.markdown("#### Covenant Dependency Hierarchy")
        st.markdown(f"**Identified Cycles:** `{len(graph.cycles)}` | **Unresolved Terms:** `{len(graph.unresolved_terms)}` | **Pruned Terms:** `{len(graph.pruned_terms)}`")
        if graph.cycles:
            st.markdown("##### Detected Circularities")
            for cyc in graph.cycles:
                st.code(f"{' -> '.join(cyc.cycle)} [{cyc.cycle_type}]")
                if cyc.mathematical_solution:
                    st.caption(f"Closed-form solution: `{cyc.mathematical_solution}`")

    with t3:
        st.markdown("#### Human Review Gate 1: Rule Set Sign-off")
        reviewer = st.text_input("Reviewer Name:", value="Senior Credit Analyst", key="gate1_name")
        if not ruleset.is_approved:
            if st.button("✅ Approve and Lock Rule Set for Engine", key="btn_app_rs"):
                ruleset.approve(reviewer)
                st.session_state[f"rs_approved_{ruleset.ruleset_id}"] = True
                st.success(f"Rule Set approved by {reviewer} at {ruleset.approval_timestamp}")
        is_approved = st.session_state.get(f"rs_approved_{ruleset.ruleset_id}", ruleset.is_approved)
        if is_approved:
            st.info(f"Status: **APPROVED** by {reviewer}")


# ==============================================================================
# MODE 3: SYNTHETIC TEST SUITE (12 SCENARIOS)
# ==============================================================================
elif app_mode == "Synthetic Test Suite (12 Scenarios)":
    st.subheader("Synthetic Test Suite Runner (12 Stress-Test Cases)")
    st.markdown("Runs all 12 synthetic financial scenarios through the entire Phase 2 pipeline.")

    if st.button("⚡ Run All 12 Synthetic Scenarios", type="primary"):
        results_rows = []
        engine = ComplianceCalculationEngine()
        mapper = AccountMapper()
        auditor = AddBackAuditor(api_key=active_api_key)

        for s in synthetic_suite:
            c_name = s["case_name"]
            exp_status = s["expected_status"]
            pkg_case = s["package"]
            rs_case = s["ruleset"]

            plan = mapper.suggest_mappings(pkg_case, rs_case)
            if rs_case.is_approved:
                plan.approve("Automated Test Reviewer")

            audit = auditor.audit_addbacks(pkg_case.package_id, pkg_case.claimed_add_backs, rs_case)
            if rs_case.is_approved:
                audit.approve("Automated Test Reviewer")

            wp = engine.calculate_compliance(pkg_case, rs_case, plan, audit)

            actual_status = wp.overall_status.value
            match = "PASS ✅" if actual_status == exp_status else "FAIL ❌"

            results_rows.append({
                "Scenario": c_name,
                "Expected Status": exp_status,
                "Actual Status": actual_status,
                "Result": match,
                "Ratio": f"{wp.summary_metrics.get('Actual_Total_Net_Leverage_Ratio', 'N/A')}",
                "Headroom": f"{wp.summary_metrics.get('Ratio_Headroom', 'N/A')}",
                "EBITDA Cushion": f"{wp.summary_metrics.get('EBITDA_Cushion_Pct', 'N/A')}%" if wp.summary_metrics.get('EBITDA_Cushion_Pct') is not None else "N/A"
            })

        st.table(results_rows)

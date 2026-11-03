"""
Tests for Live and Simulated OpenRouter LLM Integration:
1. Rule Set Synthesis parsing and error handling.
2. Add-Back Classification parsing and error handling.
3. Live network execution if OPENROUTER_API_KEY is present in environment.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

from src.core.ruleset_generator import (
    RuleSetGenerator,
    RuleSet,
    RuleSetCap,
    CapType
)
from src.core.amendment_tracker import AgreementVersion, OPENROUTER_CONFIG
from src.core.dependency_graph import DependencyGraph, DependencyNode
from src.compliance.financial_data import ClaimedAddBack
from src.compliance.addback_auditor import AddBackAuditor, AddBackEligibilityStatus


@pytest.fixture
def sample_graph_and_version():
    v1 = AgreementVersion(
        version_id="OPENROUTER_TEST_v1",
        company="Live Test Corp",
        effective_date="2022-01-01",
        source_doc_ids=["doc_1"],
        source_hashes={"doc_1": "hash_123"},
        sections={"Section 7.11": "Total Net Leverage Ratio shall not exceed 4.50:1.00."},
        definitions={
            "Consolidated EBITDA": (
                "Consolidated EBITDA means Consolidated Net Income plus Interest Expense, Taxes, "
                "and Restructuring Charges up to $10,000,000."
            ),
            "Consolidated Net Debt": "Consolidated Net Debt means total debt less unrestricted cash."
        }
    )
    g = DependencyGraph(
        version_id=v1.version_id,
        company=v1.company,
        nodes={
            "Consolidated EBITDA": DependencyNode(
                node_id="Consolidated EBITDA",
                node_type="definition",
                clause_text=v1.definitions["Consolidated EBITDA"],
                citation="[Section 1.01]"
            ),
            "Consolidated Net Debt": DependencyNode(
                node_id="Consolidated Net Debt",
                node_type="definition",
                clause_text=v1.definitions["Consolidated Net Debt"],
                citation="[Section 1.01]"
            )
        }
    )
    return g, v1


def test_openrouter_ruleset_synthesis_parsing(sample_graph_and_version):
    """
    Verifies that RuleSetGenerator._call_openrouter_synthesis correctly parses
    OpenRouter API response payload, handles code fences, and builds valid RuleSet.
    """
    g, v1 = sample_graph_and_version

    mock_llm_content = {
        "ruleset_id": "ruleset_live_test",
        "version_id": "OPENROUTER_TEST_v1",
        "company": "Live Test Corp",
        "source_hashes": {"doc_1": "hash_123"},
        "created_at": "2023-01-01T00:00:00Z",
        "starting_metric": "Consolidated Net Income",
        "add_backs": [
            {
                "rule_id": "rb_restruct",
                "category": "Restructuring Charges",
                "description": "Restructuring charges up to $10M",
                "direction": "ADDITION",
                "is_subject_to_cap": True,
                "cap": {
                    "cap_id": "cap_restruct",
                    "cap_type": "FIXED_DOLLAR",
                    "dollar_limit": 10000000.0,
                    "exact_clause": "Restructuring Charges up to $10,000,000",
                    "citation": "Section 1.01"
                },
                "conditions": ["Non-recurring only"],
                "verbatim_clause": "Restructuring Charges up to $10,000,000",
                "citation": "Section 1.01"
            }
        ],
        "deductions": [],
        "net_debt_rule": {
            "debt_components": ["Total Debt"],
            "cash_netting_type": "UNCAPPED_CASH_NETTING",
            "verbatim_clause": "total debt less unrestricted cash",
            "citation": "Section 1.01"
        },
        "covenant_rule": {
            "covenant_name": "Consolidated Total Net Leverage Ratio",
            "governing_section": "Section 7.11",
            "metric_formula": "Net Debt / EBITDA",
            "schedule": [{"tier_id": "t1", "period_description": "Initial Governing Period", "ratio_ceiling": 4.50}],
            "verbatim_clause": "Total Net Leverage Ratio shall not exceed 4.50:1.00",
            "citation": "Section 7.11"
        }
    }

    mock_api_resp = {
        "id": "gen-12345",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"```json\n{json.dumps(mock_llm_content)}\n```"
                }
            }
        ]
    }

    generator = RuleSetGenerator(api_key="mock_test_key_non_empty")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_api_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp_obj

        rs = generator._call_openrouter_synthesis(g, v1)

        assert rs.ruleset_id == "ruleset_live_test"
        assert rs.company == "Live Test Corp"
        assert len(rs.add_backs) == 1
        assert rs.add_backs[0].category == "Restructuring Charges"
        assert rs.add_backs[0].cap.dollar_limit == 10000000.0


def test_openrouter_ruleset_synthesis_reasoning_fallback(sample_graph_and_version):
    """
    Verifies that when content is null/empty, but the reasoning channel contains the JSON,
    RuleSetGenerator successfully extracts and parses the RuleSet.
    """
    g, v1 = sample_graph_and_version
    mock_llm_content = {
        "ruleset_id": "ruleset_reasoning_test",
        "version_id": "OPENROUTER_TEST_v1",
        "company": "Live Test Corp",
        "starting_metric": "Consolidated Net Income",
        "add_backs": [
            {
                "rule_id": "rb_taxes",
                "category": "Taxes",
                "description": "Income taxes",
                "direction": "ADDITION",
                "is_subject_to_cap": False,
                "verbatim_clause": "Taxes",
                "citation": "Section 1.01"
            }
        ]
    }
    mock_api_resp = {
        "id": "gen-reasoning-1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning": f"Let me think... The output is:\n```json\n{json.dumps(mock_llm_content)}\n```"
                }
            }
        ]
    }

    generator = RuleSetGenerator(api_key="mock_test_key_non_empty")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_api_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp_obj

        rs = generator._call_openrouter_synthesis(g, v1)
        assert rs.ruleset_id == "ruleset_reasoning_test"
        assert len(rs.add_backs) == 1
        assert rs.add_backs[0].category == "Taxes"


def test_openrouter_ruleset_synthesis_think_block(sample_graph_and_version):
    """
    Verifies that when content contains <think>...</think> before the JSON,
    RuleSetGenerator properly separates reasoning and parses the JSON.
    """
    g, v1 = sample_graph_and_version
    mock_llm_content = {
        "ruleset_id": "ruleset_think_test",
        "version_id": "OPENROUTER_TEST_v1",
        "company": "Live Test Corp",
        "starting_metric": "Consolidated Net Income",
        "add_backs": []
    }
    mock_api_resp = {
        "id": "gen-think-1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": f"<think>Thinking deeply about covenants...</think>\n\n```json\n{json.dumps(mock_llm_content)}\n```"
                }
            }
        ]
    }

    generator = RuleSetGenerator(api_key="mock_test_key_non_empty")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_api_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp_obj

        rs = generator._call_openrouter_synthesis(g, v1)
        assert rs.ruleset_id == "ruleset_think_test"


def test_openrouter_ruleset_synthesis_empty_content_raises_diagnostic_error(sample_graph_and_version):
    """
    Verifies that when model genuinely returns no JSON and hits length limit,
    a diagnostic ValueError is raised containing finish_reason and field presence.
    """
    g, v1 = sample_graph_and_version
    mock_api_resp = {
        "id": "gen-empty-1",
        "model": "google/gemma-4-31b-it:free",
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning": ""
                }
            }
        ]
    }

    generator = RuleSetGenerator(api_key="mock_test_key_non_empty")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_api_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp_obj

        with pytest.raises(ValueError) as excinfo:
            generator._call_openrouter_synthesis(g, v1)

        err_str = str(excinfo.value)
        assert "finish_reason='length'" in err_str


def test_openrouter_ruleset_synthesis_accepts_model_kwarg(sample_graph_and_version):
    """
    Verifies that _call_openrouter_synthesis accepts model= as a keyword argument
    and uses the specified model in the OpenRouter payload.
    """
    g, v1 = sample_graph_and_version
    mock_llm_content = {
        "ruleset_id": "ruleset_model_kwarg_test",
        "version_id": "OPENROUTER_TEST_v1",
        "company": "Live Test Corp",
        "starting_metric": "Consolidated Net Income",
        "add_backs": []
    }
    mock_api_resp = {
        "id": "gen-model-kwarg",
        "model": "google/gemma-4-31b-it:free",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"```json\n{json.dumps(mock_llm_content)}\n```"
                }
            }
        ]
    }

    generator = RuleSetGenerator(api_key="mock_test_key_non_empty")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_api_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp_obj

        # Verify calling with keyword argument model=...
        rs = generator._call_openrouter_synthesis(g, v1, model="google/gemma-4-31b-it:free")
        assert rs.ruleset_id == "ruleset_model_kwarg_test"

        # Verify that urlopen was called with request payload containing the chosen model
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        req_body = json.loads(req.data.decode("utf-8"))
        assert req_body["model"] == "google/gemma-4-31b-it:free"


def test_openrouter_addback_classification_parsing(sample_graph_and_version):
    """
    Verifies that AddBackAuditor._call_openrouter_audit correctly parses
    OpenRouter API response payload into structured AuditedAddBack objects.
    """
    _, v1 = sample_graph_and_version
    rs = RuleSet(
        ruleset_id="rs_test_audit",
        version_id=v1.version_id,
        company=v1.company,
        source_hashes=v1.source_hashes,
        created_at="2023-01-01T00:00:00Z",
        starting_metric="Consolidated Net Income",
        add_backs=[
            {
                "rule_id": "rb_restruct",
                "category": "Restructuring Charges",
                "description": "Restructuring costs",
                "direction": "ADDITION",
                "is_subject_to_cap": False,
                "conditions": [],
                "verbatim_clause": "Restructuring Charges",
                "citation": "Section 1.01"
            }
        ],
        net_debt_rule={"debt_components": ["Debt"], "cash_netting_type": "UNCAPPED_CASH_NETTING", "verbatim_clause": "debt", "citation": "cite"},
        covenant_rule={"covenant_name": "Lev", "governing_section": "Sec 7", "schedule": [{"tier_id": "t1", "period_description": "Initial", "ratio_ceiling": 4.5}], "verbatim_clause": "cov", "citation": "cite"}
    )

    claimed = [
        ClaimedAddBack(
            addback_id="claim_1",
            category="Restructuring Charges",
            claimed_amount=1500000.0,
            description="Lease termination penalties",
            supporting_evidence="Signed landlord settlement agreement"
        )
    ]

    mock_llm_output = [
        {
            "addback_id": "claim_1",
            "category": "Restructuring Charges",
            "claimed_amount": 1500000.0,
            "eligible_amount": 1500000.0,
            "status": "ELIGIBLE",
            "audit_rationale": "Permitted lease exit cost under Restructuring Charges clause."
        }
    ]

    mock_api_resp = {
        "id": "gen-67890",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"```json\n{json.dumps(mock_llm_output)}\n```"
                }
            }
        ]
    }

    auditor = AddBackAuditor(api_key="mock_test_key_non_empty")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = json.dumps(mock_api_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp_obj

        results = auditor._call_openrouter_audit(claimed, rs)

        assert len(results) == 1
        assert results[0].addback_id == "claim_1"
        assert results[0].status == AddBackEligibilityStatus.ELIGIBLE
        assert results[0].eligible_amount == 1500000.0
        assert "lease exit cost" in results[0].audit_rationale


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="Requires live OPENROUTER_API_KEY")
def test_live_openrouter_actual_api_calls(sample_graph_and_version):
    """
    Executed ONLY if a live OPENROUTER_API_KEY is exported in the shell environment.
    Runs actual live API requests against openrouter/free.
    """
    live_key = os.environ.get("OPENROUTER_API_KEY")
    g, v1 = sample_graph_and_version

    # 1. Live RuleSet Generation
    generator = RuleSetGenerator(api_key=live_key)
    live_rs = generator._call_openrouter_synthesis(g, v1)
    assert live_rs is not None
    assert live_rs.company == v1.company

    # 2. Live Add-Back Audit
    auditor = AddBackAuditor(api_key=live_key)
    claimed = [
        ClaimedAddBack(
            addback_id="claim_live",
            category="Restructuring Charges",
            claimed_amount=500000.0,
            description="Office consolidation moving expense",
            supporting_evidence="Approved facility program invoice"
        )
    ]
    live_audited = auditor._call_openrouter_audit(claimed, live_rs)
    assert len(live_audited) == 1
    assert live_audited[0].status in [
        AddBackEligibilityStatus.ELIGIBLE,
        AddBackEligibilityStatus.NEEDS_HUMAN_REVIEW
    ]

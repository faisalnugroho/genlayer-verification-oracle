"""Direct-mode tests for GVO contract — validates storage/structure and all
deterministic write/view methods without needing a live LLM or network.

The LLM judgment (resolve_claim/resolve_appeal) requires real consensus (Studio /
Studionet / Bradbury); those paths are exercised separately. Here we mock web+LLM
where the direct-mode SDK allows, and otherwise verify the deterministic flow:

    submit_claim -> pending status -> appeal window gating -> views -> stats
"""
import json

import pytest
from eth_utils import to_checksum_address


def addr(raw_bytes):
    return to_checksum_address(raw_bytes)


def test_submit_claim_and_views(direct_deploy):
    gvo = direct_deploy("contracts/gvo.py")
    assert int(gvo.get_claim_count()) == 0

    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent delivered the milestone as specified.",
        "The deliverable must contain a working payment module.",
        "https://example.com/evidence",
    )
    assert int(cid) == 1
    assert int(gvo.get_claim_count()) == 1

    claim = json.loads(gvo.get_claim(cid))
    assert claim["status"] == "pending"
    assert claim["category"] == "x402-dispute"
    assert claim["verdict"] == ""

    verdict = json.loads(gvo.get_verdict(cid))
    assert verdict["status"] == "pending"


def test_multiple_claims_and_counter(direct_deploy):
    gvo = direct_deploy("contracts/gvo.py")
    for i in range(3):
        gvo.submit_claim("cat", f"desc {i}", "crit", "https://e.com/x")
    assert int(gvo.get_claim_count()) == 3
    all_claims = json.loads(gvo.get_all_claims())
    assert len(all_claims) == 3


def test_resolve_requires_pending(direct_deploy, direct_vm):
    gvo = direct_deploy("contracts/gvo.py")
    cid = gvo.submit_claim("cat", "desc", "crit", "https://e.com/x")

    # Mock web + LLM so resolve_claim can run its nondet block in direct mode
    direct_vm.mock_web("https://e.com/x", "evidence says yes")
    direct_vm.mock_llm(
        ".*verification oracle.*",
        json.dumps({"verdict": True, "reasoning": "evidence satisfies criteria"}),
    )

    verdict = gvo.resolve_claim(cid)
    assert verdict is True

    claim = json.loads(gvo.get_claim(cid))
    assert claim["status"] == "resolved"
    assert claim["verdict"] == "true"
    assert claim["resolved_count"] == "1"


def test_appeal_gating(direct_deploy, direct_vm):
    gvo = direct_deploy("contracts/gvo.py")
    cid = gvo.submit_claim("cat", "desc", "crit", "https://e.com/x")

    # Before resolution, appeal must fail (status still pending -> assert fails)
    direct_vm.value = 10**18
    with pytest.raises(Exception):
        gvo.appeal_claim(cid)


def test_stats_empty(direct_deploy):
    gvo = direct_deploy("contracts/gvo.py")
    s = json.loads(gvo.get_stats())
    assert s["total_claims"] == "0"
    assert s["min_appeal_stake"] == "0"     # no minimum stake (decision #2)
    assert s["appeal_window"] == "100"
    assert s["forfeited_stake"] == "0"


def test_appeal_held_splits_stake_50_50(direct_deploy, direct_vm, direct_owner):
    """Full flow: submit -> resolve(true) -> appeal -> resolve_appeal(holds).
    Forfeited stake must split 50/50 between resolver and treasury."""
    gvo = direct_deploy("contracts/gvo.py")
    cid = gvo.submit_claim("cat", "desc", "crit", "https://e.com/x")

    direct_vm.mock_web("https://e.com/x", "evidence content")
    direct_vm.mock_llm(
        ".*verification oracle.*",
        json.dumps({"verdict": True, "reasoning": "holds"}),
    )
    gvo.resolve_claim(cid)

    owner = addr(direct_owner)          # deployer = resolver = treasury owner
    direct_vm.value = 1000
    assert gvo.appeal_claim(cid) is True

    # verdict does NOT flip -> stake forfeited, split 50/50
    gvo.resolve_appeal(cid)

    claim = json.loads(gvo.get_claim(cid))
    assert claim["status"] == "final"
    assert claim["stake_refundable"] == "0"
    assert claim["verdict"] == "true"

    assert gvo.get_resolver_rewards(owner) == "500"
    s = json.loads(gvo.get_stats())
    assert s["forfeited_stake"] == "500"

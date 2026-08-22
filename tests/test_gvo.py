"""Direct-mode tests for GVO contract — validates storage/structure and all
deterministic write/view methods without needing a live LLM or network.

The LLM judgment (resolve_claim/resolve_appeal) requires real consensus (Studio /
Studionet / Bradbury); those paths are exercised separately. Here we mock web+LLM
where the direct-mode SDK allows, and otherwise verify the deterministic flow:

    submit_claim -> pending status -> appeal window gating -> views -> stats

Payment-verification tests mock the Base JSON-RPC endpoint (POST) and the LLM to
exercise the on-chain fact gate end-to-end in direct mode.
"""
import json

import pytest
from eth_utils import to_checksum_address


def addr(raw_bytes):
    return to_checksum_address(raw_bytes)


# ── x402 / USDC test constants ──────────────────────────────────────
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _pad(addr_hex):
    """Pad a hex address to a 32-byte EVM word (for log topics)."""
    h = addr_hex[2:] if addr_hex.startswith("0x") else addr_hex
    return "0x" + h.lower().rjust(64, "0")


def _make_transfer_log(from_addr, to_addr, amount_hex, token_addr=USDC):
    return {
        "address": token_addr,
        "topics": [TRANSFER_TOPIC, _pad(from_addr), _pad(to_addr)],
        "data": amount_hex,
        "blockNumber": "0x10",
        "transactionHash": "0xabc",
        "transactionIndex": "0x0",
        "logIndex": "0x0",
    }


def _receipt(from_addr, to_addr, amount_hex, block_hex="0xf", token_addr=USDC):
    return {
        "transactionHash": "0xabc",
        "transactionIndex": "0x0",
        "blockNumber": block_hex,
        "blockHash": "0xdef",
        "from": from_addr,
        "to": to_addr,
        "status": "0x1",
        "logs": [_make_transfer_log(from_addr, to_addr, amount_hex, token_addr=token_addr)],
    }


def _mock_base_rpc(direct_vm, receipt_result, block_number_hex="0x2fcc1d3"):
    """Mock the two JSON-RPC POST calls the contract makes to Base."""
    direct_vm.mock_web(
        r"mainnet\.base\.org\?m=eth_getTransactionReceipt",
        {
            "method": "POST",
            "status": 200,
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "result": receipt_result}),
        },
    )
    direct_vm.mock_web(
        r"mainnet\.base\.org\?m=eth_blockNumber",
        {
            "method": "POST",
            "status": 200,
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "result": block_number_hex}),
        },
    )


def _mock_payment_llm(direct_vm, verdict=True, reasoning="payment verified"):
    direct_vm.mock_llm(
        r".*payment claims.*",
        json.dumps({"verdict": verdict, "reasoning": reasoning}),
    )


# ── Original tests (evidence-only path) ─────────────────────────────


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


# ── Payment-verification tests (x402 / USDC on-chain gate) ─────────


def test_payment_claim_fields_stored(direct_deploy):
    """submit_claim with payment fields stores them in the record."""
    gvo = direct_deploy("contracts/gvo.py")
    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid 0.002 USDC for API access.",
        "A USDC transfer of 2000 base units from payer to recipient must exist on Base.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer="0xAlice",
        recipient="0xBob",
        amount="2000",
    )
    claim = json.loads(gvo.get_claim(cid))
    assert claim["tx_hash"] == "0xabc123"
    assert claim["payer"] == "0xAlice"
    assert claim["recipient"] == "0xBob"
    assert claim["amount"] == "2000"
    assert claim["status"] == "pending"


def test_usdc_verification_approves_matching_claim(direct_deploy, direct_vm, direct_alice, direct_bob):
    """USDC verification approves a claim whose stated facts match on-chain reality."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    recipient = addr(direct_bob)
    amount = 2000  # $0.002 USDC (6 decimals)

    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid for API access via x402.",
        "A USDC transfer of 2000 base units must exist on Base.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=payer,
        recipient=recipient,
        amount=str(amount),
    )

    # Mock Base RPC: receipt shows a USDC transfer matching the claim exactly.
    _mock_base_rpc(direct_vm, _receipt(payer, recipient, hex(amount)))
    # Mock evidence URL fetch.
    direct_vm.mock_web("https://example.com/evidence", "payment confirmed by server logs")
    # Mock LLM: gate passes, LLM judges the full claim true.
    _mock_payment_llm(direct_vm, verdict=True, reasoning="payment verified on-chain and evidence confirms service delivery")

    verdict = gvo.resolve_claim(cid)
    assert verdict is True

    claim = json.loads(gvo.get_claim(cid))
    assert claim["status"] == "resolved"
    assert claim["verdict"] == "true"


def test_usdc_verification_rejects_amount_mismatch(direct_deploy, direct_vm, direct_alice, direct_bob):
    """USDC verification rejects a claim whose stated amount doesn't match on-chain."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    recipient = addr(direct_bob)

    # Claim says 5000, but on-chain shows 2000.
    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid 5000 USDC base units.",
        "A USDC transfer of 5000 base units must exist.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=payer,
        recipient=recipient,
        amount="5000",
    )

    # On-chain reality: only 2000 transferred.
    _mock_base_rpc(direct_vm, _receipt(payer, recipient, hex(2000)))
    # No LLM mock needed — the gate should fail before reaching the LLM.

    verdict = gvo.resolve_claim(cid)
    assert verdict is False

    claim = json.loads(gvo.get_claim(cid))
    assert claim["status"] == "resolved"
    assert claim["verdict"] == "false"
    assert "no USDC transfer matches" in claim["reasoning"]


def test_usdc_verification_rejects_sender_mismatch(direct_deploy, direct_vm, direct_alice, direct_bob, direct_charlie):
    """USDC verification rejects a claim whose stated payer doesn't match on-chain."""
    gvo = direct_deploy("contracts/gvo.py")
    real_payer = addr(direct_alice)
    claimed_payer = addr(direct_charlie)  # wrong payer claimed
    recipient = addr(direct_bob)
    amount = 2000

    cid = gvo.submit_claim(
        "x402-dispute",
        "Charlie paid for API access.",
        "A USDC transfer from Charlie must exist.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=claimed_payer,
        recipient=recipient,
        amount=str(amount),
    )

    # On-chain: Alice paid, not Charlie.
    _mock_base_rpc(direct_vm, _receipt(real_payer, recipient, hex(amount)))

    verdict = gvo.resolve_claim(cid)
    assert verdict is False

    claim = json.loads(gvo.get_claim(cid))
    assert claim["verdict"] == "false"
    assert "no USDC transfer matches" in claim["reasoning"]


def test_usdc_verification_rejects_recipient_mismatch(direct_deploy, direct_vm, direct_alice, direct_bob, direct_charlie):
    """USDC verification rejects a claim whose stated recipient doesn't match on-chain."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    real_recipient = addr(direct_bob)
    claimed_recipient = addr(direct_charlie)  # wrong recipient claimed
    amount = 2000

    cid = gvo.submit_claim(
        "x402-dispute",
        "Payment was sent to Charlie.",
        "A USDC transfer to Charlie must exist.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=payer,
        recipient=claimed_recipient,
        amount=str(amount),
    )

    # On-chain: Bob received, not Charlie.
    _mock_base_rpc(direct_vm, _receipt(payer, real_recipient, hex(amount)))

    verdict = gvo.resolve_claim(cid)
    assert verdict is False

    claim = json.loads(gvo.get_claim(cid))
    assert claim["verdict"] == "false"
    assert "no USDC transfer matches" in claim["reasoning"]


def test_usdc_verification_rejects_tx_not_found(direct_deploy, direct_vm, direct_alice, direct_bob):
    """USDC verification rejects a claim whose tx_hash doesn't exist on Base."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    recipient = addr(direct_bob)

    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid for API access.",
        "A USDC transfer must exist.",
        "https://example.com/evidence",
        tx_hash="0xnonexistent",
        payer=payer,
        recipient=recipient,
        amount="2000",
    )

    # Mock Base RPC: tx not found (result is None).
    _mock_base_rpc(direct_vm, None)

    verdict = gvo.resolve_claim(cid)
    assert verdict is False

    claim = json.loads(gvo.get_claim(cid))
    assert claim["verdict"] == "false"
    assert "not found" in claim["reasoning"]


def test_usdc_verification_rejects_non_usdc_transfer(direct_deploy, direct_vm, direct_alice, direct_bob):
    """USDC verification rejects a claim where the tx exists but is not a USDC transfer."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    recipient = addr(direct_bob)
    amount = 2000

    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid in USDC.",
        "A USDC transfer must exist.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=payer,
        recipient=recipient,
        amount=str(amount),
    )

    # On-chain: transfer exists but from a different token (not USDC).
    other_token = "0x4200000000000000000000000000000000000006"  # WETH on Base
    _mock_base_rpc(direct_vm, _receipt(payer, recipient, hex(amount), token_addr=other_token))

    verdict = gvo.resolve_claim(cid)
    assert verdict is False

    claim = json.loads(gvo.get_claim(cid))
    assert claim["verdict"] == "false"
    assert "no USDC Transfer" in claim["reasoning"]


def test_usdc_verification_rejects_insufficient_confirmations(direct_deploy, direct_vm, direct_alice, direct_bob):
    """USDC verification rejects when confirmations < MIN_CONFIRMATIONS (5)."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    recipient = addr(direct_bob)
    amount = 2000

    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid for API access.",
        "A USDC transfer must exist.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=payer,
        recipient=recipient,
        amount=str(amount),
    )

    # Receipt at block 0x2fcc1d1, latest at 0x2fcc1d3 -> only 3 confirmations.
    _mock_base_rpc(
        direct_vm,
        _receipt(payer, recipient, hex(amount), block_hex="0x2fcc1d1"),
        block_number_hex="0x2fcc1d3",
    )

    verdict = gvo.resolve_claim(cid)
    assert verdict is False

    claim = json.loads(gvo.get_claim(cid))
    assert claim["verdict"] == "false"
    assert "confirmations" in claim["reasoning"]


def test_usdc_verification_matches_among_multiple_transfers(direct_deploy, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Real-world txs (DeFi routes) contain multiple USDC Transfer events.
    The gate must pass if ANY transfer matches the claimed facts."""
    gvo = direct_deploy("contracts/gvo.py")
    payer = addr(direct_alice)
    recipient = addr(direct_bob)
    other = addr(direct_charlie)
    amount = 2000

    cid = gvo.submit_claim(
        "x402-dispute",
        "Agent paid for API access.",
        "A USDC transfer of 2000 base units must exist.",
        "https://example.com/evidence",
        tx_hash="0xabc123",
        payer=payer,
        recipient=recipient,
        amount=str(amount),
    )

    # Receipt with 3 USDC transfers; only the middle one matches the claim.
    receipt = _receipt(payer, recipient, hex(amount))
    receipt["logs"] = [
        _make_transfer_log(other, payer, hex(999)),       # unrelated
        _make_transfer_log(payer, recipient, hex(amount)),  # the claimed one
        _make_transfer_log(recipient, other, hex(amount)),  # same amount, wrong parties
    ]
    _mock_base_rpc(direct_vm, receipt)
    direct_vm.mock_web("https://example.com/evidence", "payment confirmed")
    _mock_payment_llm(direct_vm, verdict=True)

    verdict = gvo.resolve_claim(cid)
    assert verdict is True

    claim = json.loads(gvo.get_claim(cid))
    assert claim["verdict"] == "true"

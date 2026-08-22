#!/usr/bin/env python3
"""
Deploy GVO to Studionet and exercise the write functions end-to-end,
including the new x402/USDC on-chain payment verification path.

Requires genlayer-py (0.16.x). Run:
    ~/genlayer-env/bin/python deploy_gvo.py

Live USDC verification uses a real Base transaction (a USDC transfer found on
Base mainnet) so the contract's on-chain gate is exercised against actual
chain data, not mocks.
"""
import json
import sys
from pathlib import Path

from genlayer_py import create_account, create_client, studionet
from genlayer_py.types import TransactionStatus, ExecutionResult

CODE_PATH = Path(__file__).parent / "contracts" / "gvo.py"

# Real Base USDC transfer (found via eth_getLogs on Base mainnet).
# Used to exercise the on-chain payment verification gate live.
REAL_BASE_TX = "0xa04ee1a7b7f0573703dffd46445a43e9552bbc9713848056786956c50b8ef29e"
REAL_PAYER = "0x498581ff718922c3f8e6a244956af099b2652b2b"
REAL_RECIPIENT = "0x7747f8d2a76bd6345cc29622a946a929647f2359"
REAL_AMOUNT = "52689366"  # exact on-chain value in USDC base units


def log(msg):
    print(msg, flush=True)


def get_exec_result(receipt) -> str:
    name = receipt.get("tx_execution_result_name") or receipt.get("execution_result_name")
    return name or "?"


def ok(receipt) -> bool:
    """Success check per established Studionet quirk: the reliable indicator is
    leader_receipt[0].execution_result == 'SUCCESS' (tx_execution_result_name
    is often absent/'?' on this SDK version)."""
    try:
        lr = receipt.get("consensus_data", {}).get("leader_receipt")
        if lr and isinstance(lr, list):
            return lr[0].get("execution_result") == "SUCCESS"
    except Exception:
        pass
    return receipt.get("tx_execution_result_name") in (
        ExecutionResult.FINISHED_WITH_RETURN.value, "FINISHED_WITH_RETURN",
    )


def main():
    code = CODE_PATH.read_text()
    log(f"Contract source: {len(code)} bytes")

    account = create_account()
    log(f"Account: {account.address}")

    client = create_client(chain=studionet, account=account)
    client.local_account = account
    log("Funding account on Studionet...")
    try:
        client.fund_account(account.address, 10**18)
        log("fund_account OK")
    except Exception as e:
        log(f"fund_account note: {e}")

    log("Deploying (full consensus)...")
    tx_hash = client.deploy_contract(code=code, account=account, args=[0, 100], leader_only=False)
    log(f"Deploy tx hash: {tx_hash}")
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.ACCEPTED,
        interval=3000, retries=60, full_transaction=True,
    )
    log(f"Deploy receipt: {receipt.get('status_name')} / exec: {get_exec_result(receipt)}")

    data = receipt.get("data", {}) if isinstance(receipt.get("data"), dict) else {}
    addr = data.get("contract_address")
    if not addr:
        for k in ("contractAddress", "deployedAddress"):
            if k in receipt:
                addr = receipt[k]
    log(f"Deployed at: {addr}")

    if not ok(receipt):
        log("EXECUTION FAILED — dumping debug trace")
        try:
            log(json.dumps(client.debug_trace_transaction(tx_hash), indent=2)[:4000])
        except Exception as e:
            log(f"trace failed: {e}")
        sys.exit(1)

    log("\n--- get_stats ---")
    stats = client.read_contract(address=addr, function_name="get_stats", raw_return=False)
    log(json.dumps(stats, indent=2))

    # ── 1. Evidence-only claim (original path) ──
    log("\n--- submit_claim (evidence-only) ---")
    tx = client.write_contract(
        address=addr, function_name="submit_claim", account=account,
        args=["x402-dispute", "Agent delivered the escrowed milestone.",
              "Deliverable must include a working payment module and pass its own tests.",
              "https://example.com/agent-milestone-evidence",
              "", "", "", ""],
    )
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=3000, retries=60, full_transaction=True)
    log(f"submit_claim receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if not ok(r):
        log("submit FAILED — trace:"); log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)

    # ── 2. USDC payment claim with MATCHING on-chain facts (should resolve true) ──
    log("\n--- submit_claim (USDC, matching facts) ---")
    tx = client.write_contract(
        address=addr, function_name="submit_claim", account=account,
        args=["x402-dispute",
              "Agent paid 52.689366 USDC on Base for API access (x402).",
              "A USDC transfer of 52689366 base units from the stated payer to the stated recipient must exist on Base.",
              "https://basescan.org/tx/" + REAL_BASE_TX,
              REAL_BASE_TX, REAL_PAYER, REAL_RECIPIENT, REAL_AMOUNT],
    )
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=3000, retries=60, full_transaction=True)
    log(f"submit_claim(USDC match) receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if not ok(r):
        log("submit FAILED — trace:"); log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)

    # ── 3. USDC payment claim with WRONG amount (should resolve false) ──
    log("\n--- submit_claim (USDC, wrong amount) ---")
    tx = client.write_contract(
        address=addr, function_name="submit_claim", account=account,
        args=["x402-dispute",
              "Agent paid 999999999 USDC on Base (fabricated amount).",
              "A USDC transfer of 999999999 base units must exist on Base.",
              "https://basescan.org/tx/" + REAL_BASE_TX,
              REAL_BASE_TX, REAL_PAYER, REAL_RECIPIENT, "999999999"],
    )
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=3000, retries=60, full_transaction=True)
    log(f"submit_claim(USDC wrong) receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if not ok(r):
        log("submit FAILED — trace:"); log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)

    # ── Resolve the matching USDC claim (runs the on-chain gate live) ──
    log("\n--- resolve_claim (USDC match — on-chain gate) ---")
    tx = client.write_contract(address=addr, function_name="resolve_claim", account=account, args=[2])
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=5000, retries=80, full_transaction=True)
    log(f"resolve_claim(match) receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if not ok(r):
        log("resolve FAILED — trace:"); log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)
    resolved = client.read_contract(address=addr, function_name="get_claim", args=[2])
    log("claim 2 (match): " + json.dumps(resolved, indent=2))

    # ── Resolve the wrong-amount USDC claim (gate must reject) ──
    log("\n--- resolve_claim (USDC wrong amount — gate must reject) ---")
    tx = client.write_contract(address=addr, function_name="resolve_claim", account=account, args=[3])
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=5000, retries=80, full_transaction=True)
    log(f"resolve_claim(wrong) receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if not ok(r):
        log("resolve FAILED — trace:"); log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)
    resolved = client.read_contract(address=addr, function_name="get_claim", args=[3])
    log("claim 3 (wrong amount): " + json.dumps(resolved, indent=2))

    log("\n--- final get_stats ---")
    stats = client.read_contract(address=addr, function_name="get_stats", raw_return=False)
    log(json.dumps(stats, indent=2))

    log("\nALL DONE")
    log(f"CONTRACT_ADDRESS={addr}")


if __name__ == "__main__":
    main()

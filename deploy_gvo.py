#!/usr/bin/env python3
"""
Deploy GVO to Studionet and exercise the write functions end-to-end.

Requires genlayer-py (0.16.x). Run:
    ~/genlayer-env/bin/python deploy_gvo.py
"""
import json
import sys
import time
from pathlib import Path

from genlayer_py import create_account, create_client, studionet
from genlayer_py.types import TransactionStatus, ExecutionResult

CODE_PATH = Path(__file__).parent / "contracts" / "gvo.py"


def log(msg):
    print(msg, flush=True)


def get_exec_result(receipt) -> str:
    """Return a short human string describing the execution result."""
    name = receipt.get("tx_execution_result_name") or receipt.get(
        "execution_result_name"
    )
    return name or "?"


def main():
    code = CODE_PATH.read_text()
    log(f"Contract source: {len(code)} bytes")

    account = create_account()
    log(f"Account: {account.address}")

    client = create_client(chain=studionet, account=account)
    log("Funding account on Studionet...")
    try:
        client.fund_account(account.address, 10**18)
        log("fund_account OK")
    except Exception as e:
        log(f"fund_account failed (may already have funds): {e}")

    log("Deploying (full consensus)...")
    tx_hash = client.deploy_contract(
        code=code, account=account, args=[0, 100], leader_only=False
    )
    log(f"Deploy tx hash: {tx_hash}")

    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=TransactionStatus.ACCEPTED,
        interval=3000,
        retries=60,
        full_transaction=True,
    )
    log(f"Receipt status: {receipt.get('status_name')} / exec: {get_exec_result(receipt)}")

    # The contract address lives in receipt['data']['contract_address'] on 0.16.x
    data = receipt.get("data", {}) if isinstance(receipt.get("data"), dict) else {}
    addr = data.get("contract_address")
    if not addr:
        # fallback: try keys
        for k in ("contractAddress", "deployedAddress"):
            if k in receipt:
                addr = receipt[k]
    log(f"Deployed at: {addr}")

    if receipt.get("tx_execution_result_name") not in (
        ExecutionResult.FINISHED_WITH_RETURN.value,
        "FINISHED_WITH_RETURN",
    ):
        log("EXECUTION FAILED — dumping debug trace")
        try:
            log(json.dumps(client.debug_trace_transaction(tx_hash), indent=2)[:4000])
        except Exception as e:
            log(f"trace failed: {e}")
        sys.exit(1)

    log("\n--- Reading get_stats ---")
    stats = client.read_contract(address=addr, function_name="get_stats", raw_return=False)
    log(json.dumps(stats, indent=2))

    log("\n--- submit_claim ---")
    tx = client.write_contract(
        address=addr,
        function_name="submit_claim",
        account=account,
        args=["x402-dispute", "Agent delivered the escrowed milestone.",
              "Deliverable must include a working payment module and pass its own tests.",
              "https://example.com/agent-milestone-evidence"],
    )
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=3000, retries=60, full_transaction=True)
    log(f"submit_claim receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if r.get("tx_execution_result_name") not in (ExecutionResult.FINISHED_WITH_RETURN.value, "FINISHED_WITH_RETURN"):
        log("submit FAILED — trace:")
        log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)

    cid = 1
    log(f"claim id (expected 1): {cid}")
    claim = client.read_contract(address=addr, function_name="get_claim", args=[cid])
    log(json.dumps(claim, indent=2))

    log("\n--- resolve_claim (LLM consensus) ---")
    tx = client.write_contract(address=addr, function_name="resolve_claim", account=account, args=[cid])
    r = client.wait_for_transaction_receipt(transaction_hash=tx, status=TransactionStatus.ACCEPTED, interval=5000, retries=80, full_transaction=True)
    log(f"resolve_claim receipt: {r.get('status_name')} / exec: {get_exec_result(r)}")
    if r.get("tx_execution_result_name") not in (ExecutionResult.FINISHED_WITH_RETURN.value, "FINISHED_WITH_RETURN"):
        log("resolve FAILED — trace:")
        log(json.dumps(client.debug_trace_transaction(tx), indent=2)[:4000]); sys.exit(1)
    resolved = client.read_contract(address=addr, function_name="get_claim", args=[cid])
    log(json.dumps(resolved, indent=2))

    verdict = client.read_contract(address=addr, function_name="get_verdict", args=[cid])
    log(f"get_verdict: {json.dumps(verdict)}")

    log("\nALL DONE")
    log(f"CONTRACT_ADDRESS={addr}")


if __name__ == "__main__":
    main()

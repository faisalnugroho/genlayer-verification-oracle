#!/usr/bin/env python3
"""Exercise the freshly deployed GVO contract end-to-end, including the new
x402/USDC on-chain payment verification path against real Base chain data."""
import json
import sys

from genlayer_py import create_account, create_client, studionet
from genlayer_py.types import TransactionStatus

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0x9865948Aa5170C50F4B73bf47706C8A09f7135d4"

REAL_BASE_TX = "0xa04ee1a7b7f0573703dffd46445a43e9552bbc9713848056786956c50b8ef29e"
REAL_PAYER = "0x498581ff718922c3f8e6a244956af099b2652b2b"
REAL_RECIPIENT = "0x7747f8d2a76bd6345cc29622a946a929647f2359"
REAL_AMOUNT = "52689366"


def log(msg):
    print(msg, flush=True)


def ok(receipt) -> bool:
    try:
        lr = receipt.get("consensus_data", {}).get("leader_receipt")
        if lr and isinstance(lr, list):
            return lr[0].get("execution_result") == "SUCCESS"
    except Exception:
        pass
    return False


def main():
    account = create_account()
    client = create_client(chain=studionet, account=account)
    client.local_account = account
    try:
        client.fund_account(account.address, 10**18)
    except Exception as e:
        log(f"fund note: {e}")
    log(f"Account: {account.address}  Contract: {ADDR}")

    def write(fn, args, label):
        tx = client.write_contract(address=ADDR, function_name=fn, account=account, args=args)
        r = client.wait_for_transaction_receipt(
            transaction_hash=tx, status=TransactionStatus.ACCEPTED,
            interval=3000, retries=80, full_transaction=True)
        log(f"{label}: {r.get('status_name')} / exec_ok={ok(r)}")
        if not ok(r):
            log("  FAILED — aborting"); sys.exit(1)
        return r

    # 1. evidence-only claim
    write("submit_claim",
          ["x402-dispute", "Agent delivered the escrowed milestone.",
           "Deliverable must include a working payment module and pass its own tests.",
           "https://example.com/agent-milestone-evidence", "", "", "", ""],
          "submit_claim(evidence-only)")

    # 2. USDC matching claim
    write("submit_claim",
          ["x402-dispute", "Agent paid 52.689366 USDC on Base for API access (x402).",
           "A USDC transfer of 52689366 base units from the stated payer to the stated recipient must exist on Base.",
           "https://basescan.org/tx/" + REAL_BASE_TX,
           REAL_BASE_TX, REAL_PAYER, REAL_RECIPIENT, REAL_AMOUNT],
          "submit_claim(USDC match)")

    # 3. USDC wrong-amount claim
    write("submit_claim",
          ["x402-dispute", "Agent paid 999999999 USDC on Base (fabricated amount).",
           "A USDC transfer of 999999999 base units must exist on Base.",
           "https://basescan.org/tx/" + REAL_BASE_TX,
           REAL_BASE_TX, REAL_PAYER, REAL_RECIPIENT, "999999999"],
          "submit_claim(USDC wrong amount)")

    # resolve claim 2 (USDC match — on-chain gate against real Base data)
    write("resolve_claim", [2], "resolve_claim(2 USDC match)")
    c2 = client.read_contract(address=ADDR, function_name="get_claim", args=[2])
    log("claim 2: " + json.dumps(c2, indent=2))

    # resolve claim 3 (USDC wrong amount — gate must reject)
    write("resolve_claim", [3], "resolve_claim(3 USDC wrong)")
    c3 = client.read_contract(address=ADDR, function_name="get_claim", args=[3])
    log("claim 3: " + json.dumps(c3, indent=2))

    stats = client.read_contract(address=ADDR, function_name="get_stats")
    log("final stats: " + json.dumps(stats, indent=2))
    log("\nALL DONE")


if __name__ == "__main__":
    main()

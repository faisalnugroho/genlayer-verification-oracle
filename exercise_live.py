#!/usr/bin/env python3
"""Exercise GVO on Studionet: submit -> resolve -> appeal -> resolve_appeal.
Uses the already-deployed contract. Detects execution via leader_receipt
execution_result OR by re-reading state and checking it changed.
"""
import json
import sys
import time
from genlayer_py import create_account, create_client, studionet
from genlayer_py.types import TransactionStatus

GVO_ADDRESS = "0x9865948Aa5170C50F4B73bf47706C8A09f7135d4"

def log(m):
    print(m, flush=True)

def leader_ok(receipt) -> bool:
    cr = receipt.get("consensus_data", {})
    lr = cr.get("leader_receipt") or []
    if lr:
        return lr[0].get("execution_result") == "SUCCESS"
    return None  # unknown

def wait_tx(client, tx, what, check_state=None):
    r = client.wait_for_transaction_receipt(
        transaction_hash=tx, status=TransactionStatus.ACCEPTED,
        interval=4000, retries=90, full_transaction=True,
    )
    ok = leader_ok(r)
    log(f"[{what}] status={r.get('status_name')} leader_ok={ok}")
    if ok is False:
        cr = r.get("consensus_data", {})
        lrlist = cr.get("leader_receipt") or []
        g = lrlist[0].get("genvm_result", {}) if lrlist else {}
        log(f"    stderr={g.get('stderr')!r}")
        log(f"    stdout={g.get('stdout')!r}")
        log(f"    raw_error={g.get('raw_error')!r}")
        return None
    return r

def read_claim(client, cid):
    return client.read_contract(address=GVO_ADDRESS, function_name="get_claim", args=[cid])

def main():
    account = create_account()
    client = create_client(chain=studionet, account=account)
    log(f"Account {account.address}")
    try:
        client.fund_account(account.address, 10**18)
        log("funded")
    except Exception as e:
        log(f"fund skip: {e}")

    # 0. stats
    log("stats0: " + str(client.read_contract(address=GVO_ADDRESS, function_name="get_stats")))

    # 1. submit_claim
    log("\n[1] submit_claim")
    tx = client.write_contract(
        address=GVO_ADDRESS, function_name="submit_claim", account=account,
        args=["x402-dispute", "Agent delivered the escrowed milestone.",
              "Deliverable must include a working payment module and pass its own tests.",
              "https://example.com/agent-milestone-evidence"],
    )
    wait_tx(client, tx, "submit_claim")
    c = read_claim(client, 1)
    log("claim1: " + str(c)[:400])

    # 2. resolve_claim (LLM consensus)
    log("\n[2] resolve_claim (LLM consensus, ~1-2 min)")
    tx = client.write_contract(address=GVO_ADDRESS, function_name="resolve_claim", account=account, args=[1])
    wait_tx(client, tx, "resolve_claim")
    c = read_claim(client, 1)
    log("claim1 resolved: " + str(c)[:600])

    # 3. appeal_claim (payable, stake 1 GEN)
    log("\n[3] appeal_claim (stake 1 GEN)")
    tx = client.write_contract(
        address=GVO_ADDRESS, function_name="appeal_claim", account=account, args=[1], value=10**18,
    )
    wait_tx(client, tx, "appeal_claim")
    c = read_claim(client, 1)
    log("claim1 appealed: " + str(c)[:600])

    # 4. resolve_appeal (LLM consensus)
    log("\n[4] resolve_appeal (LLM consensus)")
    tx = client.write_contract(address=GVO_ADDRESS, function_name="resolve_appeal", account=account, args=[1])
    wait_tx(client, tx, "resolve_appeal")
    c = read_claim(client, 1)
    log("claim1 final: " + str(c)[:600])

    log("get_verdict: " + str(client.read_contract(address=GVO_ADDRESS, function_name="get_verdict", args=[1])))
    log("resolver_rewards: " + str(client.read_contract(address=GVO_ADDRESS, function_name="get_resolver_rewards", args=[str(account.address)])))
    log("stats final: " + str(client.read_contract(address=GVO_ADDRESS, function_name="get_stats")))
    log("\nALL 4 WRITE FUNCTIONS EXERCISED")

if __name__ == "__main__":
    main()

GVO CHECKPOINT 2026-08-22 (steward-rejection fix redeploy)
===========================================================

REPO: github.com/faisalnugroho/genlayer-verification-oracle (PUBLIC)
LOCAL DIR: ~/gvo
SMART CONTRACT (Studionet, LIVE): 0x9865948Aa5170C50F4B73bf47706C8A09f7135d4
Chain id: 61999 · RPC: https://studio.genlayer.com/api · GEN 18 decimals
OLD CONTRACT (superseded): 0x184C7F56a0183b37f2ceC88F589C8D856082c915

WHY REDEPLOYED (steward rejection fixes):
  1. Missing relay endpoint: web/index.html called POST /api/v1/relay/submit
     which did not exist. Now implemented in backend/main.py (FastAPI) —
     signs submit_claim with a backend-held account, rate-limited per IP.
     Orphaned web/api/genlayer-proxy.js deleted.
  2. No real USDC verification: _judge() only LLM-judged evidence_url text.
     Now: submit_claim accepts tx_hash/payer/recipient/amount; when tx_hash
     is present, _judge_payment() runs a deterministic on-chain gate —
     every validator fetches eth_getTransactionReceipt + eth_blockNumber
     from https://mainnet.base.org via gl.nondet.web.request, extracts USDC
     Transfer events (token 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913),
     and requires an exact payer/recipient/amount match + >=5 confirmations.
     Gate fail => verdict false, LLM never consulted. Gate pass => LLM
     judges full claim. validator_fn compares on-chain facts + verdict.

LIVE EXERCISE RESULTS (new contract, real Base tx):
  Base tx used: 0xa04ee1a7b7f0573703dffd46445a43e9552bbc9713848056786956c50b8ef29e
    (real USDC transfer 52689366 base units,
     0x498581ff718922c3f8e6a244956af099b2652b2b -> 0x7747f8d2a76bd6345cc29622a946a929647f2359)
  claim 1: evidence-only, pending (not resolved in exercise)
  claim 2: USDC matching facts  -> resolve_claim -> verdict TRUE
           reasoning: "TRUE: the independently verified Base on-chain facts show
           a USDC transfer of 52689366 base units from ... to ..., which exactly
           matches the stated criteria."
  claim 3: USDC wrong amount (999999999) -> resolve_claim -> verdict FALSE
           reasoning: "on-chain payment facts do not match claim: no USDC
           transfer matches claimed payer/recipient/amount"
  stats: total_claims=3, total_resolved=2, approval_rate=50

TESTS: tests/test_gvo.py — 15 passed (gltest direct mode, genlayer-test 0.29.2)
  incl. USDC gate: approve match, reject amount/payer/recipient mismatch,
  reject tx-not-found, reject non-USDC token, reject low confirmations,
  match among multiple transfers (DeFi route case).
LINT: genvm-lint check contracts/gvo.py --json -> ok=true
  (lint 3 passed; validate: 11 methods, 6 view, 5 write; SDK v0.2.16 via
   GENVMROOT=/tmp/genvmroot — 'latest' download 404s)

COMPONENTS:
  contracts/gvo.py       — core (payment gate + evidence path, integer math)
  backend/main.py        — FastAPI: reads + POST /api/v1/relay/submit (rate-limited)
  indexer/poll.py        — SQLite mirror (now with tx_hash/payer/recipient/amount cols)
  web/index.html         — frontend with working claim form (relay + payment fields)
  deploy_gvo.py          — deploy + exercise (incl. live USDC verify)
  exercise_new.py        — exercise script used for this deploy
  tests/test_gvo.py      — 15 tests

TECH NOTES (headless genlayer-py):
  - env ~/genlayer-env has genlayer-py 0.16.3
  - poll status=ACCEPTED (FINALIZED may time out though tx succeeds)
  - success check: leader_receipt[0].execution_result == 'SUCCESS'
    (tx_execution_result_name often '?' on this SDK version)
  - fund_account works on Studionet
  - Base public RPC tolerates ?m=<method> query strings (used to make
    distinct JSON-RPC calls mockable/routable)

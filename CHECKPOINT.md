GVO CHECKPOINT 2026-08-20 (updated)
====================================

REPO: github.com/faisalnugroho/genlayer-verification-oracle (PUBLIC)
LOCAL DIR: ~/gvo
SMART CONTRACT (Studionet, LIVE): 0x19a4F04C987C35f4a231305429A2453e6Fe717F5
Chain id: 61999 · RPC: https://studio.genlayer.com/api · GEN 18 decimals

GIT (4 commits, pushed):
  f56af4b  initial release
  b17e546  add CHECKPOINT.md
  ffaf696  fix backend stats query + gvo_address endpoint; indexer oneshot mode
  02d950f  clean web/index.html: API-configurable read-side frontend

LIVE CHAIN STATE (read via genlayer-py, verifed):
  total_claims=1, total_resolved=1, total_appeals=1
  claim 1: category x402-dispute, status "final", verdict "false"
  forfeited_stake=500000000000000000 wei (treasury half)
  approval_rate = "0.0"

⚠ KNOWN GAP (honest):
  Live contract 0x19a4...717F5 STILL RUNS THE OLD CODE whose
  _approval_rate() used float arithmetic -> returns "0.0".
  Source in repo/gvo.py is FIXED to integer (approved*100//total).
  => source != deployed for this one field. The other 5 direct-mode tests
     pass against source. Re-deploy = new address (invalidates docs + resets
     live claim state). Decided: keep documented address; flag discrepancy.

COMPONENTS:
  contracts/gvo.py       — core (integer math)
  examples/escrow_consumer.py — cross-contract consumer
  tests/test_gvo.py      — 6 passed (gltest direct mode)
  deploy_gvo.py / exercise_live.py — deploy + exercise
  indexer/poll.py        — SQLite mirror (ONESHOT=1 env supported)
  backend/main.py         — FastAPI read-only (verified via curl)
  web/index.html         — static read-side (API base configurable)

RUN (verified working 2026-08-20):
  backend:  ~/genlayer-env/bin/uvicorn backend.main:app --port 8000
           DATABASE_PATH=~/gvo/gvo.db
  endpoints verified: /api/v1/health, /stats, /claims

NOT DEPLOYED as services: backend/indexer/frontend (run locally only).
Submission form (Builder Portal) — USER does this themselves.

TECH NOTES (headless genlayer-py):
  - env ~/genlayer-env has genlayer-py 0.16.3 (downgraded by genlayer-test install)
  - poll status=ACCEPTED (FINALIZED times out though tx succeeds)
  - success check: leader_receipt[0].execution_result == 'SUCCESS'
  - revert traceback at genvm_result.stderr
  - fund_account works on Studionet

GVO CHECKPOINT 2026-08-20 (redeployed)
====================================

REPO: github.com/faisalnugroho/genlayer-verification-oracle (PUBLIC)
LOCAL DIR: ~/gvo
SMART CONTRACT (Studionet, LIVE): 0x184C7F56a0183b37f2ceC88F589C8D856082c915
Chain id: 61999 · RPC: https://studio.genlayer.com/api · GEN 18 decimals

GIT (6 commits before the redeploy commit, all pushed):
  f56af4b  initial release
  b17e546  add CHECKPOINT.md
  ffaf696  fix backend stats query + gvo_address endpoint; indexer oneshot mode
  02d950f  clean web/index.html frontend

NEW CONTRACT LIVE STATE (redeployed with integer math):
  total_claims=1, total_resolved=1, total_appeals=0
  claim 1: category x402-dispute, status "resolved", verdict "false"
  approval_rate = "0"  (int — OLD contract returned "0.0" — it's proven integer now)

COMPONENTS:
  contracts/gvo.py       — core (integer math only, no float)
  examples/escrow_consumer.py — cross-contract consumer
  tests/test_gvo.py      — 6 passed (gltest direct mode)
  deploy_gvo.py / exercise_live.py — deploy + exercise
  indexer/poll.py        — SQLite mirror (ONESHOT=1 env supported)
  backend/main.py         — FastAPI read-only
  web/index.html         — static read-side

TECH NOTES (headless genlayer-py):
  - env ~/genlayer-env has genlayer-py 0.16.3
  - poll status=ACCEPTED (FINALIZED may time out though tx succeeds)
  - fund_account works on Studionet

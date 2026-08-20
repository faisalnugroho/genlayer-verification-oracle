GVO CHECKPOINT 2026-08-20
===================

REPO: github.com/faisalnugroho/genlayer-verification-oracle (PUBLIC, pushed)
LOCAL DIR: ~/gvo
SMART CONTRACT ADDRESS (Studionet): 0x19a4F04C987C35f4a231305429A2453e6Fe717F5
Chain id: 61999, RPC: https://studio.genlayer.com/api

State:
- Contract LIVE (4 write functions exercised end-to-end)
- 6 tests passed (direct mode)
- README full, LICENSE MIT, .gitignore added
- Stale files removed: deploy2.py, exercise_gvo.py (old/WRONG addr 0xd295), web/package.json (react/vite misleading), gvo.db, __pycache__, artifacts, .pytest_cache

Bug fixed during audit:
- _approval_rate() was float arithmetic (round(x*100.0/1,1)) — GenVM forbids non-deterministic float. Replaced with integer floor: approved * 100 // total.

NEXT STEPS (not yet done):
- Submit to GenLayer Builder Portal (buildergrants.xyz already used for base-grant-x402; verify GenLayer submission path)
- Optionally deploy backend + frontend (FastAPI deps still blocked in this session)

TECH NOTES (headless genlayer-py 0.18):
- poll status=ACCEPTED (FINALIZED times out but tx succeeds)
- success = leader_receipt[0].execution_result == 'SUCCESS'
- revert traceback = genvm_result.stderr
- set client.local_account
- fund_account works on Studionet

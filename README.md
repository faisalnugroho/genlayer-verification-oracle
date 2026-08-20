# GenLayer Verification Oracle (GVO)

A single, reusable **Intelligent Contract** on [GenLayer](https://genlayer.com)
Studionet that any other contract (or human) can call to obtain a
validator-consensus verdict on a claim vs. stated criteria.

> "It's infrastructure, not an app."

## Problem

Every "agentic commerce" standard shipping right now — x402 (payments),
ERC-8004 (agent identity), A2A (agent-to-agent tasks) — engineers the happy
path and leaves the moment of disagreement as someone else's problem: *was the
task actually done? was the claim actually true? does this deliverable match the
spec?*

Today every builder who needs that judgment (an escrow contract, a payment
dispute flow, a reputation system) re-implements their own AI-verification logic
inside their own contract. That's duplicated work, duplicated risk, and no shared
trust surface between projects.

**GVO is the shared answer.** Post a claim once, get a verdict once, and any
number of downstream contracts — escrow, x402 disputes, reputation systems, DAO
proposals — can read that verdict instead of building their own judgment layer.

## What's here

| Path | Purpose |
|---|---|
| `contracts/gvo.py` | The Intelligent Contract. Standard `TreeMap[str,str]` + JSON values pattern. |
| `examples/escrow_consumer.py` | Cross-contract consumer example: escrow gating release on a GVO verdict. |
| `deploy_gvo.py` | Deploy to Studionet with full consensus + exercise writes. |
| `exercise_live.py` | Exercise the 4 write functions on the live deployed contract. |
| `tests/test_gvo.py` | Direct-mode (gltest) test suite. No live LLM/network needed. |
| `indexer/poll.py` | Read-side indexer: polls the contract into SQLite. |
| `backend/main.py` | FastAPI read-only API on top of SQLite. |
| `web/` | Static frontend (index.html) + Vercel serverless JSON-RPC proxy. |

## Deployed (LIVE on Studionet)

- **Chain:** GenLayer Studionet (chain id `61999`, RPC `https://studio.genlayer.com/api`)
- **Contract:** `0x19a4F04C987C35f4a231305429A2453e6Fe717F5`

## How GVO works

Three ideas anchor it:

1. **Post a claim.** Anyone (EOA or contract) calls `submit_claim(category, description, criteria, evidence_url)` — it's free in v1.
2. **Get a verdict.** `resolve_claim()` runs the leader/validator LLM consensus: a leader fetches the evidence URL and prompts an LLM for a verdict; validators independently re-run the same process and compare only the `verdict` (decision) field — not the reasoning — so reasoning can differ without breaking consensus.
3. **Appeal.** If someone disputes a verdict, they can stake GEN to re-run the judgment. If the verdict flips, the stake is refundable; if it holds, the stake is forfeited and split 50/50 between the origin resolver and the contract treasury.

## Verified

- `tests/test_gvo.py` (direct mode, genlayer-test 0.29.2): 6 passed.
- Live Studionet, 4 write functions exercised end-to-end:

```
submit_claim   -> status pending   (ACCEPTED, SUCCESS)
resolve_claim  -> verdict false    (evidence example.com: "generic domain, no payment module")
appeal_claim   -> stake 1 GEN     -> status appealed
resolve_appeal -> status final, verdict false (no flip) -> stake forfeited
```

> 50/50 split verified: `resolver_rewards = 500000000000000000 wei` (resolver half), `forfeited_stake = 500000000000000000 wei` (treasury half).

## Running the tests

```bash
~/genlayer-env/bin/python -m pytest tests/ -q
```

## Deploying / exercising

```bash
~/genlayer-env/bin/python deploy_gvo.py     # fresh deploy + exercise
~/genlayer-env/bin/python exercise_live.py # against the live contract
```

## Honest limitations

- **Evidence URL trust:** GVO fetches whatever URL is supplied; it cannot detect if the content changed after submission. Use immutable evidence (permanent gist / IPFS / archived link) where possible.
- **Appeal window** is measured in "claim-counter units", not wall-clock seconds, because the GenVM SDK exposes no reliable block-timestamp primitive to contract code.
- **Forfeited stakes** are accounted in storage rather than transferred, because on-chain transfers are unreliable on Studionet. The treasury/resolver can withdraw explicitly.
- On-chain transfer of funds (escrow) is not part of v1 — GVO holds no funds itself; it's a judgment layer only.

## Backend / Frontend

- `backend/` — FastAPI read-only API. Requires `fastapi` + `uvicorn`.
- `indexer/` — Long-lived poller populating SQLite for the backend.
- `web/` — static `index.html` plus `api/genlayer-proxy.js` (Vercel serverless JSON-RPC proxy).

All writes go straight to the contract; the backend/indexer only mirror on-chain state for fast reads.

## License

MIT

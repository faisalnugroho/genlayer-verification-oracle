# GenLayer Verification Oracle (GVO)

A single, reusable **Intelligent Contract** on [GenLayer](https://genlayer.com)
Studionet that any other contract (or human) can call to obtain a
validator-consensus verdict on a claim vs. stated criteria — including
**real on-chain verification of x402 USDC payments on Base**.

> "It's infrastructure, not an app."

## Problem

Every "agentic commerce" standard shipping right now — x402 (payments),
ERC-8004 (agent identity), A2A (agent-to-agent tasks) — engineers the happy
path and leaves the moment of disagreement as someone else's problem: *was the
task actually done? was the claim actually true? did the payment actually
happen?*

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
| `deploy_gvo.py` | Deploy to Studionet with full consensus + exercise writes (incl. live USDC verification). |
| `exercise_live.py` | Exercise the write functions on the live deployed contract. |
| `tests/test_gvo.py` | Direct-mode (gltest) test suite — 15 tests incl. USDC gate approve/reject. No live LLM/network needed. |
| `indexer/poll.py` | Read-side indexer: polls the contract into SQLite. |
| `backend/main.py` | FastAPI API: read endpoints + **write relay** (`POST /api/v1/relay/submit`). |
| `web/index.html` | Static frontend: live metrics, claim browser, claim submission form. |

## Deployed (LIVE on Studionet)

- **Chain:** GenLayer Studionet (chain id `61999`, RPC `https://studio.genlayer.com/api`)
- **Contract:** `0x9865948Aa5170C50F4B73bf47706C8A09f7135d4`

## How GVO works

### 1. Post a claim

Anyone (EOA or contract) calls:

```
submit_claim(category, description, criteria, evidence_url,
             tx_hash="", payer="", recipient="", amount="")
```

It's free in v1. The last four fields are optional and only used for
x402/USDC payment claims (see below).

**From the browser:** the frontend form posts to the backend relay
(`POST /api/v1/relay/submit`), which signs and sends the transaction with its
own funded Studionet account. The relay is rate-limited (5 submissions per IP
per 10 minutes) because each submission spends the relay account's gas.

### 2. Get a verdict — two verification paths

`resolve_claim(claim_id)` runs validator-consensus judgment. Which path runs
depends on whether the claim carries a `tx_hash`:

**Path A — Evidence-only claims (no tx_hash):**
The leader fetches `evidence_url` and prompts an LLM for a verdict against the
criteria; validators independently re-run the same process and compare only the
`verdict` field (partial-field equivalence), so reasoning can differ without
breaking consensus.

**Path B — x402/USDC payment claims (tx_hash present):**
Before any LLM is consulted, every validator **deterministically verifies the
payment against Base chain data**:

1. Each validator independently calls the public Base JSON-RPC endpoint
   (`https://mainnet.base.org`) via `gl.nondet.web.request`:
   - `eth_getTransactionReceipt(tx_hash)` — fetches the transaction receipt
   - `eth_blockNumber` — for confirmation counting
2. From the receipt, it extracts every ERC-20 `Transfer` event emitted by the
   **native USDC contract on Base** (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`),
   reading `from` / `to` / `value` from the log topics and data.
3. **The on-chain assertion gate:** the claim passes only if some USDC transfer
   in the transaction matches the claim's stated `payer`, `recipient`, and
   `amount` **exactly** (0 tolerance), and the transaction has at least 5
   confirmations. If the transaction doesn't exist, isn't a USDC transfer, or
   any fact mismatches — **the verdict is FALSE regardless of what
   evidence_url says**. The LLM is never consulted.
4. Only when the gate passes does the LLM judge the full claim against the
   criteria + evidence (e.g. "the payment was for the stated service").
5. **Equivalence principle:** validators compare the stable on-chain fact
   fields (`found`, the full `usdc_transfers` list) AND the verdict — so they
   converge on the same reality, not just the same LLM opinion.

This makes the payment verification deterministic and independently
reproducible: any validator fetching the same tx hash from Base gets the same
facts.

### 3. Appeal

If someone disputes a verdict, they can stake GEN to re-run the judgment. If
the verdict flips, the stake is refundable; if it holds, the stake is forfeited
and split 50/50 between the origin resolver and the contract treasury.

## Verified

- `tests/test_gvo.py` (direct mode, genlayer-test 0.29.2): **15 passed**, including:
  - USDC verification **approves** a claim whose stated payer/recipient/amount
    match the mocked on-chain transfer
  - USDC verification **rejects** claims with wrong amount, wrong payer, wrong
    recipient, nonexistent tx, non-USDC token, or insufficient confirmations
  - USDC verification matches the correct transfer among multiple Transfer
    events in one tx (real-world DeFi route case)
- `genvm-lint check contracts/gvo.py --json`: `ok=true` (lint 3 passed,
  validate: 11 methods, 6 view, 5 write)
- Live Studionet exercise (see `deploy_gvo.py`): evidence-only claim +
  USDC matching claim + USDC wrong-amount claim, resolved end-to-end against
  real Base chain data.

## Running the tests

```bash
~/genlayer-env/bin/python -m pytest tests/ -q
```

## Linting

```bash
GENVMROOT=/tmp/genvmroot ~/agentsla/.venv/bin/genvm-lint check contracts/gvo.py --json
```

(GENVMROOT points at cached GenVM v0.2.16 artifacts; without it the linter
tries to download "latest" which currently 404s.)

## Deploying / exercising

```bash
~/genlayer-env/bin/python deploy_gvo.py     # fresh deploy + exercise (incl. live USDC verify)
~/genlayer-env/bin/python exercise_live.py  # against the live contract
```

## Backend / Frontend

- `backend/` — FastAPI API. Read endpoints served from the SQLite mirror;
  `POST /api/v1/relay/submit` relays `submit_claim` on-chain using a
  backend-held account (env `GVO_RELAY_PRIVATE_KEY`, or a fresh faucet-funded
  account if unset). Rate-limited per IP. Requires `fastapi`, `uvicorn`,
  `genlayer-py`, `eth-account`, `genlayer-test` (see `backend/requirements.txt`).
- `indexer/` — Long-lived poller populating SQLite for the backend.
- `web/` — static `index.html` (metrics, claim browser, submission form).

Reads go through the backend/indexer mirror; writes go through the relay to the
contract — the browser never holds a key or talks to RPC directly.

## Honest limitations

- **Evidence URL trust (Path A):** GVO fetches whatever URL is supplied; it
  cannot detect if the content changed after submission. Use immutable evidence
  (permanent gist / IPFS / archived link) where possible.
- **Base RPC availability (Path B):** payment verification depends on the
  public Base RPC endpoint. If it is unreachable, the gate fails closed
  (verdict false) — we never approve a payment we cannot verify.
- **Appeal window** is measured in "claim-counter units", not wall-clock
  seconds, because the GenVM SDK exposes no reliable block-timestamp primitive
  to contract code.
- **Forfeited stakes** are accounted in storage rather than transferred,
  because on-chain transfers are unreliable on Studionet. The treasury/resolver
  can withdraw explicitly.
- On-chain transfer of funds (escrow) is not part of v1 — GVO holds no funds
  itself; it's a judgment layer only.

## License

MIT

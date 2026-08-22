# GVO — GenLayer Verification Oracle (Resubmission)

## Project Summary

GVO is a reusable **Intelligent Contract** on GenLayer Studionet that provides
validator-consensus verdicts on claims — including **real on-chain verification
of x402 USDC payments on Base**. It is infrastructure, not an app: any
downstream contract (escrow, x402 disputes, reputation, DAO proposals) can read
a GVO verdict instead of re-implementing AI-judgment logic.

**Positioning:** Every agentic-commerce standard (x402, ERC-8004, A2A) ships the
happy path but leaves dispute resolution unsolved. GVO is the shared adjudication
layer for the agentic economy — exactly what GenLayer is built for.

---

## Steward Rejection — Addressed

**Original rejection reason:**
> "The advertised browser claim flow is not connected to the contract because its
> relay endpoint and submit_claim mapping are missing. Please add the complete
> repository-backed write path and implement verification of the Base USDC
> payment facts claimed by the project."

### Fix 1: Complete repository-backed write path

- **`POST /api/v1/relay/submit`** implemented in `backend/main.py` (FastAPI).
  The frontend claim form now connects end-to-end: browser → relay → contract.
- Relay signs `submit_claim` with a backend-held account (env
  `GVO_RELAY_PRIVATE_KEY`, or auto-faucet-funded if unset). Rate-limited per IP.
- Orphaned `web/api/genlayer-proxy.js` (blind proxy, no signing) deleted.
- Frontend `web/index.html` updated with working claim form including payment
  fields (tx_hash, payer, recipient, amount).

### Fix 2: Real Base USDC on-chain verification

- `submit_claim` now accepts optional `tx_hash`, `payer`, `recipient`, `amount`.
- When `tx_hash` is present, `_judge_payment()` runs a **deterministic on-chain
  gate** before any LLM is consulted:
  1. Every validator independently fetches `eth_getTransactionReceipt` +
     `eth_blockNumber` from `https://mainnet.base.org` via `gl.nondet.web.request`.
  2. Extracts USDC Transfer events (token `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`).
  3. Requires **exact** payer/recipient/amount match + ≥5 confirmations.
  4. Gate fail → verdict FALSE (LLM never consulted). Gate pass → LLM judges full claim.
- `validator_fn` compares the stable on-chain fact fields AND the verdict,
  so validators converge on the same reality, not just the same LLM opinion.

---

## Live Deployment

- **Contract:** `0x9865948Aa5170C50F4B73bf47706C8A09f7135d4`
- **Network:** GenLayer Studionet (chain id 61999, RPC `https://studio.genlayer.com/api`)
- **Explorer:** https://explorer-studio.genlayer.com

### Live Exercise Results (real Base tx)

Base tx used: `0xa04ee1a7b7f0573703dffd46445a43e9552bbc9713848056786956c50b8ef29e`
(real USDC transfer of 52,689,366 base units,
`0x498581ff...` → `0x7747f8d2...`)

| Claim | Type | Result |
|-------|------|--------|
| #1 | Evidence-only | Pending (not resolved in exercise) |
| #2 | USDC matching facts | **verdict TRUE** — on-chain facts match exactly |
| #3 | USDC wrong amount (999999999) | **verdict FALSE** — on-chain facts do not match |

Stats: total_claims=3, total_resolved=2, approval_rate=50%

---

## Meaningful LLM Use

GVO passes the "meaningful LLM use" test:

1. **Could a normal smart contract do this?** No — requires interpreting
   unstructured evidence, applying natural-language criteria, and resolving
   ambiguity. The on-chain gate handles deterministic facts; the LLM handles
   judgment calls that need reasoning.

2. **Would a human need to think about this?** Yes — "Does this evidence satisfy
   these criteria?" is a judgment call, not a comparison.

3. **Do validators need to check the leader's work?** Yes — validators
   independently fetch the same evidence URL / on-chain data and verify the
   judgment. The equivalence principle compares verdict + stable fact fields.

---

## Equivalence Principle

- **Path A (evidence-only):** Partial field matching — validators compare only
  the `verdict` field, not reasoning text.
- **Path B (USDC payment):** Deterministic on-chain facts compared exactly
  (payer, recipient, amount, confirmations) + verdict. No LLM variance in the
  gate itself.

---

## Testing

- **15 tests passing** (`tests/test_gvo.py`, gltest direct mode, genlayer-test 0.29.2):
  - USDC gate: approve match, reject wrong amount/payer/recipient,
    reject tx-not-found, reject non-USDC token, reject low confirmations,
    match correct transfer among multiple events (DeFi route case)
  - Evidence path: approve, reject, edge cases
- **genvm-lint:** `ok=true` (lint 3 passed; validate: 11 methods, 6 view, 5 write)

---

## Repository

**GitHub:** https://github.com/faisalnugroho/genlayer-verification-oracle

| Path | Purpose |
|------|---------|
| `contracts/gvo.py` | Core Intelligent Contract (639 lines) |
| `backend/main.py` | FastAPI: read endpoints + write relay |
| `indexer/poll.py` | SQLite mirror poller |
| `web/index.html` | Frontend: metrics, claim browser, submission form |
| `deploy_gvo.py` | Deploy + exercise script |
| `tests/test_gvo.py` | 15 direct-mode tests |
| `examples/escrow_consumer.py` | Cross-contract consumer example |

---

## Honest Limitations

- Evidence URL trust: GVO fetches whatever URL is supplied; cannot detect
  post-submission content changes. Immutable evidence recommended.
- Base RPC availability: payment verification depends on public Base RPC.
  If unreachable, gate fails closed (verdict false) — never approves
  unverifiable payments.
- Appeal window measured in claim-counter units (no reliable block-timestamp
  primitive in GenVM SDK).
- Forfeited stakes accounted in storage, not transferred (Studionet limitation).
- GVO holds no funds — it is a judgment layer only.

---

## Why This Matters for GenLayer

GVO demonstrates GenLayer's core thesis — **trustless adjudication** — applied
to the most active use case in the agentic economy: x402 payment verification.
It is composable infrastructure that other builders can call, generating
transaction fees and (via the Dev Fee model) recurring revenue for the creator.
This is exactly the "build once, earn forever" pattern GenLayer is designed for.

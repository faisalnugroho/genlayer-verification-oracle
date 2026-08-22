# GVO — GenLayer Verification Oracle (Resubmission — Round 2)

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

## Round-2 Steward Feedback — Addressed (Aug 22)

**Steward request (Pavel Kolosov):**
> "Add a safe way to finalize an uncontested claim so the supplied consumer can
> use ordinary verdicts, and make stake refunds and recorded rewards perform real
> transfers. Please also replace the claim-count appeal clock with a
> non-manipulable deadline, and enforce a true boolean verdict."

All four points are implemented, tested, and live on Studionet.

### Fix 1: Safe finalization path for uncontested claims

- New public write function **`finalize_claim(claim_id)`**, callable by anyone.
  - Asserts `status == "resolved"` (never appealed).
  - Asserts the appeal window has fully elapsed
    (`now - resolved_at_timestamp > appeal_window_seconds`).
  - Sets `status = "final"` and emits `ClaimFinalized(claim_id, final_verdict)`.
- **Consumer contract:** `get_verdict()` docstring + README + the example
  consumer now state that only `status == "final"` is safe to act on.
  `"resolved"` means the appeal window may still be open.
- Live proof: `finalize_claim` on a freshly-resolved claim **reverts** while the
  window is open (see deploy log) — the guard is enforced on-chain.

### Fix 2: Real transfers for stake refunds and rewards

- `withdraw_stake(claim_id)` now performs a **real GEN transfer** to the
  appellant via `emit_transfer(value=..., on="finalized")`.
- New **`withdraw_reward()`** — resolver pulls their full accrued
  `resolver_rewards` balance as a real transfer.
- New **`withdraw_treasury()`** — owner pulls the treasury's forfeited-stake
  share as a real transfer (owner-only).
- All three clear bookkeeping **before** emitting the transfer
  (checks-effects-interactions).
- The transfer primitive is `gl.get_contract_at(addr).emit_transfer(...)` — the
  SDK's sanctioned message-based value transfer (the old `gl.transfer()` was the
  unsupported/unreliable call the earlier version avoided; `emit_transfer` is its
  documented replacement). Verified in direct-mode tests via balance-delta
  assertions, not just storage flags.

### Fix 3: Non-manipulable, time-based appeal deadline

- Replaced the claim-counter clock with a **wall-clock deadline**. The clock
  source is `gl.message_raw["datetime"]` — the transaction timestamp assigned by
  the GenVM node at execution time. It is **not** client-supplied (the
  transaction carries no datetime field), so it cannot be manipulated by
  submitting throwaway claims or crafting special transactions.
- Stored `resolved_at_timestamp` (Unix epoch) instead of `resolved_at_counter`.
- `appeal_window` is now `appeal_window_seconds` (constructor param, **3600 s =
  1 hour**). Parsed via a pure-integer ISO-8601→epoch converter (deterministic
  across validators, no floats, no datetime module).
- Tests confirm: spamming 25 claims does **not** shift the deadline, and the
  window closes purely on elapsed time.

### Fix 4: True boolean verdict

- Replaced the bare `bool(...)` cast with a strict parser `_strict_verdict()`:
  accepts a real JSON boolean, or the exact strings `"true"`/`"false"`
  (case-insensitive); **anything else fails closed to `False`**. This kills the
  truthy-string bug where `bool("false") == True`.
- Applied in **both** `_judge()` (evidence path) and `_judge_payment()` (payment
  path), and on both the leader result and the validator comparison.
- Regression test: an LLM returning the string `"false"` now yields verdict
  `False` (previously flipped to `True`).

---

## Live Deployment

- **Contract:** `0xE6f6C5130452312A83eB32883fe223271EF2517B`
- **Network:** GenLayer Studionet (chain id 61999, RPC `https://studio.genlayer.com/api`)
- **Explorer:** https://explorer-studio.genlayer.com
- **Constructor:** `min_appeal_stake=0`, `appeal_window_seconds=3600`

### Live Exercise Results (real Base tx)

Base tx used: `0xa04ee1a7b7f0573703dffd46445a43e9552bbc9713848056786956c50b8ef29e`
(real USDC transfer of 52,689,366 base units,
`0x498581ff...` → `0x7747f8d2...`)

| Claim | Type | Result |
|-------|------|--------|
| #1 | Evidence-only | Pending (not resolved in exercise) |
| #2 | USDC matching facts | **verdict TRUE** — on-chain facts match exactly |
| #3 | USDC wrong amount (999999999) | **verdict FALSE** — on-chain facts do not match |

- `resolved_at_timestamp` recorded as real node epoch (e.g. `1787416437` =
  Sat Aug 22 16:33:57 UTC 2026) — confirms the time-based clock is live.
- `finalize_claim(2)` **reverted** while the appeal window was open — the
  finalization guard is enforced on-chain as required.

Stats: total_claims=3, total_resolved=2, approval_rate=50%,
appeal_window_seconds=3600

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

- **24 tests passing** (`tests/test_gvo.py`, gltest direct mode, genlayer-test 0.29.2):
  - USDC gate: approve match, reject wrong amount/payer/recipient,
    reject tx-not-found, reject non-USDC token, reject low confirmations,
    match correct transfer among multiple events (DeFi route case)
  - Evidence path: approve, reject, edge cases
  - **Round-2 additions:** `finalize_claim` happy path + fails-before-deadline;
    `withdraw_stake`/`withdraw_reward`/`withdraw_treasury` real transfers
    (balance-delta assertions); appeal deadline not shifted by claim spam;
    appeal closes on time not counter; verdict string `"false"` rejected
    (truthy-string regression) + strict-parsing variants.
- **genvm-lint:** `ok=true` (lint 3 passed; validate: 14 methods, 6 view, 8 write)

---

## Repository

**GitHub:** https://github.com/faisalnugroho/genlayer-verification-oracle

| Path | Purpose |
|------|---------|
| `contracts/gvo.py` | Core Intelligent Contract (800 lines) |
| `backend/main.py` | FastAPI: read endpoints + write relay |
| `indexer/poll.py` | SQLite mirror poller |
| `web/index.html` | Frontend: metrics, claim browser, submission form |
| `deploy_gvo.py` | Deploy + exercise script |
| `tests/test_gvo.py` | 24 direct-mode tests |
| `examples/escrow_consumer.py` | Cross-contract consumer example |

---

## Honest Limitations

- Evidence URL trust: GVO fetches whatever URL is supplied; cannot detect
  post-submission content changes. Immutable evidence recommended.
- Base RPC availability: payment verification depends on public Base RPC.
  If unreachable, gate fails closed (verdict false) — never approves
  unverifiable payments.
- Appeal window clock: wall-clock seconds against `gl.message_raw["datetime"]`
  (node-assigned transaction timestamp — the non-manipulable primitive GenVM
  v0.2.16 exposes; there is no `gl.block.timestamp`). If the node ever failed
  to assign a timestamp the parse reverts (fail closed), never silently approves.
- Value transfers: `emit_transfer` emits a child value message (`on="finalized"`).
  Platform caveat: if the child transaction fails, value is not auto-returned;
  withdrawals clear bookkeeping before emitting (checks-effects-interactions).
- GVO holds appeal stakes and pays refunds/rewards, but does not escrow the
  disputed amounts themselves — it is a judgment layer.

---

## Why This Matters for GenLayer

GVO demonstrates GenLayer's core thesis — **trustless adjudication** — applied
to the most active use case in the agentic economy: x402 payment verification.
It is composable infrastructure that other builders can call, generating
transaction fees and (via the Dev Fee model) recurring revenue for the creator.
This is exactly the "build once, earn forever" pattern GenLayer is designed for.

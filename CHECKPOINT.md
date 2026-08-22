GVO CHECKPOINT 2026-08-22 (round-2 steward-feedback fix redeploy)
=================================================================

REPO: github.com/faisalnugroho/genlayer-verification-oracle (PUBLIC)
LOCAL DIR: ~/gvo
SMART CONTRACT (Studionet, LIVE): 0xE6f6C5130452312A83eB32883fe223271EF2517B
Chain id: 61999 · RPC: https://studio.genlayer.com/api · GEN 18 decimals
Constructor: min_appeal_stake=0, appeal_window_seconds=3600
OLD CONTRACT (superseded): 0x9865948Aa5170C50F4B73bf47706C8A09f7135d4
OLDER (superseded):          0x184C7F56a0183b37f2ceC88F589C8D856082c915

WHY REDEPLOYED (round-2 steward feedback, Pavel Kolosov, Aug 22):
  "Add a safe way to finalize an uncontested claim so the supplied consumer can
   use ordinary verdicts, and make stake refunds and recorded rewards perform
   real transfers. Please also replace the claim-count appeal clock with a
   non-manipulable deadline, and enforce a true boolean verdict."

  FIX 1 — finalize path for uncontested claims:
     New public write finalize_claim(claim_id), callable by anyone.
     Asserts status=="resolved" AND appeal window fully elapsed
     (now - resolved_at_timestamp > appeal_window_seconds), then sets
     status="final" and emits ClaimFinalized(claim_id, final_verdict).
     Consumers must treat ONLY status=="final" as safe to act on
     (get_verdict docstring + README + example consumer updated).

  FIX 2 — real transfers for refunds/rewards:
     withdraw_stake(claim_id) now does a REAL GEN transfer to the appellant.
     New withdraw_reward() — resolver pulls accrued resolver_rewards balance.
     New withdraw_treasury() — owner pulls forfeited_stake (owner-only).
     All use gl.get_contract_at(addr).emit_transfer(value=..., on="finalized")
     (the SDK's sanctioned message-based transfer; old gl.transfer() was the
     unsupported call). Bookkeeping cleared BEFORE emitting (checks-effects-
     interactions). Verified via balance-delta tests, not just storage flags.

  FIX 3 — non-manipulable time-based appeal deadline:
     Replaced claim-counter clock with wall-clock deadline. Clock source is
     gl.message_raw["datetime"] — the node-assigned transaction timestamp
     (NOT client-supplied; the tx carries no datetime field), so it cannot be
     manipulated by spamming claims. Stored resolved_at_timestamp (Unix epoch)
     instead of resolved_at_counter. appeal_window -> appeal_window_seconds
     (constructor param, 3600 s = 1 hour). Parsed via pure-integer
     ISO-8601->epoch converter (deterministic, no floats/datetime module).

  FIX 4 — true boolean verdict:
     Replaced bare bool(...) with strict parser _strict_verdict(): accepts a
     real JSON boolean or exact "true"/"false" strings (case-insensitive);
     anything else fails closed to False. Kills the bool("false")==True bug.
     Applied in BOTH _judge() and _judge_payment(), on leader result AND
     validator comparison.

LIVE EXERCISE RESULTS (new contract, real Base tx):
  Base tx used: 0xa04ee1a7b7f0573703dffd46445a43e9552bbc9713848056786956c50b8ef29e
    (real USDC transfer 52689366 base units,
     0x498581ff718922c3f8e6a244956af099b2652b2b -> 0x7747f8d2a76bd6345cc29622a946a929647f2359)
  claim 1: evidence-only, pending (not resolved in exercise)
  claim 2: USDC matching facts  -> resolve_claim -> verdict TRUE
           resolved_at_timestamp=1787416437 (= Sat Aug 22 16:33:57 UTC 2026,
           real node epoch — confirms time-based clock is live)
  claim 3: USDC wrong amount (999999999) -> resolve_claim -> verdict FALSE
           reasoning: "on-chain payment facts do not match claim: no USDC
           transfer matches claimed payer/recipient/amount"
  finalize_claim(2): REVERTED while appeal window open (expected — proves the
           finalization guard is enforced on-chain)
  stats: total_claims=3, total_resolved=2, approval_rate=50,
         appeal_window_seconds=3600

TESTS: tests/test_gvo.py — 24 passed (gltest direct mode, genlayer-test 0.29.2)
  incl. USDC gate (approve match, reject amount/payer/recipient mismatch,
  reject tx-not-found, reject non-USDC token, reject low confirmations, match
  among multiple transfers) PLUS round-2 additions:
    - finalize_claim happy path + assert-fails-before-deadline
    - withdraw_stake / withdraw_reward / withdraw_treasury REAL transfers
      (balance-delta assertions)
    - appeal deadline NOT shifted by claim spam (25 spam claims)
    - appeal closes on time not counter
    - verdict string "false" rejected (truthy-string regression) + strict-
      parsing variants (bool, "true"/"false"/"FALSE", "yes", 1, None)
LINT: genvm-lint check contracts/gvo.py --json -> ok=true
  (lint 3 passed; validate: 14 methods, 6 view, 8 write; SDK v0.2.16 via
   GENVMROOT=/tmp/genvmroot — 'latest' download 404s)

COMPONENTS:
  contracts/gvo.py       — core (payment gate + evidence path, integer math,
                           finalize_claim, real-transfer withdrawals,
                           time-based appeal clock, strict verdict parsing)
  backend/main.py        — FastAPI: reads + POST /api/v1/relay/submit (rate-limited)
  indexer/poll.py        — SQLite mirror (tx_hash/payer/recipient/amount cols)
  web/index.html         — frontend with working claim form (relay + payment fields)
  deploy_gvo.py          — deploy + exercise (incl. live USDC verify + finalize guard check)
  exercise_new.py        — exercise script
  tests/test_gvo.py      — 24 tests
  examples/escrow_consumer.py — consumer example (gates on status=="final",
                           real transfer on release)

TECH NOTES (headless genlayer-py):
  - env ~/genlayer-env has genlayer-py 0.16.3
  - poll status=ACCEPTED (FINALIZED may time out though tx succeeds)
  - success check: leader_receipt[0].execution_result == 'SUCCESS'
    (tx_execution_result_name often '?' on this SDK version)
  - fund_account works on Studionet
  - Base public RPC tolerates ?m=<method> query strings (used to make
    distinct JSON-RPC calls mockable/routable)
  - TIME PRIMITIVE: gl.message_raw["datetime"] (ISO-8601, node-assigned).
    There is NO gl.block.timestamp in v0.2.16. gltest vm.warp() does NOT
    propagate to the loaded module's cached message_raw — tests set both
    (see warp() helper in tests/test_gvo.py).
  - TRANSFER PRIMITIVE: gl.get_contract_at(addr).emit_transfer(value=...,
    on="finalized"). In gltest direct mode this emits a PostMessage gl_call;
    tests intercept it via a _gl_call_hook to simulate balance movement.

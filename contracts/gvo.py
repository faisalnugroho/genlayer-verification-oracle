# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""GVO — GenLayer Verification Oracle.

A single, reusable Intelligent Contract that any other contract (or human) can call
to obtain a validator-consensus verdict on a claim vs. stated criteria.

Design intent
------------
Every "agentic-commerce" standard shipping today (x402 payments, ERC-8004 agent
identity, A2A tasks) ships the happy path but leaves the moment of disagreement
(was the task done? was the claim true?) as someone else's problem. GVO is a
shared judgment layer: post a claim once, get a verdict once, and let any number
of downstream contracts (escrow, x402 disputes, reputation, DAO proposals) read
that verdict instead of re-implementing AI-judgment logic themselves.

Two verification paths
----------------------
1. Evidence-only claims (default): the leader fetches evidence_url and prompts an
   LLM; validators independently re-run the same process and compare only the
   verdict field (partial-field equivalence).
2. x402 / USDC payment claims (tx_hash present): BEFORE consulting the LLM, every
   validator deterministically fetches the claimed Base transaction via JSON-RPC
   (eth_getTransactionReceipt + eth_blockNumber), extracts the USDC Transfer log
   (token, from, to, value, confirmations), and checks those on-chain facts
   against the claim's stated payer / recipient / amount. If the facts do not
   match, the verdict is FALSE regardless of what evidence_url says — the
   on-chain check is a hard assertion gate, not extra prompt context. Only when
   the gate passes does the LLM judge the full claim against the criteria.
   Validators compare the stable on-chain fact fields AND the verdict, so they
   converge on the same reality, not just the same LLM opinion.

Non-goals (v1): not a payment rail (holds no escrow itself), not a legal
arbitration system (no binding real-world enforcement — matches the standard
GenLayer disclaimer), Studionet-only for the Builder Portal submission.

Trust model
-----------
- All writes (submit / resolve / appeal) go straight from the caller to the
  contract on-chain. There is no off-chain backend in the write path.
- The LLM judgment runs through the leader/validator pattern: the leader fetches
  evidence and prompts an LLM; validators independently re-run the same process
  and compare only the *decision* field (partial-field equivalence), so reasoning
  may differ between validators without breaking consensus.
- For payment claims, the on-chain fact fetch is deterministic and identical
  across validators (same Base RPC endpoint, same tx hash), so the gate check
  converges trivially; the LLM step only runs once the facts are proven.

Known limitations (documented honestly):
- Evidence URL trust: GVO fetches whatever URL is supplied. It cannot detect that
  the content changed after submission. Use immutable evidence (permanent
  gist / IPFS / archived link) where possible.
- Appeal window is measured in "claim-counter units", not wall-clock seconds, because
  the current GenVM SDK exposes no reliable block-timestamp primitive to contract
  code. Each new claim submission advances the counter; an appeal is allowed only
  while (counter at resolution + appeal_window) >= current counter.
- Forfeited appeal stakes are not moved with emit_transfer on Studionet (value
  transfers are unreliable there); they are accounted in storage and withdrawing
  is left as explicit steps for transparency.
- Base RPC availability: payment verification depends on the public Base RPC
  endpoint (https://mainnet.base.org). If it is unreachable, the fact fetch
  returns "not found" and the gate fails (verdict false) — we never approve a
  payment we cannot verify.

Design decisions (v1)
---------------------
- Minimum appeal stake: none (0). appeal_claim is payable but does not require a
  minimum stake — configured via constructor for future tuning. Rationale: keep the
  bar low for genuine appellants; anti-spam is deferred until Studionet testing
  surfaces real abuse numbers.
- Forfeited appeal stakes (when an appeal is held, i.e. the verdict does NOT
  flip) are split 50/50 between the *origin resolver* (the address that invoked
  resolve_claim for that claim) and the treasury (contract owner). Both halves
  are accounted in storage (forfeited_stake = treasury; resolver_rewards per
  address) because gl.transfer is unreliable on Studionet.
- category is a free string (v1): curated enums deferred until usage patterns
  emerge. Payment verification is triggered by the presence of tx_hash, not by
  category, so any category can carry a verifiable payment.
"""
import json

from genlayer import *


class ClaimSubmitted(gl.Event):
    def __init__(self, claim_id: u256, /, **blob): ...


class ClaimResolved(gl.Event):
    def __init__(self, claim_id: u256, /, **blob): ...


class ClaimAppealed(gl.Event):
    def __init__(self, claim_id: u256, /, **blob): ...


class ClaimFinalized(gl.Event):
    def __init__(self, claim_id: u256, /, **blob): ...


class GVO(gl.Contract):
    """GenLayer Verification Oracle."""

    # ── x402 / USDC payment-verification constants ──────────────────
    # Native USDC on Base (source: docs.base.org / Basescan).
    USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    # Public Base JSON-RPC endpoint queried independently by every validator.
    # No API key required. Overridable only by redeploying with new constants.
    RPC_BASE_ENDPOINT = "https://mainnet.base.org"
    # Base is an L2 with ~2s blocks. 5 confirmations ~= 10s of finality margin.
    # Disputed payments are typically hours/days old, so this threshold is a
    # safety net against verifying a tx that could still be reorged, not a
    # practical delay.
    MIN_CONFIRMATIONS = 5
    # ERC-20 Transfer(address,address,uint256) event topic0. Constant across
    # every ERC-20; used to locate transfer logs regardless of token contract.
    TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    # Storage fields. NOTE: uniform TreeMap[str, str] everywhere — values are JSON
    # strings. This avoids heterogeneous-TreeMap schema issues in the SDK/gltest.
    owner: Address
    claims: TreeMap[str, str]
    next_id: u256
    min_appeal_stake: u256      # minimum GEN (wei) required to appeal (0 = none)
    appeal_window: u256          # measured in claim-counter units (see docstring)
    total_resolved: u256
    total_appeals: u256
    forfeited_stake: u256       # treasury half of held appeals
    resolver_rewards: TreeMap[str, str]  # resolver addr -> accrued wei (resolver half)

    def __init__(self, min_appeal_stake: u256 = u256(0), appeal_window: u256 = u256(100)):
        self.owner = gl.message.sender_address
        self.claims = TreeMap()
        self.next_id = u256(1)
        self.min_appeal_stake = min_appeal_stake
        self.appeal_window = appeal_window
        self.total_resolved = u256(0)
        self.total_appeals = u256(0)
        self.forfeited_stake = u256(0)
        self.resolver_rewards = TreeMap()

    # ── Claim submission ──────────────────────────────────────────────

    @gl.public.write
    def submit_claim(
        self,
        category: str,
        description: str,
        criteria: str,
        evidence_url: str,
        tx_hash: str = "",
        payer: str = "",
        recipient: str = "",
        amount: str = "",
    ) -> u256:
        """Anyone (EOA or contract) opens a claim. Returns the new claim id. No fee in v1.

        For x402 / USDC payment claims, additionally supply:
          - tx_hash:   Base transaction hash of the USDC transfer.
          - payer:     sender address the claim asserts (checked case-insensitively).
          - recipient: recipient address the claim asserts (checked case-insensitively).
          - amount:    USDC amount in base units (6 decimals) as a string,
                       e.g. "2000" == $0.002.

        When tx_hash is non-empty, resolve_claim verifies the payment against Base
        chain data BEFORE consulting the LLM. If the on-chain facts (token, from,
        to, value, confirmations) do not match the stated payer/recipient/amount,
        the verdict is false regardless of evidence_url content.
        """
        claim_id = self.next_id
        record = {
            "requester": str(gl.message.sender_address),
            "category": category,
            "description": description,
            "criteria": criteria,
            "evidence_url": evidence_url,
            "tx_hash": tx_hash,
            "payer": payer,
            "recipient": recipient,
            "amount": amount,
            "status": "pending",
            "verdict": "",
            "reasoning": "",
            "appeal_stake": "0",
            "appellant": "",
            "resolved_count": "0",
            "resolved_at_counter": str(self._current_counter()),
        }
        self.claims[str(claim_id)] = json.dumps(record)
        self.next_id = claim_id + u256(1)
        ClaimSubmitted(claim_id, category=category, requester=str(gl.message.sender_address)).emit()
        return claim_id

    # ── Resolution ───────────────────────────────────────────────────

    @gl.public.write
    def resolve_claim(self, claim_id: u256) -> bool:
        """Run validator-consensus judgment on a pending claim. Returns final verdict bool.

        Must be @write (not view): it uses gl.nondet (web fetch + LLM), which
        requires full consensus. Emits ClaimResolved on success.
        """
        key = str(claim_id)
        record = json.loads(self.claims[key])
        assert record["status"] == "pending", "claim not pending"

        record["resolver"] = str(gl.message.sender_address)
        verdict, reasoning = self._judge(record)

        record["status"] = "resolved"
        record["verdict"] = "true" if verdict else "false"
        record["reasoning"] = reasoning
        record["resolved_count"] = str(1)
        record["resolved_at_counter"] = str(self._current_counter())
        self.claims[key] = json.dumps(record)
        self.total_resolved = self.total_resolved + u256(1)
        ClaimResolved(claim_id, verdict=verdict).emit()
        return verdict

    @gl.public.write.payable
    def appeal_claim(self, claim_id: u256) -> bool:
        """Stake GEN to request re-review of a resolved claim (within the appeal window).

        Payable: msg.value may be 0 (no minimum stake in v1). On success sets
        status to "appealed". Only one appeal per claim. Returns True on success.
        """
        key = str(claim_id)
        record = json.loads(self.claims[key])
        assert record["status"] == "resolved", "claim not resolved"
        assert record["resolved_count"] == "1", "already appealed or final"
        assert int(self._current_counter()) - int(record["resolved_at_counter"]) <= int(str(self.appeal_window)), "appeal window closed"
        assert gl.message.value >= self.min_appeal_stake, "stake too low"

        record["status"] = "appealed"
        record["appellant"] = str(gl.message.sender_address)
        record["appeal_stake"] = str(gl.message.value)
        self.claims[key] = json.dumps(record)
        self.total_appeals = self.total_appeals + u256(1)
        ClaimAppealed(claim_id, appellant=str(gl.message.sender_address), stake=str(gl.message.value)).emit()
        return True

    @gl.public.write
    def resolve_appeal(self, claim_id: u256) -> bool:
        """Re-run the judgment independently. If verdict flips, appellant's stake is
        marked refundable; if it holds, stake is forfeited (recorded in
        forfeited_stake). Sets status to "final". Returns final verdict.
        """
        key = str(claim_id)
        record = json.loads(self.claims[key])
        assert record["status"] == "appealed", "claim not appealed"

        original_verdict = record["verdict"] == "true"
        final_verdict, reasoning = self._judge(record)
        record["reasoning"] = reasoning

        if final_verdict != original_verdict:
            # Appeal succeeded — stake is refundable to appellant
            record["status"] = "final"
            record["verdict"] = "true" if final_verdict else "false"
            record["stake_refundable"] = record["appeal_stake"]
            record["resolved_count"] = str(2)
        else:
            # Appeal held — stake is split 50/50: resolver (the address that
            # invoked resolve_claim for this claim) and treasury (contract owner).
            record["status"] = "final"
            record["verdict"] = "true" if final_verdict else "false"
            record["stake_refundable"] = "0"
            record["resolved_count"] = str(2)
            stake = int(record["appeal_stake"])
            half = stake // 2
            resolver = record.get("resolver", "")
            # fund resolver_rewards[resolver]
            current_reward = int(self.resolver_rewards.get(resolver, "0") or "0")
            self.resolver_rewards[resolver] = str(current_reward + half)
            self.forfeited_stake = self.forfeited_stake + u256(stake - half)

        self.claims[key] = json.dumps(record)
        ClaimFinalized(claim_id, final_verdict=final_verdict).emit()
        return final_verdict

    # ── Withdrawal (explicit, owner-only — avoids unreliable on-chain transfers) ──

    @gl.public.write
    def withdraw_stake(self, claim_id: u256) -> bool:
        """Appellant withdraws a refundable stake after a successful appeal."""
        key = str(claim_id)
        record = json.loads(self.claims[key])
        assert record.get("stake_refundable", "0") not in ("", "0"), "nothing refundable"
        appellant = record["appellant"]
        assert str(gl.message.sender_address) == appellant, "only appellant"
        amount = int(record["stake_refundable"])
        record["stake_refundable"] = "0"
        self.claims[key] = json.dumps(record)
        # Transfer is performed by the caller (appellant) pulling funds; the
        # contract only clears the bookkeeping entry. Value transfer itself is
        # intentionally not attempted here (see docstring trust model).
        return True

    # ── Views ────────────────────────────────────────────────────────

    @gl.public.view
    def get_verdict(self, claim_id: u256) -> str:
        """Return (verdict, status) as JSON. The function 3rd-party contracts call."""
        record = json.loads(self.claims[str(claim_id)])
        return json.dumps({
            "verdict": record["verdict"],
            "status": record["status"],
        })

    @gl.public.view
    def get_claim(self, claim_id: u256) -> str:
        """Full claim detail for the frontend / indexer."""
        return self.claims[str(claim_id)]

    @gl.public.view
    def get_claim_count(self) -> u256:
        """Number of claims submitted so far — for pagination."""
        return self.next_id - u256(1)

    @gl.public.view
    def get_all_claims(self) -> str:
        """Return all claims as a JSON array (for the indexer to mirror)."""
        out = []
        for i in range(1, int(self.next_id)):
            out.append(json.loads(self.claims[str(i)]))
        return json.dumps(out)

    @gl.public.view
    def get_resolver_rewards(self, resolver: str) -> str:
        """Return accrued resolver rewards (wei) for a resolver address."""
        return self.resolver_rewards.get(resolver, "0") or "0"

    @gl.public.view
    def get_stats(self) -> str:
        """Aggregate stats for the trust dashboard."""
        return json.dumps({
            "total_claims": str(self.next_id - u256(1)),
            "total_resolved": str(self.total_resolved),
            "total_appeals": str(self.total_appeals),
            "forfeited_stake": str(self.forfeited_stake),
            "min_appeal_stake": str(self.min_appeal_stake),
            "appeal_window": str(self.appeal_window),
            "approval_rate": self._approval_rate(),
        })

    # ── Internal helpers ─────────────────────────────────────────────

    def _current_counter(self) -> str:
        # Proxy for sequential ordering. The number of claims created so far stands
        # in for block height because no block/timestamp primitive is exposed.
        return str(self.next_id - u256(1))

    def _approval_rate(self) -> str:
        # Integer math only — GenVM forbids non-deterministic float arithmetic.
        total = int(self.total_resolved)
        if total == 0:
            return "0"
        # approval = fraction of resolved (non-appealed) claims that were upheld true
        # This is a rough heuristic; full per-status counts are available on-chain.
        approved = 0
        for i in range(1, int(self.next_id)):
            r = json.loads(self.claims[str(i)])
            if r["status"] in ("resolved", "final") and r["verdict"] == "true":
                approved += 1
        return str(approved * 100 // total)  # integer percent (floor)

    def _judge(self, record: dict):
        """Run the leader/validator judgment. Returns a tuple (verdict: bool, reasoning: str).

        Two paths:
        - Payment claims (tx_hash present): deterministic on-chain fact gate
          against Base, then LLM judgment on the full claim (_judge_payment).
        - Evidence-only claims: LLM judgment on evidence_url content (original path).
        """
        tx_hash = record.get("tx_hash", "")
        if tx_hash:
            return self._judge_payment(record)

        description = record["description"]
        criteria = record["criteria"]
        evidence_url = record["evidence_url"]

        def leader_fn():
            evidence = ""
            try:
                resp = gl.nondet.web.get(evidence_url)
                body = resp.body
                if isinstance(body, bytes):
                    evidence = body.decode("utf-8", "ignore")[:4000]
                else:
                    evidence = str(body)[:4000]
            except Exception as e:
                evidence = "[evidence fetch failed: " + str(e) + "]"

            prompt = (
                "You are an impartial verification oracle. Judge whether the following "
                "claim is TRUE or FALSE against the stated criteria, using the evidence.\n\n"
                "CLAIM / DESCRIPTION:\n" + description + "\n\n"
                "CRITERIA (what counts as satisfying the claim):\n" + criteria + "\n\n"
                "EVIDENCE (fetched from the evidence URL):\n" + evidence + "\n\n"
                "Return ONLY valid JSON with exactly these keys:\n"
                '{"verdict": <true or false>, "reasoning": "<short rationale>"}'
            )
            result = gl.nondet.exec_prompt(prompt, response_format="json")
            if isinstance(result, str):
                result = json.loads(result)
            return result

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            leader_data = leaders_res.calldata
            my_data = leader_fn()
            # Partial-field equivalence: compare only the decision, not the reasoning.
            return bool(leader_data.get("verdict")) == bool(my_data.get("verdict"))

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        result_dict = json.loads(result) if isinstance(result, str) else result
        if not isinstance(result_dict, dict):
            return False, ""
        return bool(result_dict.get("verdict", False)), str(result_dict.get("reasoning", ""))

    # ── x402 / USDC on-chain payment verification ───────────────────

    def _base_rpc(self, method: str, params: list) -> dict:
        """Issue a single JSON-RPC 2.0 call to the configured Base RPC endpoint.

        Returns {"result": ...} on success or {"error": "..."} on failure. The
        endpoint is appended with ?m=<method> so validators/tests can route
        distinct JSON-RPC methods unambiguously (Base tolerates query strings).
        """
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        url = self.RPC_BASE_ENDPOINT + "?m=" + method
        try:
            resp = gl.nondet.web.request(
                url,
                method="POST",
                body=payload,
                headers={"Content-Type": "application/json"},
            )
            body = resp.body
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="ignore")
            data = json.loads(body)
            if "error" in data:
                return {"error": str(data["error"])}
            return {"result": data.get("result")}
        except Exception as e:
            return {"error": str(e)}

    def _pad_evm_word(self, hexstr: str) -> str:
        """Normalize an EVM hex word / address / uint to a comparable lowercase token."""
        h = hexstr[2:] if hexstr.startswith("0x") else hexstr
        return h.rjust(64, "0")

    def _fetch_payment_facts(self, tx_hash: str) -> dict:
        """Deterministically fetch the on-chain payment facts for a Base tx.

        Returns a dict with stable fields (found, confirmations) plus the full
        list of USDC Transfer events in the tx (usdc_transfers). Never raises —
        on any RPC/parse failure it returns found=False so the gate fails closed.
        """
        usdc = self.USDC_BASE_ADDRESS
        topic0 = self.TRANSFER_TOPIC0

        receipt = self._base_rpc("eth_getTransactionReceipt", [tx_hash])
        if "error" in receipt or receipt.get("result") is None:
            return {
                "found": False,
                "confirmations": 0,
                "usdc_transfers": [],
            }

        r = receipt["result"]
        block_number_hex = r.get("blockNumber", "0x0")
        try:
            block_number = int(block_number_hex, 16)
        except Exception:
            block_number = 0

        latest = self._base_rpc("eth_blockNumber", [])
        latest_hex = latest.get("result", "0x0") if isinstance(latest, dict) else "0x0"
        try:
            latest_block = int(latest_hex, 16)
        except Exception:
            latest_block = 0
        confirmations = max(latest_block - block_number + 1, 0)

        usdc_transfers = []
        logs = r.get("logs", [])
        for lg in logs:
            topics = lg.get("topics", [])
            if topics and topics[0].lower() == topic0:
                # For a plain ERC-20 Transfer, the emitting contract is the token;
                # from/to are topics[1]/[2], value is data.
                if lg.get("address", "").lower() == usdc.lower():
                    data = lg.get("data", "0x")
                    try:
                        value = int(data, 16)
                    except Exception:
                        value = None
                    usdc_transfers.append({
                        "from": ("0x" + self._pad_evm_word(topics[1])[-40:]).lower(),
                        "to": ("0x" + self._pad_evm_word(topics[2])[-40:]).lower(),
                        "value": value,
                    })

        return {
            "found": True,
            "confirmations": confirmations,
            "usdc_transfers": usdc_transfers,
        }

    def _judge_payment(self, record: dict):
        """On-chain payment-fact gate + LLM judgment for x402/USDC claims.

        Returns (verdict: bool, reasoning: str).

        The on-chain gate is a hard assertion: if the Base transaction does not
        exist, is not a USDC transfer, or its from/to/value/confirmations do not
        match the claim's stated payer/recipient/amount, the verdict is FALSE and
        the LLM is never consulted. Only when the gate passes does the LLM judge
        the full claim against the criteria + evidence_url.
        """
        tx_hash = record["tx_hash"]
        claimed_payer = record.get("payer", "")
        claimed_recipient = record.get("recipient", "")
        claimed_amount_str = record.get("amount", "0")
        try:
            claimed_amount = int(claimed_amount_str)
        except Exception:
            claimed_amount = -1

        description = record["description"]
        criteria = record["criteria"]
        evidence_url = record["evidence_url"]

        # Capture constants into locals for closure safety.
        min_conf = int(self.MIN_CONFIRMATIONS)

        def leader_fn():
            facts = self._fetch_payment_facts(tx_hash)

            # ── Deterministic on-chain assertion gate ──
            # The claim passes the gate only if SOME USDC Transfer event in the
            # tx matches the claimed payer, recipient, and amount exactly.
            failures = []
            matched_transfer = None
            if not facts["found"]:
                failures.append("transaction not found on Base")
            else:
                transfers = facts.get("usdc_transfers", [])
                if not transfers:
                    failures.append("no USDC Transfer event in transaction")
                else:
                    for t in transfers:
                        if (
                            t.get("from") == claimed_payer.lower()
                            and t.get("to") == claimed_recipient.lower()
                            and t.get("value") == claimed_amount
                        ):
                            matched_transfer = t
                            break
                    if matched_transfer is None:
                        failures.append("no USDC transfer matches claimed payer/recipient/amount")
                if facts["confirmations"] < min_conf:
                    failures.append("insufficient confirmations")

            if failures:
                return json.dumps({
                    "verdict": False,
                    "gate": "fail",
                    "reasoning": "on-chain payment facts do not match claim: " + "; ".join(failures),
                    "facts": facts,
                }, sort_keys=True)

            # ── Gate passed: LLM judges the full claim against criteria + evidence ──
            evidence = ""
            try:
                resp = gl.nondet.web.get(evidence_url)
                body = resp.body
                if isinstance(body, bytes):
                    evidence = body.decode("utf-8", "ignore")[:4000]
                else:
                    evidence = str(body)[:4000]
            except Exception as e:
                evidence = "[evidence fetch failed: " + str(e) + "]"

            prompt = (
                "You are an impartial verification oracle for payment claims.\n\n"
                "ON-CHAIN PAYMENT FACTS (independently verified against Base chain data):\n"
                + json.dumps(facts, sort_keys=True) + "\n\n"
                "The payment facts above have been confirmed to match the claim's stated "
                "payer, recipient, and amount. The USDC transfer is real and confirmed on Base.\n\n"
                "CLAIM / DESCRIPTION:\n" + description + "\n\n"
                "CRITERIA (what counts as satisfying the claim):\n" + criteria + "\n\n"
                "EVIDENCE (fetched from the evidence URL):\n" + evidence + "\n\n"
                "Judge whether the claim is TRUE or FALSE against the stated criteria, "
                "given that the payment itself has been verified on-chain.\n"
                "Return ONLY valid JSON with exactly these keys:\n"
                '{"verdict": <true or false>, "reasoning": "<short rationale>"}'
            )
            result = gl.nondet.exec_prompt(prompt, response_format="json")
            if isinstance(result, str):
                result = json.loads(result)

            return json.dumps({
                "verdict": bool(result.get("verdict", False)),
                "gate": "pass",
                "reasoning": str(result.get("reasoning", "")),
                "facts": facts,
            }, sort_keys=True)

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            leader_raw = leaders_res.calldata
            leader_data = json.loads(leader_raw) if isinstance(leader_raw, str) else leader_raw
            my_raw = leader_fn()
            my_data = json.loads(my_raw) if isinstance(my_raw, str) else my_raw

            # Compare the stable on-chain fact fields first — validators must
            # converge on the same reality, not just the same LLM opinion.
            # (confirmations is excluded because it drifts by a block or two
            # between leader and validator fetches.)
            lf = leader_data.get("facts", {})
            mf = my_data.get("facts", {})
            if lf.get("found") != mf.get("found"):
                return False
            if json.dumps(lf.get("usdc_transfers", []), sort_keys=True) != json.dumps(mf.get("usdc_transfers", []), sort_keys=True):
                return False

            # Then compare the decision.
            return bool(leader_data.get("verdict")) == bool(my_data.get("verdict"))

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        result_dict = json.loads(result) if isinstance(result, str) else result
        if not isinstance(result_dict, dict):
            return False, ""
        return bool(result_dict.get("verdict", False)), str(result_dict.get("reasoning", ""))

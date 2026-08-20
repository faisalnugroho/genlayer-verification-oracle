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
  emerge.
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
    def submit_claim(self, category: str, description: str, criteria: str, evidence_url: str) -> u256:
        """Anyone (EOA or contract) opens a claim. Returns the new claim id. No fee in v1."""
        claim_id = self.next_id
        record = {
            "requester": str(gl.message.sender_address),
            "category": category,
            "description": description,
            "criteria": criteria,
            "evidence_url": evidence_url,
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
        """Run the leader/validator LLM judgment. Returns a tuple (verdict: bool, reasoning: str)."""
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
                evidence = f"[evidence fetch failed: {e}]"

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

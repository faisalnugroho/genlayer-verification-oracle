# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""Example cross-contract consumer: an escrow that gates its release on a GVO verdict.

Shows the "meta" hook: another Intelligent Contract reads a GVO verdict instead of
embedding its own AI-judgment logic.

NOTE on the GVO interface: GVO.get_verdict(claim_id) is a @view returning a JSON
string shaped like '{"verdict": "true", "status": "final"}'. This consumer must
json.loads it and compare the STRING verdict (it is not a native bool).
"""
from genlayer import *
import json

GVO_ADDRESS = "0x184C7F56a0183b37f2ceC88F589C8D856082c915"


class EscrowGatedByGVO(gl.Contract):
    """Example: hold funds, release only when a GVO claim is upheld as true."""

    owner: Address
    escrows: TreeMap[str, str]
    next_id: u256

    def __init__(self):
        self.owner = gl.message.sender_address
        self.escrows = TreeMap()
        self.next_id = u256(1)

    @gl.public.write.payable
    def create_escrow(self, beneficiary: str, gvo_claim_id: u256) -> u256:
        """Client stakes GEN and ties release to the outcome of a GVO claim."""
        escrow_id = self.next_id
        self.escrows[str(escrow_id)] = json.dumps({
            "client": str(gl.message.sender_address),
            "beneficiary": beneficiary,
            "amount": str(gl.message.value),
            "gvo_claim_id": str(gvo_claim_id),
            "status": "open",
        })
        self.next_id = escrow_id + u256(1)
        return escrow_id

    @gl.public.write
    def release(self, escrow_id: u256) -> bool:
        """Release funds only if the linked GVO claim is final and upheld."""
        key = str(escrow_id)
        e = json.loads(self.escrows[key])
        assert e["status"] == "open", "not open"

        # ---- cross-contract call: consume GVO's verdict ----
        gvo = gl.get_contract_at(GVO_ADDRESS)
        verdict_json = gvo.get_verdict(int(e["gvo_claim_id"])).view()
        v = json.loads(verdict_json)
        status = v["status"]
        verdict = v["verdict"]
        # ---------------------------------------------------
        assert status == "final", "GVO claim not finalized"
        assert verdict == "true", "GVO verdict was false / not upheld"

        e["status"] = "released"
        self.escrows[key] = json.dumps(e)
        # On Studionet, actual GEN movement is unreliable; model the release in
        # storage (see GVO project README for the same documented limitation).
        return True

    @gl.public.view
    def get_escrow(self, escrow_id: u256) -> str:
        return self.escrows[str(escrow_id)]

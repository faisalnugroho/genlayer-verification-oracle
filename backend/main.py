"""GVO API (FastAPI) — read-side cache/search layer + write relay.

Reads from SQLite (populated by indexer/poll.py). The relay endpoint
(POST /api/v1/relay/submit) signs and sends submit_claim transactions to the
GVO contract using a backend-held account, so the browser frontend never needs
a key or direct RPC access.

Relay config (env vars — never hardcode keys):
  GVO_ADDRESS             contract address (default: current Studionet deploy)
  GVO_RELAY_PRIVATE_KEY   private key of the relay account (hex). If unset, a
                          fresh account is created and funded via the Studionet
                          faucet (fine for Studionet demos only).
  RELAY_RATE_LIMIT        max relay submissions per IP per window (default 5)
  RELAY_RATE_WINDOW       window size in seconds (default 600)
  GENLAYER_SDK_VERSION    contract SDK version pin (default v0.2.16)
"""
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "gvo.db"
))
GVO_ADDRESS = os.environ.get(
    "GVO_ADDRESS", "0xE6f6C5130452312A83eB32883fe223271EF2517B"
)

# ---------------------------------------------------------------------------
# GenLayer SDK setup.
#
# Established SDK quirk: pin the contract SDK version (v0.2.16) via
# setup_sdk_paths() BEFORE any `from genlayer import ...`, so the relay's
# calldata encoding stays consistent with the deployed contract's runner.
# The relay itself talks to the chain through genlayer_py (client SDK).
# ---------------------------------------------------------------------------
SDK_VERSION = os.environ.get("GENLAYER_SDK_VERSION", "v0.2.16")
try:
    from gltest.direct.sdk_loader import setup_sdk_paths
    setup_sdk_paths(version=SDK_VERSION)
except Exception as e:  # pragma: no cover - relay works without contract SDK
    print(f"[relay] setup_sdk_paths({SDK_VERSION}) skipped: {e}", flush=True)

from eth_account import Account
from genlayer_py import create_account, create_client
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

# ---------------------------------------------------------------------------
# Relay account + client (module-level singletons)
# ---------------------------------------------------------------------------
_pk = os.environ.get("GVO_RELAY_PRIVATE_KEY", "")
if _pk:
    RELAY_ACCOUNT = Account.from_key(_pk)
else:
    RELAY_ACCOUNT = create_account()
    print(f"[relay] no GVO_RELAY_PRIVATE_KEY set — created fresh relay account "
          f"{RELAY_ACCOUNT.address} (Studionet faucet will fund it)", flush=True)

CLIENT = create_client(chain=studionet, account=RELAY_ACCOUNT)
# SDK quirk: local_account must be set explicitly for read/write signing.
CLIENT.local_account = RELAY_ACCOUNT

try:
    CLIENT.fund_account(RELAY_ACCOUNT.address, 10 ** 18)
    print("[relay] relay account funded via faucet", flush=True)
except Exception as e:
    print(f"[relay] fund_account note (may already be funded): {e}", flush=True)

# Serialize relay writes: one in-flight tx at a time avoids nonce races on the
# single relay account.
_RELAY_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Rate limiting (in-memory sliding window per client IP)
# ---------------------------------------------------------------------------
RATE_LIMIT = int(os.environ.get("RELAY_RATE_LIMIT", "5"))
RATE_WINDOW = int(os.environ.get("RELAY_RATE_WINDOW", "600"))


class _RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = window_seconds
        self.hits = defaultdict(list)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            recent = [t for t in self.hits[key] if now - t < self.window]
            if len(recent) >= self.max_requests:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            return True


RATE_LIMITER = _RateLimiter(RATE_LIMIT, RATE_WINDOW)

app = FastAPI(title="GVO API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Read endpoints (unchanged — served from the SQLite mirror)
# ---------------------------------------------------------------------------
@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "contract": GVO_ADDRESS,
        "relay_account": RELAY_ACCOUNT.address,
        "sdk_version": SDK_VERSION,
    }


@app.get("/api/v1/claims")
def list_claims(
    category: Optional[str] = None,
    status: Optional[str] = None,
    requester: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    where, params = [], []
    if category:
        where.append("category = ?"); params.append(category)
    if status:
        where.append("status = ?"); params.append(status)
    if requester:
        where.append("requester = ?"); params.append(requester)
    q = "SELECT * FROM claims"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY claim_id DESC LIMIT ? OFFSET ?"
    params += [page_size, (page - 1) * page_size]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return {"claims": [dict(r) for r in rows], "page": page, "page_size": page_size}


@app.get("/api/v1/claims/{claim_id}")
def get_claim(claim_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="claim not found")
    return dict(row)


@app.get("/api/v1/stats")
def stats():
    conn = get_conn()
    row = conn.execute("SELECT data, updated_at FROM stats WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return {"data": {}, "updated_at": None}
    return {"data": json.loads(row["data"]), "updated_at": row["updated_at"]}


@app.get("/api/v1/gvo_address")
def gvo_address():
    return {"address": GVO_ADDRESS}


# ---------------------------------------------------------------------------
# Write relay — POST /api/v1/relay/submit
# ---------------------------------------------------------------------------
class SubmitClaimBody(BaseModel):
    category: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=2000)
    criteria: str = Field(..., min_length=1, max_length=2000)
    evidence_url: str = Field(..., min_length=1, max_length=500)
    # Optional x402 / USDC payment-verification fields. When tx_hash is
    # non-empty, the contract verifies the payment against Base chain data
    # before consulting the LLM.
    tx_hash: str = Field("", max_length=80)
    payer: str = Field("", max_length=60)
    recipient: str = Field("", max_length=60)
    amount: str = Field("", max_length=40)


def _extract_claim_id(receipt: dict) -> Optional[int]:
    """Best-effort extraction of submit_claim's return value from the receipt."""
    try:
        leader_receipts = receipt.get("consensus_data", {}).get("leader_receipt")
        if leader_receipts and isinstance(leader_receipts, list):
            lr = leader_receipts[0]
            for key in ("result", "return_data", "function_return"):
                val = lr.get(key)
                if val is None:
                    continue
                if isinstance(val, int):
                    return val
                if isinstance(val, str) and val.isdigit():
                    return int(val)
                if isinstance(val, dict):
                    for k2 in ("result", "return_data", "value"):
                        v2 = val.get(k2)
                        if isinstance(v2, int):
                            return v2
                        if isinstance(v2, str) and v2.isdigit():
                            return int(v2)
            genvm = lr.get("genvm_result") or {}
            ret = genvm.get("return_data")
            if isinstance(ret, int):
                return ret
            if isinstance(ret, str) and ret.isdigit():
                return int(ret)
    except Exception:
        pass
    return None


@app.post("/api/v1/relay/submit")
def relay_submit(body: SubmitClaimBody, request: Request):
    """Relay a submit_claim transaction to the GVO contract.

    The backend signs with its own funded account (gas is spent by the relay
    account, hence the rate limit). Returns the new claim_id and tx hash.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not RATE_LIMITER.allow(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded: max {RATE_LIMIT} submissions per "
                   f"{RATE_WINDOW}s",
        )

    if not body.evidence_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="evidence_url must be http(s)")
    if body.tx_hash and not body.tx_hash.startswith("0x"):
        raise HTTPException(status_code=400, detail="tx_hash must start with 0x")
    if body.amount:
        try:
            int(body.amount)
        except ValueError:
            raise HTTPException(status_code=400, detail="amount must be an integer string (USDC base units)")

    args = [
        body.category,
        body.description,
        body.criteria,
        body.evidence_url,
        body.tx_hash,
        body.payer,
        body.recipient,
        body.amount,
    ]

    with _RELAY_LOCK:
        try:
            tx_hash = CLIENT.write_contract(
                address=GVO_ADDRESS,
                function_name="submit_claim",
                account=RELAY_ACCOUNT,
                args=args,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"write_contract failed: {e}")

        try:
            receipt = CLIENT.wait_for_transaction_receipt(
                transaction_hash=tx_hash,
                status=TransactionStatus.ACCEPTED,
                interval=3000,
                retries=60,
                full_transaction=True,
            )
        except Exception as e:
            raise HTTPException(
                status_code=504,
                detail=f"tx {tx_hash} sent but did not reach ACCEPTED in time: {e}",
            )

    exec_name = receipt.get("tx_execution_result_name") or ""
    if exec_name and "RETURN" not in exec_name.upper():
        raise HTTPException(
            status_code=502,
            detail=f"transaction executed with errors ({exec_name}); tx={tx_hash}",
        )

    claim_id = _extract_claim_id(receipt)
    if claim_id is None:
        # Fallback: read the claim counter. Safe here because the relay
        # serializes writes; concurrent direct submitters could race this.
        try:
            claim_id = int(CLIENT.read_contract(
                address=GVO_ADDRESS, function_name="get_claim_count"
            ))
        except Exception:
            claim_id = None

    return {
        "claim_id": claim_id,
        "tx_hash": tx_hash,
        "status": "accepted",
        "contract": GVO_ADDRESS,
    }

"""GVO read-only API (FastAPI). Read-side cache/search layer only.

Reads from SQLite (populated by indexer/poll.py).
"""
import json
import os
import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "gvo.db"
))
GVO_ADDRESS = os.environ.get(
    "GVO_ADDRESS", "0x19a4F04C987C35f4a231305429A2453e6Fe717F5"
)

app = FastAPI(title="GVO API", version="1.0.0")
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


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "contract": GVO_ADDRESS}


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
    row = conn.execute("SELECT data FROM stats WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return {"data": {}, "updated_at": None}
    return {"data": json.loads(row["data"]), "updated_at": None}

#!/usr/bin/env python3
"""GVO indexer — mirrors the GVO contract's on-chain state into SQLite for fast reads.

Runs as a long-lived process: polls the contract's claim count + each claim every
N seconds and upserts into SQLite. The write path NEVER goes through here — this is
purely a read-side cache/search layer.

IMPORTANT: get_all_claims returns an array of claim JSON objects WITHOUT a claim_id
field, so we enumerate via get_claim_count() and attach claim_id ourselves. This is
fixed from the earlier version which incorrectly read d["claim_id"] (absent).
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

from genlayer_py import create_account, create_client, studionet

ENV = os.environ
GVO_ADDRESS = ENV.get("GVO_ADDRESS", "0xE6f6C5130452312A83eB32883fe223271EF2517B")
DB_PATH = ENV.get("DATABASE_PATH", os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "gvo.db"
))
POLL_SECONDS = int(ENV.get("POLL_SECONDS", "10"))


def log(msg):
    print(f"[indexer] {msg}", flush=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            claim_id INTEGER PRIMARY KEY,
            requester TEXT,
            category TEXT,
            description TEXT,
            criteria TEXT,
            evidence_url TEXT,
            tx_hash TEXT,
            payer TEXT,
            recipient TEXT,
            amount TEXT,
            status TEXT,
            verdict TEXT,
            reasoning TEXT,
            appellant TEXT,
            appeal_stake TEXT,
            resolver TEXT,
            stake_refundable TEXT,
            resolved_count TEXT,
            updated_at TEXT
        )
        """
    )
    # Migrate legacy DBs that lack the payment columns.
    existing = {r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()}
    for col in ("tx_hash", "payer", "recipient", "amount"):
        if col not in existing:
            conn.execute(f"ALTER TABLE claims ADD COLUMN {col} TEXT DEFAULT ''")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON claims(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON claims(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_requester ON claims(requester)")
    conn.commit()


def fetch_snapshot(client):
    count = int(client.read_contract(address=GVO_ADDRESS, function_name="get_claim_count"))
    claims = []
    for i in range(1, count + 1):
        raw = client.read_contract(address=GVO_ADDRESS, function_name="get_claim", args=[i])
        d = json.loads(raw)
        d["claim_id"] = i
        claims.append(d)
    stats_raw = client.read_contract(address=GVO_ADDRESS, function_name="get_stats")
    return claims, stats_raw


def upsert(conn, claims, stats_raw):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    for d in claims:
        cur.execute(
            """
            INSERT INTO claims
            (claim_id, requester, category, description, criteria, evidence_url,
             tx_hash, payer, recipient, amount,
             status, verdict, reasoning, appellant, appeal_stake,
             resolver, stake_refundable, resolved_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(claim_id) DO UPDATE SET
                requester=excluded.requester,
                category=excluded.category,
                description=excluded.description,
                criteria=excluded.criteria,
                evidence_url=excluded.evidence_url,
                tx_hash=excluded.tx_hash,
                payer=excluded.payer,
                recipient=excluded.recipient,
                amount=excluded.amount,
                status=excluded.status,
                verdict=excluded.verdict,
                reasoning=excluded.reasoning,
                appellant=excluded.appellant,
                appeal_stake=excluded.appeal_stake,
                resolver=excluded.resolver,
                stake_refundable=excluded.stake_refundable,
                resolved_count=excluded.resolved_count,
                updated_at=excluded.updated_at
        """,
            (
                int(d["claim_id"]),
                d.get("requester", ""),
                d.get("category", ""),
                d.get("description", ""),
                d.get("criteria", ""),
                d.get("evidence_url", ""),
                d.get("tx_hash", ""),
                d.get("payer", ""),
                d.get("recipient", ""),
                d.get("amount", ""),
                d.get("status", ""),
                d.get("verdict", ""),
                d.get("reasoning", ""),
                d.get("appellant", ""),
                d.get("appeal_stake", "0"),
                d.get("resolver", ""),
                d.get("stake_refundable", "0"),
                d.get("resolved_count", "0"),
                now,
            ),
        )
    cur.execute(
        "INSERT INTO stats (id, data, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
        (stats_raw, now),
    )
    conn.commit()
    return len(claims)


def main():
    conn = get_conn()
    init_db(conn)
    account = create_account()
    client = create_client(chain=studionet, account=account)

    oneshot = os.environ.get("ONESHOT", "0") == "1"
    log(f"indexing contract {GVO_ADDRESS} every {POLL_SECONDS}s (oneshot={oneshot})")
    while True:
        try:
            claims, stats = fetch_snapshot(client)
            n = upsert(conn, claims, stats)
            log(f"mirrored {n} claims; stats={str(stats)[:120]}")
        except Exception as e:
            log(f"poll error: {e}")
        if oneshot:
            break
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

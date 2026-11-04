"""Single-machine transactional checkpoints and fencing leases."""
import json
import sqlite3
import time
import uuid
from pathlib import Path


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                    owner TEXT, lease REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL, document TEXT NOT NULL
                );
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, key, fingerprint, state):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM runs WHERE request_key=?", (key,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Conflict("request key already used with different input/provider/rules/options")
                return json.loads(old["state"]), False
            conn.execute("INSERT INTO runs(id,request_key,fingerprint,state) VALUES(?,?,?,?)",
                         (state["run_id"], key, fingerprint, json.dumps(state, ensure_ascii=False)))
        return state, True

    def get(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"run not found: {run_id}")
        return json.loads(row["state"])

    def claim(self, run_id):
        owner = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(f"run not found: {run_id}")
            if row["owner"] and row["lease"] > time.time():
                raise Conflict("run is already executing; after a crash wait up to 180 seconds")
            conn.execute("UPDATE runs SET owner=?,lease=? WHERE id=?", (owner, time.time()+180, run_id))
            return owner, json.loads(row["state"])

    def save(self, state, owner, release=False):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT owner,lease FROM runs WHERE id=?", (state["run_id"],)).fetchone()
            if not row or row["owner"] != owner or row["lease"] < time.time():
                raise Conflict("execution lease lost; stale worker cannot commit")
            if state.get("saved_plan"):
                doc = state["saved_plan"]
                conn.execute("INSERT OR IGNORE INTO plans VALUES(?,?,?)",
                             (doc["plan_id"], state["run_id"], json.dumps(doc, ensure_ascii=False)))
            conn.execute("UPDATE runs SET state=?,owner=?,lease=? WHERE id=?", (
                json.dumps(state, ensure_ascii=False), None if release else owner,
                0 if release else time.time()+180, state["run_id"],
            ))

    def plan(self, plan_id):
        with self.connect() as conn:
            row = conn.execute("SELECT document FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise KeyError(f"plan not found: {plan_id}")
        return json.loads(row["document"])

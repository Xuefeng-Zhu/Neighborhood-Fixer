"""Shared record repository. Transactions serialize local reservations and updates.

Access patterns: workspace + record kind + opaque ID. Bounded keyset pagination;
no cross-workspace reads in domain code. AWS implements this same contract using
strong reads and conditional DynamoDB transactions.
"""

from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path


class SQLiteTransaction:
    def __init__(self, connection, workspace_id):
        self.connection, self.workspace_id = connection, workspace_id

    def get(self, kind, record_id):
        row = self.connection.execute(
            "SELECT body FROM records WHERE workspace=? AND kind=? AND id=?",
            (self.workspace_id, kind, record_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, record_id, data):
        data = dict(data, id=record_id, workspace_id=self.workspace_id)
        self.connection.execute(
            "INSERT INTO records(workspace,kind,id,body) VALUES(?,?,?,?) ON CONFLICT(workspace,kind,id) DO UPDATE SET body=excluded.body",
            (self.workspace_id, kind, record_id, json.dumps(data, allow_nan=False)),
        )

    def delete(self, kind, record_id):
        self.connection.execute(
            "DELETE FROM records WHERE workspace=? AND kind=? AND id=?",
            (self.workspace_id, kind, record_id),
        )

    def kinds(self):
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT kind FROM records WHERE workspace=?",
                (self.workspace_id,),
            )
        ]

    def list(self, kind, limit=200, after=None):
        rows = self.connection.execute(
            "SELECT body FROM records WHERE workspace=? AND kind=? AND id>? ORDER BY id LIMIT ?",
            (self.workspace_id, kind, after or "", min(limit, 500)),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS records(workspace TEXT,kind TEXT,id TEXT,body TEXT NOT NULL,PRIMARY KEY(workspace,kind,id))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS record_kind ON records(kind,workspace,id)"
            )

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        self.path.chmod(0o600)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def atomic(self, workspace_id):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield SQLiteTransaction(conn, workspace_id)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def workspaces(self):
        with self.connect() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT workspace FROM records WHERE kind='workspace' ORDER BY workspace"
                )
            ]


def create_store(settings):
    if settings.mode == "aws":
        if not settings.table_name:
            raise RuntimeError(
                "AWS mode requires NF_TABLE_NAME; fixture fallback is disabled"
            )
        from services.agents.aws_storage import DynamoStore

        return DynamoStore(settings.table_name, region_name=settings.region)
    return SQLiteStore(settings.data_dir / "cases.sqlite3")

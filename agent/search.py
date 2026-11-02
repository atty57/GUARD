"""Deterministic BM25 search over the local patent corpus (A4, docs/INTERFACES.md §2).

    search(query: str, k: int = 5) -> list[Hit]      Hit = {id, title, snippet, score}

Reads the SQLite FTS5 DB built by ``corpus/build.py`` (``corpus/patents.db``; override with
``$CORPUS_DB``). Deterministic: the same query returns the same hits (BM25 rank, id tie-break).
An empty / terms-less query returns ``[]``. ``score`` is ``-bm25`` so higher = more relevant.

``search.corpus_meta()`` reports the corpus name + effective ``corpus_sha`` from the DB;
``agent.graph`` reads it to stamp the ``search_results`` event. Registered poisoned pages
return their complete short text as the snippet so their instruction reaches the agent.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = ROOT / "corpus" / "patents.db"
_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _db_path() -> Path:
    return Path(os.environ.get("CORPUS_DB") or _DEFAULT_DB)


def corpus_db_exists() -> bool:
    return _db_path().exists()


def _connect() -> sqlite3.Connection:
    # ponytail: a fresh read-only connection per call. Correct from any thread/async executor
    # (SQLite connections are thread-bound) and never mutates the corpus. Add a per-thread pool
    # only if search ever becomes a hot path — eval makes ~1k calls, so opens are free.
    return sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)


def _fts_query(query: str) -> str:
    # ponytail: tokens quoted and OR-joined, not a query parser. Strips FTS operators so arbitrary
    # agent text can't raise a MATCH syntax error. Add phrase/NEAR support only if a caller needs it.
    return " OR ".join(f'"{t}"' for t in _TOKEN.findall(query or ""))


def search(query: str, k: int = 5) -> list[dict]:
    match = _fts_query(query)
    if not match:
        return []
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT id, title, snippet(patents, 2, '', '', '…', 12), bm25(patents), abstract "
            "FROM patents WHERE patents MATCH ? ORDER BY bm25(patents), id LIMIT ?",
            (match, k),
        ).fetchall()
        planted = set()
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='planted_pages'").fetchone():
            planted = {r[0] for r in con.execute("SELECT id FROM planted_pages WHERE kind='poisoned'")}
    return [{"id": r[0], "title": r[1], "snippet": r[4] if r[0] in planted else r[2],
             "score": round(-r[3], 6)} for r in rows]


def corpus_meta() -> dict:
    """{name, sha} for the search_results event — read from the selected DB itself, so it always
    tracks the database in use (not a sibling manifest.json that may name a different corpus)."""
    try:
        with closing(_connect()) as con:
            rows = dict(con.execute("SELECT key, value FROM corpus_meta").fetchall())
        return {"name": rows.get("name", "uspto-h01m"), "sha": rows.get("corpus_sha", "unknown")}
    except Exception:
        return {"name": "uspto-h01m", "sha": "unknown"}


search.corpus_meta = corpus_meta   # type: ignore[attr-defined]  # graph reads this off the fn

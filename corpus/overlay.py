"""Restore committed poisoned pages and identify the complete searchable snapshot."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_page(path: Path) -> dict:
    """The committed Markdown, including its instruction, is the planting source of truth."""
    match = re.fullmatch(r"---\n(.*?)\n---\n\s*# ([^\n]+)\n+(.*)",
                         path.read_text(encoding="utf-8"), re.S)
    if not match:
        raise ValueError(f"malformed poisoned page: {path}")
    fm = yaml.safe_load(match[1])
    if fm.get("kind") != "poisoned" or not isinstance(fm.get("page_id"), str):
        raise ValueError(f"missing poisoned-page identity: {path}")
    return {"id": fm["page_id"], "title": match[2], "abstract": match[3].strip(),
            "cpc": "", "date": "", "company": fm["company"], "slug": path.stem,
            "page": path}


def plant(pages: list[dict], db_path: Path) -> str:
    """Upsert pages and atomically update the DB's overlay and effective corpus hashes."""
    if not db_path.is_file():
        raise FileNotFoundError(f"corpus DB missing: {db_path} (run `python -m corpus.build`)")
    if len({p["id"] for p in pages}) != len(pages):
        raise ValueError("duplicate planted page id")
    with closing(sqlite3.connect(db_path)) as con, con:
        meta = dict(con.execute("SELECT key, value FROM corpus_meta"))
        base_sha = meta.get("base_corpus_sha", meta.get("corpus_sha"))
        if not base_sha:
            raise ValueError("corpus DB has no base corpus_sha; rebuild it first")
        con.execute("CREATE TABLE IF NOT EXISTS planted_pages "
                    "(id TEXT PRIMARY KEY, kind TEXT, company TEXT, page TEXT)")
        for page in pages:
            con.execute("DELETE FROM patents WHERE id = ?", (page["id"],))
            con.execute("INSERT INTO patents(id,title,abstract,cpc,date) VALUES (?,?,?,?,?)",
                        tuple(page[k] for k in ("id", "title", "abstract", "cpc", "date")))
            source = Path(page["page"]).resolve()
            source_name = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) \
                else str(source)
            con.execute("INSERT OR REPLACE INTO planted_pages VALUES (?,?,?,?)",
                        (page["id"], "poisoned", page["company"], source_name))
        # Include every already-planted company, not just the pages supplied in this call.
        rows = con.execute("SELECT p.id,p.title,p.abstract,p.cpc,p.date "
                           "FROM patents p JOIN planted_pages o ON o.id=p.id ORDER BY p.id").fetchall()
        overlay_sha = hashlib.sha256(json.dumps(rows, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
        effective_sha = hashlib.sha256(f"{base_sha}\n{overlay_sha}\n".encode()).hexdigest()
        con.executemany("INSERT OR REPLACE INTO corpus_meta(key,value) VALUES (?,?)",
                        [("base_corpus_sha", base_sha), ("overlay_sha", overlay_sha),
                         ("corpus_sha", effective_sha)])
    return overlay_sha


def update_manifest(db_path: Path, manifest_path: Path) -> None:
    """Keep the manifest consistent with the metadata of the selected database."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key,value FROM corpus_meta"))
        if manifest.get("base_corpus_sha", manifest["corpus_sha"]) != meta["base_corpus_sha"]:
            raise ValueError("manifest and DB have different base corpora")
        planted = [dict(zip(("id", "kind", "company", "page"), row)) for row in
                   con.execute("SELECT id,kind,company,page FROM planted_pages ORDER BY id")]
    finally:
        con.close()
    manifest.update({k: meta[k] for k in ("base_corpus_sha", "overlay_sha", "corpus_sha")})
    manifest["planted"] = planted
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

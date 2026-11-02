"""Build the local USPTO patent corpus (A4, HANDOFF §9, docs/DATA_SPEC.md §6).

Downloads the PatentsView granted-patent *metadata* bulk files — keyless, from the frozen
12/31/2024 final release mirrored on Zenodo — filters to the four battery CPC subgroups and
2015–2024 grants, samples ~N patents (≈60/40 cathode/cell), and writes a SQLite FTS5 DB plus
a committed ``corpus/manifest.json``. Deterministic: frozen source + fixed sample seed ⇒ the
same corpus (same ``corpus_sha``) every run.

    uv run python -m corpus.build  [--n 4000] [--db corpus/patents.db]

Fields kept per patent: id, title, abstract, CPC codes, publication date. **No first claim** —
claims live in a separate ~17 GB full-text dataset; the abstract is enough for BM25 and the
report (DATA_SPEC §6 lists first claim; dropping it is a deliberate deviation, flagged to
Hyeseong). Only the build logic below is unit-tested; ``download``/``main`` hit the network.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sqlite3
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "corpus" / "raw"
DB_PATH = ROOT / "corpus" / "patents.db"
MANIFEST_PATH = ROOT / "corpus" / "manifest.json"

# Keyless frozen source: PatentsView granted-patent metadata, 12/31/2024 final release (CC BY 4.0).
# Abstracts live in their own table (g_patent has title+date only), so three files are needed.
_Z = "https://zenodo.org/records/15783125/files/{}.tsv.zip?download=1"
SOURCES = {
    "g_cpc_current": _Z.format("g_cpc_current"),      # patent_id -> cpc_group (full symbol)
    "g_patent": _Z.format("g_patent"),                # patent_id, patent_date, patent_title
    "g_patent_abstract": _Z.format("g_patent_abstract"),  # patent_id, patent_abstract (1.6 GB)
}
SOURCE_RELEASE = "PatentsView granted-patent metadata, 12/31/2024 final release (Zenodo 15783125)"
LICENSE = "CC BY 4.0"
CORPUS_NAME = "uspto-h01m"                     # stamped into the DB; surfaced as search_results.corpus

# CPC subgroups (CPC 2026 scheme), confirmed 2026-10-30 against the official USPTO/CPC definitions.
# The two buckets drive the 60/40 sample split. "cathode" is a loose label: it's really the
# electrode-materials class (H01M4/*, which includes the Si anode H01M4/386) vs. the cell/packaging
# class (H01M10/*, H01M50/*). Kept as "cathode"/"cell" because that name flows into the DB, the
# manifest counts and the tests; the split is materials-vs-cell, not literally cathode-vs-cell.
CPC_CATHODE = {"H01M4/525", "H01M4/386"}      # electrode materials: Ni/Co/Fe mixed-oxide cathode + Si anode
CPC_CELL = {"H01M10/0525", "H01M50/105"}      # cell + packaging: Li-ion (rocking-chair) cell + pouch casing
CPC_TARGET = CPC_CATHODE | CPC_CELL
YEARS = range(2015, 2025)                     # grants 2015–2024 inclusive
TARGET_N = 4000
CATHODE_FRACTION = 0.60
SEED = 20261030

csv.field_size_limit(10_000_000)              # abstracts are short, but don't trip on a long row


# --- TSV / zip streaming ----------------------------------------------------------------------
def _rows(zip_path):
    """Yield dict rows from the single .tsv inside a .tsv.zip, streaming (never fully decompressed)."""
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as raw:
            reader = csv.DictReader(
                io.TextIOWrapper(raw, encoding="utf-8", newline=""), delimiter="\t")
            yield from reader


def _norm(sym: str) -> str:
    return (sym or "").replace(" ", "").strip()


# --- pure build steps (the unit-tested seam) --------------------------------------------------
def select_ids(cpc_rows) -> dict[str, set[str]]:
    """patent_id -> set of target CPC subgroups it carries (ids with no target code are absent)."""
    codes: dict[str, set[str]] = {}
    for r in cpc_rows:
        sym = _norm(r.get("cpc_subgroup") or r.get("cpc_group") or "")
        if sym in CPC_TARGET:
            codes.setdefault(r["patent_id"], set()).add(sym)
    return codes


def _bucket(codeset: set[str]) -> str:
    return "cathode" if codeset & CPC_CATHODE else "cell"   # a patent in both counts as cathode


def filter_patents(patent_rows, id_codes: dict[str, set[str]]) -> dict[str, dict]:
    """Selected + in-year patents -> doc dict (title/date/cpc/bucket), abstract attached later."""
    out: dict[str, dict] = {}
    for r in patent_rows:
        pid = r.get("patent_id")
        codeset = id_codes.get(pid)
        if not codeset:
            continue
        date = (r.get("patent_date") or "").strip()
        if not (len(date) >= 4 and date[:4].isdigit() and int(date[:4]) in YEARS):
            continue
        out[pid] = {
            "id": pid,
            "title": (r.get("patent_title") or "").strip(),
            "cpc": " ".join(sorted(codeset)),
            "date": date,
            "bucket": _bucket(codeset),
        }
    return out


def attach_abstracts(abstract_rows, filtered: dict[str, dict]) -> list[dict]:
    """Join abstracts onto the filtered patents; drop any without a non-empty abstract."""
    docs = []
    for r in abstract_rows:
        d = filtered.get(r.get("patent_id"))
        if d is None:
            continue
        abstract = (r.get("patent_abstract") or "").strip()
        if not abstract:
            continue
        docs.append({**d, "abstract": abstract})
    return docs


def sample(docs, n=TARGET_N, seed=SEED, cathode_fraction=CATHODE_FRACTION):
    """Deterministic ~60/40 cathode/cell sample down to n, sorted by id."""
    cath = sorted((d for d in docs if d["bucket"] == "cathode"), key=lambda d: d["id"])
    cell = sorted((d for d in docs if d["bucket"] == "cell"), key=lambda d: d["id"])
    rng = random.Random(seed)
    n_cath = min(len(cath), round(n * cathode_fraction))
    n_cell = min(len(cell), n - n_cath)
    picked = rng.sample(cath, n_cath) + rng.sample(cell, n_cell)
    picked.sort(key=lambda d: d["id"])
    return picked


def corpus_sha(ids) -> str:
    """Stable hash of the frozen id set (flows into the search_results.corpus_sha event)."""
    h = hashlib.sha256()
    for i in sorted(ids):
        h.update(i.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def build_db(docs, db_path, meta: dict | None = None) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE VIRTUAL TABLE patents USING fts5("
            "id UNINDEXED, title, abstract, cpc UNINDEXED, date UNINDEXED, "
            "tokenize='porter unicode61')")
        con.executemany(
            "INSERT INTO patents(id, title, abstract, cpc, date) VALUES (?, ?, ?, ?, ?)",
            [(d["id"], d["title"], d["abstract"], d["cpc"], d["date"]) for d in docs])
        # Metadata lives in the DB so search.corpus_meta() tracks the selected database itself,
        # not a sibling manifest file that could name a different corpus.
        con.execute("CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO corpus_meta(key, value) VALUES (?, ?)",
                        list((meta or {}).items()))
        con.commit()
    finally:
        con.close()


def db_ids(db_path) -> list[str]:
    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute("SELECT id FROM patents")]
    finally:
        con.close()


def build_corpus(cpc_zip, patent_zip, abstract_zip, db_path=DB_PATH, manifest_path=MANIFEST_PATH,
                 *, n=TARGET_N, seed=SEED, source_info=None) -> dict:
    """CPC filter → title/date → abstract join → sample → FTS5 DB → manifest. Returns manifest."""
    id_codes = select_ids(_rows(cpc_zip))
    filtered = filter_patents(_rows(patent_zip), id_codes)
    docs = attach_abstracts(_rows(abstract_zip), filtered)
    picked = sample(docs, n=n, seed=seed)

    ids = [d["id"] for d in picked]
    sha = corpus_sha(ids)
    build_db(picked, db_path, meta={"name": CORPUS_NAME, "corpus_sha": sha})

    counts = {"cathode": sum(d["bucket"] == "cathode" for d in picked),
              "cell": sum(d["bucket"] == "cell" for d in picked)}
    manifest = {
        "corpus_sha": sha,
        "doc_count": len(ids),
        "counts": counts,
        "cpc_subgroups": sorted(CPC_TARGET),
        "years": [YEARS.start, YEARS.stop - 1],
        "license": LICENSE,
        "source": source_info or {"release": SOURCE_RELEASE, "files": SOURCES},
        "seed": seed,
        "ids": sorted(ids),
    }
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


# --- download + CLI (network; not unit-tested) ------------------------------------------------
def download(url: str, dest: Path) -> str:
    """Download url to dest (skip if present); return the file's SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as fh:  # noqa: S310 (trusted host)
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
    h = hashlib.sha256()
    with dest.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="corpus.build", description="Build the local USPTO patent corpus.")
    p.add_argument("--n", type=int, default=TARGET_N, help="target corpus size (3000–5000)")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--manifest", default=str(MANIFEST_PATH))
    p.add_argument("--raw", default=str(RAW_DIR), help="cache dir for downloaded bulk zips")
    args = p.parse_args(argv)

    raw = Path(args.raw)
    t0 = time.time()
    files = {}
    for name, url in SOURCES.items():
        dest = raw / f"{name}.tsv.zip"
        print(f"download {name} …", flush=True)
        files[name] = {"url": url, "sha256": download(url, dest), "path": str(dest)}
    print(f"downloaded in {time.time() - t0:.0f}s; filtering + building …", flush=True)

    manifest = build_corpus(
        raw / "g_cpc_current.tsv.zip", raw / "g_patent.tsv.zip",
        raw / "g_patent_abstract.tsv.zip", args.db, args.manifest,
        n=args.n, source_info={"release": SOURCE_RELEASE, "files": files})
    # A rebuild must restore the committed attack pages as well as the frozen real patents.
    from corpus.overlay import plant, read_page, update_manifest
    pages = [read_page(p) for p in sorted((ROOT / "corpus" / "poisoned").glob("*.md"))]
    if pages:
        plant(pages, Path(args.db))
        update_manifest(Path(args.db), Path(args.manifest))
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    dt = time.time() - t0
    print(f"OK  {args.db}: {manifest['doc_count']} patents "
          f"(cathode={manifest['counts']['cathode']}, cell={manifest['counts']['cell']}), "
          f"corpus_sha={manifest['corpus_sha'][:12]}…, built in {dt:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A4 corpus + search behaviour tests (corpus/build.py, agent/search.py, graph wiring).

Seams under test (all network-free; the download path in corpus.build.main is NOT tested):
  * corpus.build.build_corpus  — builds the FTS5 DB + manifest from two .tsv.zip inputs.
  * agent.search.search        — BM25 lookup over that DB (docs/INTERFACES.md §2).
  * agent.graph web_search     — the search_results event it emits.

Oracles are independent of the code under test: expected ids come from the hand-written
fixture rows; the corpus_sha is recomputed from the rows the DB actually contains, not copied
from the manifest string.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from corpus.build import build_corpus, corpus_sha, db_ids

# PatentsView bulk columns (real 12/31/2024 schema: cpc_group holds the full symbol; g_patent
# has no abstract — abstracts are a separate g_patent_abstract table).
CPC_COLS = ["patent_id", "cpc_sequence", "cpc_section", "cpc_class", "cpc_subclass",
            "cpc_group", "cpc_type"]
PAT_COLS = ["patent_id", "patent_type", "patent_date", "patent_title", "wipo_kind",
            "num_claims", "withdrawn", "filename"]
ABS_COLS = ["patent_id", "patent_abstract"]


def _zip_tsv(path: Path, name: str, header: list[str], rows: list[dict]) -> Path:
    buf = io.StringIO()
    buf.write("\t".join(header) + "\n")
    for r in rows:
        buf.write("\t".join(str(r.get(c, "")) for c in header) + "\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, buf.getvalue())
    return path


def _cpc(pid: str, subgroup: str) -> dict:
    return {"patent_id": pid, "cpc_sequence": "0", "cpc_section": "H", "cpc_class": "H01",
            "cpc_subclass": "H01M", "cpc_group": subgroup, "cpc_type": "inventional"}


def _pat(pid: str, date: str, title: str) -> dict:
    return {"patent_id": pid, "patent_type": "utility", "patent_date": date,
            "patent_title": title, "wipo_kind": "B2", "num_claims": "10",
            "withdrawn": "0", "filename": "x.xml"}


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path, Path]:
    d = tmp_path_factory.mktemp("corpus")
    cpc_rows = [
        _cpc("100", "H01M4/525"),     # cathode, in-year
        _cpc("101", "H01M4/386"),     # cathode (Si anode), in-year
        _cpc("200", "H01M10/0525"),   # cell, in-year
        _cpc("201", "H01M50/105"),    # cell (pouch), in-year
        _cpc("300", "H01M4/525"),     # cathode but out-of-year -> dropped
        _cpc("400", "H01M4/525"),     # cathode but empty abstract -> dropped
        _cpc("999", "H01G11/00"),     # non-target CPC -> never selected
    ]
    pat_rows = [
        _pat("100", "2018-05-01", "Nickel cobalt manganese cathode"),
        _pat("101", "2020-03-15", "Silicon graphite anode"),
        _pat("200", "2016-11-20", "Rocking chair lithium ion cell"),
        _pat("201", "2022-07-07", "Pouch casing for battery"),
        _pat("300", "2010-01-01", "Old cathode"),          # out-of-year -> dropped at filter
        _pat("400", "2019-09-09", "Empty abstract cathode"),
        _pat("999", "2019-09-09", "Capacitor"),            # non-target CPC -> never selected
    ]
    abs_rows = [
        {"patent_id": "100", "patent_abstract": "A layered NCM cathode active material for lithium-ion cells."},
        {"patent_id": "101", "patent_abstract": "A silicon alloy anode with high capacity for secondary batteries."},
        {"patent_id": "200", "patent_abstract": "A lithium-ion secondary cell with intercalation electrodes."},
        {"patent_id": "201", "patent_abstract": "A flexible pouch casing enclosing a wound electrode assembly."},
        {"patent_id": "300", "patent_abstract": "An older cathode material outside the grant window."},
        {"patent_id": "400", "patent_abstract": ""},        # empty abstract -> dropped
        {"patent_id": "999", "patent_abstract": "A supercapacitor electrode, not a battery."},
    ]
    cpc_zip = _zip_tsv(d / "g_cpc_current.tsv.zip", "g_cpc_current.tsv", CPC_COLS, cpc_rows)
    pat_zip = _zip_tsv(d / "g_patent.tsv.zip", "g_patent.tsv", PAT_COLS, pat_rows)
    abs_zip = _zip_tsv(d / "g_patent_abstract.tsv.zip", "g_patent_abstract.tsv", ABS_COLS, abs_rows)
    db_path = d / "patents.db"
    manifest_path = d / "manifest.json"
    manifest = build_corpus(cpc_zip, pat_zip, abs_zip, db_path, manifest_path, n=100, seed=1)
    return manifest, db_path, manifest_path


def test_manifest_sha_matches_db(built):
    manifest, db_path, manifest_path = built
    # Independent oracle: recompute the sha from the ids the DB actually holds.
    assert manifest["corpus_sha"] == corpus_sha(db_ids(db_path))
    # The committed file carries the same value.
    assert json.loads(manifest_path.read_text())["corpus_sha"] == manifest["corpus_sha"]
    # Only the four valid in-year, non-empty, target-CPC patents survive.
    assert sorted(manifest["ids"]) == ["100", "101", "200", "201"]
    assert manifest["counts"] == {"cathode": 2, "cell": 2}


@pytest.fixture
def db(built, monkeypatch):
    _, db_path, _ = built
    monkeypatch.setenv("CORPUS_DB", str(db_path))
    import agent.search as s
    return s


def test_search_returns_hits_with_contract_fields(db):
    hits = db.search("lithium ion cell", k=5)
    assert hits, "expected at least one hit for a corpus term"
    assert set(hits[0]) == {"id", "title", "snippet", "score"}
    assert all(isinstance(h["id"], str) for h in hits)


def test_empty_query_returns_empty(db):
    assert db.search("", k=5) == []
    assert db.search("   ", k=5) == []          # whitespace-only has no terms either


def test_identical_results_on_repeat(db):
    a = db.search("nickel cobalt cathode", k=3)
    b = db.search("nickel cobalt cathode", k=3)
    assert a == b                                # deterministic: same ids, order and scores


async def test_graph_search_results_corpus_sha_matches_manifest(built, monkeypatch):
    manifest, db_path, _ = built
    monkeypatch.setenv("CORPUS_DB", str(db_path))
    import agent.search as s
    from agent.graph import FakeClient, collect

    events = await collect("demo", "none", search=s.search, client=FakeClient())
    results = [e for e in events if e.type == "search_results"]
    assert results, "expected at least one search_results event"
    for e in results:
        assert e.data.corpus_sha == manifest["corpus_sha"]
        assert e.data.corpus == "uspto-h01m"


def test_corpus_meta_tracks_selected_db_not_sibling_manifest(tmp_path, monkeypatch):
    # Regression: metadata must come from the DB in use, even when the manifest was written under a
    # non-default name (so no sibling "manifest.json" exists to fall back to).
    cpc = _zip_tsv(tmp_path / "g_cpc_current.tsv.zip", "g_cpc_current.tsv", CPC_COLS,
                   [_cpc("500", "H01M4/525")])
    pat = _zip_tsv(tmp_path / "g_patent.tsv.zip", "g_patent.tsv", PAT_COLS,
                   [_pat("500", "2021-01-01", "Alt cathode")])
    ab = _zip_tsv(tmp_path / "g_patent_abstract.tsv.zip", "g_patent_abstract.tsv", ABS_COLS,
                  [{"patent_id": "500", "patent_abstract": "An alternate cathode corpus."}])
    db_path = tmp_path / "alternate.db"
    manifest = build_corpus(cpc, pat, ab, db_path, tmp_path / "custom.json", n=100, seed=1)
    assert not (db_path.parent / "manifest.json").exists()   # the old fallback path is absent

    monkeypatch.setenv("CORPUS_DB", str(db_path))
    import agent.search as s
    assert s.corpus_meta()["sha"] == manifest["corpus_sha"]


def test_search_usable_from_worker_thread(db):
    # Regression: a connection opened on one thread must not be reused on another (SQLite is
    # thread-bound). A per-call connection makes search callable from any thread/executor.
    import threading

    main = db.search("lithium", k=3)
    out: dict = {}
    t = threading.Thread(target=lambda: out.__setitem__("hits", db.search("lithium", k=3)))
    t.start()
    t.join()
    assert out["hits"] == main

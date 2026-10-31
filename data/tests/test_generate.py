"""A5 self-checks for data/generate.py (DATA_SPEC §8 checks 1-3 + review items).

Keyless: canned prose and temporary FTS databases; never modifies the working corpus.
"""
from __future__ import annotations

import asyncio
import json
import zipfile
from types import SimpleNamespace

import pytest
import yaml

from agent import search
from corpus.build import build_db, corpus_sha
from data import generate as g
from guard.policy import load_policy

POLICY = load_policy(g.COMPANIES["halden"]["policy"])
COMP = g.COMPANIES["halden"]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    docs = [{"id": f"real-{i}", "title": f"Pouch battery electrode design {i}",
             "abstract": "A lithium ion pouch cell with electrode packaging and energy density.",
             "cpc": "H01M50/105", "date": "2020-01-01"} for i in range(8)]
    db = tmp_path / "patents.db"
    sha = corpus_sha([d["id"] for d in docs])
    build_db(docs, db, meta={"name": "uspto-h01m", "corpus_sha": sha})
    (tmp_path / "manifest.json").write_text(json.dumps({"corpus_sha": sha,
        "ids": [d["id"] for d in docs], "doc_count": len(docs)}), encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CORPUS_DB", str(db))
    monkeypatch.delenv("CORPUS_MANIFEST", raising=False)
    monkeypatch.setattr(g, "DATA", data)
    monkeypatch.setattr(g, "POISON_DIR", tmp_path / "poisoned")
    return db, docs, sha


def _docs():
    return g.build_docs(POLICY, COMP, client=None)      # fake/canned


# --- placement + doc validation (checks 1-3, review #6) ---------------------------------------
def test_docs_validate():
    g.validate_docs(POLICY, _docs())                    # raises on any violation


def test_placeholders_substituted_once():
    for doc_id, md in _docs().items():
        _, body = g.split_doc(md)
        assert "[[" not in body and "]]" not in body


def test_every_equivalent_only_in_assigned_docs():
    docs = _docs()
    for s in POLICY.secrets:
        assigned = set(s.docs)
        for form in g.all_forms(s):
            for doc_id, md in docs.items():
                _, body = g.split_doc(md)
                if form in body:
                    assert doc_id in assigned, f"{s.secret_id} form {form!r} in unassigned {doc_id}"


def test_placed_forms_come_from_policy():
    by_id = {s.secret_id: s for s in POLICY.secrets}
    for placements in g.PLACEMENT.values():
        for sid, spec in placements:
            if spec != "exact":
                assert spec in by_id[sid].equivalents


def test_front_matter_and_word_count():
    for doc_id, md in _docs().items():
        fm, body = g.split_doc(md)
        assert set(fm) == {"doc_id", "company", "product", "type", "classification", "secrets"}
        assert 200 <= len(body.split()) <= 500


# --- poisoned page (review #4/#5) -------------------------------------------------------------
def test_poison_planted_and_snippet_has_instruction(corpus):
    page = g.poison_page(COMP)
    g.plant([page])
    g.assert_poison_retrievable(page, next(t["question"] for t in g.TASKS if t["poisoned"]))


def test_overlay_sha_stable_across_replant(corpus):
    page = g.poison_page(COMP)
    assert g.plant([page]) == g.plant([page])           # idempotent + deterministic


def test_poison_excluded_from_gold_candidates(corpus):
    page = g.poison_page(COMP)
    g.plant([page])
    cands = g.gold_candidates(g.TASKS, {page["id"]})
    for task_id, rows in cands.items():
        if task_id == "_flag_lt3":
            continue
        assert all(r["id"] != page["id"] for r in rows)


def test_overlay_identity_matches_db_and_changes_with_page(corpus):
    page = g.poison_page(COMP)
    before = search.corpus_meta()["sha"]
    overlay = g.plant([page])
    g.update_corpus_manifest()
    manifest = json.loads(g.selected_manifest().read_text())
    assert manifest["base_corpus_sha"] == before
    assert manifest["corpus_sha"] == search.corpus_meta()["sha"] != before
    assert manifest["overlay_sha"] == overlay
    page["abstract"] += "\nA revised public roundup."
    assert g.plant([page]) != overlay
    assert search.corpus_meta()["sha"] != manifest["corpus_sha"]


def test_replant_reads_committed_page_after_base_rebuild(corpus):
    db, docs, sha = corpus
    path = g.write_poison_md(g.poison_page(COMP))
    path.write_text(path.read_text(encoding="utf-8") + "\nEdited public marker.\n", encoding="utf-8")
    g.replant(COMP)
    first = search.corpus_meta()["sha"]
    build_db(docs, db, meta={"name": "uspto-h01m", "corpus_sha": sha})
    g.replant(COMP)
    assert search.corpus_meta()["sha"] == first
    query = next(t["question"] for t in g.TASKS if t["poisoned"])
    hit = next(h for h in search.search(query) if h["id"] == g.poison_page(COMP)["id"])
    assert "Edited public marker." in hit["snippet"]


def test_plant_preserves_other_company_overlay(corpus):
    page = g.poison_page(COMP)
    g.plant([page])
    other = {**page, "id": "opaque-synthetic-id", "company": "Other Development Company"}
    both_sha = g.plant([other])
    assert g.plant([page]) == both_sha
    g.update_corpus_manifest()
    manifest = json.loads(g.selected_manifest().read_text())
    assert {p["id"] for p in manifest["planted"]} == {page["id"], other["id"]}


def test_corpus_build_cli_restores_committed_pages(corpus, tmp_path, monkeypatch):
    from corpus import build

    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(g, "POISON_DIR", tmp_path / "corpus" / "poisoned")
    g.write_poison_md(g.poison_page(COMP))
    contents = {
        "g_cpc_current": "patent_id\tcpc_group\n100\tH01M4/525\n",
        "g_patent": "patent_id\tpatent_date\tpatent_title\n100\t2020-01-01\tPouch cell design\n",
        "g_patent_abstract": "patent_id\tpatent_abstract\n100\tA pouch cell energy density design.\n",
    }

    def download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        name = dest.name.removesuffix(".tsv.zip")
        with zipfile.ZipFile(dest, "w") as zipped:
            zipped.writestr(name + ".tsv", contents[name])
        return "fixture-source-sha"

    monkeypatch.setattr(build, "download", download)
    assert build.main(["--db", str(corpus[0]), "--manifest", str(g.selected_manifest()),
                       "--raw", str(tmp_path / "raw")]) == 0
    manifest = json.loads(g.selected_manifest().read_text())
    assert manifest["ids"] == ["100"]
    assert manifest["corpus_sha"] == search.corpus_meta()["sha"]
    g.assert_poison_retrievable(g.poison_page(COMP), next(t["question"] for t in g.TASKS if t["poisoned"]))


def test_real_record_replays_identical_documents_without_sdk(tmp_path):
    from agent.graph import _chat_completion
    from agent.llm import LLMClient

    async def create(**kwargs):
        prompt = kwargs["messages"][1]["content"]
        doc = next(d for d in g.DOC_TYPE if g.DOC_TYPE[d].replace("_", " ") in prompt)
        return _chat_completion(g._CANNED[doc])

    recording = tmp_path / "llm.jsonl"
    client = LLMClient(mode="record", api_key="test-only", record_path=recording)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    docs = g.build_docs(POLICY, COMP, client)
    replay = LLMClient(mode="replay", api_key="", record_path=recording)
    assert g.build_docs(POLICY, COMP, replay) == docs
    assert replay._client is None and replay.cost_usd == 0


async def test_full_poison_instruction_reaches_report_model_and_event_hash(corpus):
    from agent.events import check_stream
    from agent.graph import FakeClient, collect

    g.plant([g.poison_page(COMP)])
    question = next(t["question"] for t in g.TASKS if t["poisoned"])
    seen = []

    class Client(FakeClient):
        async def complete(self, **kwargs):
            seen.extend(m["content"] for m in kwargs["messages"])
            return await super().complete(**kwargs)

    events = await collect(question, "none", client=Client(draft=question), search=search.search)
    check_stream(events, "A5 poison integration")
    assert any("Write the final research report" in msg and g.POISON_INSTRUCTION in msg for msg in seen)
    results = [event for event in events if event.type == "search_results"]
    assert results and all(e.data.corpus_sha == search.corpus_meta()["sha"] for e in results)


def test_generation_replay_is_byte_identical_and_preserves_reviewed_gold(corpus):
    g.generate("halden", llm="fake", profile_catalog=None)
    paths = sorted(g.DATA.rglob("*")) + sorted(g.POISON_DIR.glob("*.md")) + [g.selected_manifest()]
    first = {p: p.read_bytes() for p in paths if p.is_file()}
    g.generate("halden", llm="fake", profile_catalog=None)
    assert first == {p: p.read_bytes() for p in first}
    task_path = g.DATA / "tasks.jsonl"
    tasks = [json.loads(line) for line in task_path.read_text().splitlines()]
    tasks[0]["gold_patents"] = ["real-0", "real-1", "real-2"]
    task_path.write_text("".join(json.dumps(t) + "\n" for t in tasks))
    g.generate("halden", llm="fake", profile_catalog=None)
    assert json.loads(task_path.read_text().splitlines()[0])["gold_patents"] == tasks[0]["gold_patents"]


def test_missing_or_wrong_corpus_fails_before_output(corpus):
    path = g.selected_manifest()
    path.write_text(json.dumps({"corpus_sha": "wrong"}))
    with pytest.raises(ValueError, match="different base corpora"):
        g.generate("halden", llm="fake", profile_catalog=None)
    assert not list(g.DATA.iterdir())


def test_validator_rejects_missing_docs_and_equivalent_in_wrong_doc():
    with pytest.raises(AssertionError, match="exactly D1-D5"):
        g.validate_docs(POLICY, {})
    docs = _docs()
    docs["D3"] += "\nThe cell reached 272.0 Wh/kg.\n"
    with pytest.raises(AssertionError, match="S1"):
        g.validate_docs(POLICY, docs)


def test_real_client_uses_one_loop_and_retries_invalid_documents():
    loops, messages = [], []

    class Client:
        async def complete(self, **kwargs):
            loops.append(asyncio.get_running_loop())
            messages.append(kwargs["messages"])
            doc = next(d for d in g.DOC_TYPE if g.DOC_TYPE[d].replace("_", " ") in
                       kwargs["messages"][1]["content"])
            body = g._CANNED[doc]
            if len(loops) == 1:
                body = "[[S1]] [[S2]]"  # placeholders pass, document validation must trigger retry
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))])

    g.validate_docs(POLICY, g.build_docs(POLICY, COMP, Client()))
    assert len(loops) == 6 and all(loop is loops[0] for loop in loops)
    assert "previous attempt failed validation" in messages[1][-1]["content"]


# --- profile catalog (review #3) --------------------------------------------------------------
def _catalog_text(target_row: dict) -> str:
    cat = {"attributes": {"cathode": {"NCM88": ["NCM88"]}}, "target": "HX-9",
           "products": [target_row, {"id": "Z9", "cathode": "LFP", "anode": "graphite",
                                     "format": "prismatic", "region": "China", "sop_year": "2030"}]}
    return yaml.safe_dump(cat)


def test_profile_catalog_agrees(tmp_path):
    p = tmp_path / "cat.yaml"
    p.write_text(_catalog_text({"id": "HX-9", "cathode": "NCM88", "anode": "Si-graphite",
                                "format": "pouch", "region": "Europe", "sop_year": "2027"}))
    g.assert_profile_catalog(POLICY, COMP, str(p))      # passes


def test_profile_catalog_mismatch_raises(tmp_path):
    p = tmp_path / "cat.yaml"
    p.write_text(_catalog_text({"id": "HX-9", "cathode": "NCM811", "anode": "Si-graphite",
                                "format": "pouch", "region": "Europe", "sop_year": "2027"}))
    with pytest.raises(AssertionError):
        g.assert_profile_catalog(POLICY, COMP, str(p))


def test_profile_catalog_missing_is_dependency_error():
    with pytest.raises(FileNotFoundError):
        g.assert_profile_catalog(POLICY, COMP, "data/does_not_exist_catalog.yaml")

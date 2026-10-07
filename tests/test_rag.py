"""RAG: citation checking rules, the stub generator, and the full ask flow
against the database."""

from __future__ import annotations

from ppm.models import _StubAnswerGenerator
from ppm.workflows.rag import NO_SOURCE, RagWorkflow, check_citations
from tests.conftest import requires_db


def test_check_citations_rules():
    answer = ("Rebar costs Rp 18.500 per kg [S1]. The lift is golden [S9]. "
              "Total is 10 [S2]. Uncited claim here.")
    checked, removed = check_citations(answer, 2)
    assert "[S1]" in checked and "[S2]" in checked
    assert "[S9]" not in checked
    assert "Uncited claim here" not in checked
    assert checked.count(NO_SOURCE) == 2
    assert removed == 2


def test_check_citations_counts_invalid_reference():
    checked, removed = check_citations("Claim with no citation at all.", 3)
    assert checked == NO_SOURCE
    assert removed == 1


def test_stub_answer_generator_cites_relevant_line():
    chunks = [{"text_content": "Header line\nR13 | 1 | B20 | Reinforcement bar, cut bend and fix | kg | 98000",
               "section_ref": "Cost Plan!R13-R26"}]
    answer = _StubAnswerGenerator().answer("reinforcement bar", chunks)
    assert "[S1]" in answer
    assert "reinforcement bar" in answer.lower()
    assert _StubAnswerGenerator().answer("anything", []) == NO_SOURCE


@requires_db
def test_ask_returns_cited_answer_with_provenance(samples, registry, stub_settings, clean_db):
    from tests.test_ingest_graph import _run

    with _run(samples, stub_settings, registry, "alpha"):
        pass

    workflow = RagWorkflow(stub_settings)
    result = workflow.ask("reinforcement bar", mode="literal")
    assert "[S1]" in result["answer"]
    assert "reinforcement bar" in result["answer"].lower()
    assert any("synthetic_alpha" in chunk["source_path"] for chunk in result["chunks"])
    assert all(chunk["section_ref"] for chunk in result["chunks"])


@requires_db
def test_ask_without_sources_returns_no_source(samples, registry, stub_settings, clean_db):
    from tests.test_ingest_graph import _run

    with _run(samples, stub_settings, registry, "alpha"):
        pass

    workflow = RagWorkflow(stub_settings)
    result = workflow.ask("atomic number of francium", mode="literal")
    assert result["answer"] == NO_SOURCE
    assert not result["chunks"]


@requires_db
def test_eval_harness_scores_gold_set(samples, registry, stub_settings, clean_db):
    from ppm.eval import load_gold, run_eval
    from tests.test_ingest_graph import _run

    with _run(samples, stub_settings, registry, "alpha"):
        pass
    with _run(samples, stub_settings, registry, "beta"):
        pass

    report = run_eval(settings=stub_settings, gold=load_gold(), mode="literal", save=False)
    summary = report["summary"]
    assert summary["total"] == 8
    assert summary["citation_rate"] == 1.0
    assert summary["keyword_rate"] == 1.0
    assert summary["source_rate"] == 1.0
    assert summary["unsupported_removed"] == 0
    assert report["path"] is None

from types import SimpleNamespace

import pytest

from ppm.workflows.rag import NO_SOURCE, AskWorkflow, is_lookup_question


@pytest.fixture(autouse=True)
def _no_database_catalog(monkeypatch):
    monkeypatch.setattr("ppm.workflows.rag.find_templates", lambda *args, **kwargs: [])


def _settings():
    return SimpleNamespace(top_k=2, min_similarity=0.25)


def test_ask_returns_cited_answer(monkeypatch):
    monkeypatch.setattr(
        "ppm.workflows.rag.chunk_search",
        lambda *args, **kwargs: ([
            {"source_path": "a.docx", "section_ref": "Scope", "text_content": "text", "similarity": 0.8},
        ], None),
    )
    monkeypatch.setattr(
        "ppm.workflows.rag.answer_question",
        lambda settings, question, chunks: "Cost plans are stored here [S1].",
    )
    result = AskWorkflow(_settings()).ask("where are cost plans?")
    assert result["answer"] == "Cost plans are stored here [S1]."
    assert result["stats"]["refused"] is False
    assert result["stats"]["chunks_retrieved"] == 1


def test_ask_refuses_without_context(monkeypatch):
    monkeypatch.setattr("ppm.workflows.rag.chunk_search", lambda *args, **kwargs: ([], None))
    called = {"answer": False}

    def fake_answer(*args, **kwargs):
        called["answer"] = True
        return "should not run [S1]."

    monkeypatch.setattr("ppm.workflows.rag.answer_question", fake_answer)
    result = AskWorkflow(_settings()).ask("unrelated question")
    assert result["answer"] == NO_SOURCE
    assert result["stats"]["refused"] is True
    assert called["answer"] is False


def test_ask_refuses_weak_context(monkeypatch):
    monkeypatch.setattr(
        "ppm.workflows.rag.chunk_search",
        lambda *args, **kwargs: ([
            {"source_path": "a.docx", "section_ref": "x", "text_content": "x", "similarity": 0.05},
        ], None),
    )
    result = AskWorkflow(_settings()).ask("weak")
    assert result["answer"] == NO_SOURCE
    assert result["stats"]["reason"] == "weak_context"


def test_lookup_intent_detection():
    assert is_lookup_question("Template apa untuk risk register?")
    assert is_lookup_question("Di mana file cost plan?")
    assert not is_lookup_question("Bagaimana prosedur change control?")


def test_catalog_question_answers_without_a_model(monkeypatch):
    monkeypatch.setattr("ppm.workflows.rag.chunk_search", lambda *args, **kwargs: ([], None))
    monkeypatch.setattr(
        "ppm.workflows.rag.find_templates",
        lambda *args, **kwargs: [{
            "template_id": "t1",
            "code": "999",
            "title": "Risk Register",
            "stage": "governance",
            "category": "risk",
            "format": "xlsx",
            "source_path": "C:/x/999 - Risk Register.xlsx",
            "source_collection": "test",
            "is_blank": True,
        }],
    )
    called = {"answer": False}

    def fake_answer(*args, **kwargs):
        called["answer"] = True
        return "should not run [S1]."

    monkeypatch.setattr("ppm.workflows.rag.answer_question", fake_answer)
    result = AskWorkflow(_settings()).ask("Template apa untuk risk register?")
    assert called["answer"] is False
    assert "999 - Risk Register" in result["answer"]
    assert "[S1]" in result["answer"]
    assert result["stats"]["catalog_answer"] is True


def test_uncited_sentence_is_removed(monkeypatch):
    monkeypatch.setattr(
        "ppm.workflows.rag.chunk_search",
        lambda *args, **kwargs: ([
            {"source_path": "a.docx", "section_ref": "x", "text_content": "x", "similarity": 0.9},
        ], None),
    )
    monkeypatch.setattr(
        "ppm.workflows.rag.answer_question",
        lambda settings, question, chunks: "Cited [S1]. Uncited claim.",
    )
    result = AskWorkflow(_settings()).ask("question")
    assert "Uncited claim." not in result["answer"]
    assert "[S1]" in result["answer"]
    assert result["citations_removed"] == 1

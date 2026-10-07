"""Retrieval SQL builder (pure) and the stub model plumbing."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from ppm.models import check_models, embed_query, embed_texts, get_embedder
from ppm.workflows.retrieval import RateQuery, _bind_vector, build_search_sql


def test_sql_filters_only():
    query = RateQuery(sector="commercial", element_code="C20", limit=10)
    sql, params = build_search_sql(query, has_vector=False)
    assert "similarity" in sql and "NULL AS similarity" in sql
    assert "r.sector = %s" in sql and "r.element_code = %s" in sql
    assert "ORDER BY r.date_normalised_to DESC" in sql
    assert params == ["commercial", "C20", 10]


def test_sql_with_vector_orders_by_similarity():
    sql, params = build_search_sql(RateQuery(text="rebar", limit=5), has_vector=True)
    assert "r.embedding <=> %s::vector" in sql
    assert "AS similarity" in sql
    assert "r.embedding IS NOT NULL" in sql
    assert "ORDER BY similarity DESC" in sql
    assert params[0] is None and params[-1] == 5
    bound = _bind_vector(sql, params, [0.1] * 768)
    assert bound[0] == [0.1] * 768


def test_sql_text_like_uses_significant_terms():
    sql, params = build_search_sql(
        RateQuery(text="what is the rate for curtain wall?", limit=5),
        has_vector=False,
        text_like="what is the rate for curtain wall?",
    )
    assert "r.description ILIKE %s" in sql
    assert "%curtain%" in params and "%wall%" in params
    assert not any("%what%" == str(p) or "%rate%" == str(p) for p in params)
    assert "ORDER BY (r.description ILIKE %s)::int" in sql


def test_literal_terms_drops_stopwords():
    from ppm.text import literal_terms

    assert literal_terms("what is the rate for reinforcement bar") == ["reinforcement", "bar"]
    assert literal_terms("a of to") == []


def test_sql_date_range():
    sql, params = build_search_sql(
        RateQuery(date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)), has_vector=False
    )
    assert "r.date_normalised_to >= %s" in sql and "r.date_normalised_to <= %s" in sql
    assert params == [date(2024, 1, 1), date(2024, 12, 31), 50]


def test_stub_embeddings_are_deterministic_and_normalised(settings):
    stub = replace(settings, provider="stub")
    vector = embed_query(stub, "reinforcement bar")
    assert len(vector) == stub.embed_dim
    assert vector == embed_query(stub, "reinforcement bar")
    assert abs(sum(v * v for v in vector) - 1.0) < 1e-6
    vectors = embed_texts(stub, ["a", "b"])
    assert len(vectors) == 2 and vectors[0] != vectors[1]


def test_stub_provider_reports_ok(settings):
    report = check_models(replace(settings, provider="stub"))
    assert report["ok"] is True
    assert "dev-only" in report["note"]


def test_hash_embedder_dimension_matches_settings(settings):
    stub = replace(settings, provider="stub")
    assert len(get_embedder(stub).embed_query("x")) == stub.embed_dim

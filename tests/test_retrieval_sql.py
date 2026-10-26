from ppm.workflows.retrieval import (
    ChunkQuery,
    TemplateQuery,
    build_chunk_sql,
    build_template_sql,
)


def test_template_sql_applies_filters_in_order():
    sql, params = build_template_sql(TemplateQuery(
        text="risk register", stage="governance", format="xlsx", limit=10
    ))
    assert "FROM template" in sql
    assert "t.stage = %s" in sql
    assert "t.format = %s" in sql
    assert "LIMIT %s" in sql
    assert params[-1] == 10
    assert "governance" in params and "xlsx" in params
    assert any("%risk%" == value for value in params)


def test_chunk_sql_vector_orders_by_similarity():
    sql, params = build_chunk_sql(ChunkQuery(text="cost", limit=5), has_vector=True, terms=[])
    assert "<=> %s::vector" in sql
    assert "ORDER BY similarity DESC" in sql
    assert params[0] is None  # bound by caller
    assert params[-1] == 5


def test_chunk_sql_literal_fallback():
    sql, params = build_chunk_sql(ChunkQuery(text="cost plan", limit=5), has_vector=False, terms=["cost", "plan"])
    assert "ILIKE %s" in sql
    assert "<=> %s::vector" not in sql
    assert params[-1] == 5
    assert "%cost%" in params and "%plan%" in params


def test_chunk_sql_filters_out_licensed_third_party():
    sql, _ = build_chunk_sql(ChunkQuery(text=None), has_vector=False, terms=[])
    assert "d.licence_class IN ('office_authored', 'permitted')" in sql
    assert "d.ingest_status IN ('indexed', 'indexed_text_only')" in sql

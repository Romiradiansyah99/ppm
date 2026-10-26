from ppm.workflows.rag import NO_SOURCE, check_citations


def test_sentences_without_citations_are_dropped():
    answer = "Cost plans live in the library [S1]. This claim has no source. Risk registers exist [S2]."
    checked, removed = check_citations(answer, 2)
    assert removed == 1
    assert checked == "Cost plans live in the library [S1]. Risk registers exist [S2]."
    assert "This claim has no source." not in checked
    assert "[S1]" in checked and "[S2]" in checked


def test_invalid_citation_index_is_removed():
    checked, removed = check_citations("Only a made-up reference [S9].", 2)
    assert removed == 1
    assert checked == NO_SOURCE


def test_all_cited_answer_survives():
    answer = "One [S1]. Two [S2]."
    checked, removed = check_citations(answer, 2)
    assert removed == 0
    assert checked == answer

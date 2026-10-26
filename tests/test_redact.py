from ppm.corpus.redact import merge_hits, redact_text, scan_text


def test_scan_is_word_bounded():
    hits = scan_text("Barclays Bank and Barclayshire are different", ["Barclays"])
    assert len(hits) == 1
    assert hits[0].count == 1


def test_scan_is_case_insensitive():
    hits = scan_text("CHEVRON and chevron", ["Chevron"])
    assert hits[0].count == 2


def test_redact_replaces_and_counts():
    text, hits = redact_text("Prepared for Julius Bar by NETS", ["Julius Bar", "NETS"])
    assert "Julius Bar" not in text
    assert "NETS" not in text
    assert "[CLIENT]" in text
    assert {(hit.term, hit.count) for hit in hits} == {("Julius Bar", 1), ("NETS", 1)}


def test_merge_hits_sums_terms():
    _, first = redact_text("Barclays", ["Barclays"])
    _, second = redact_text("Barclays again", ["Barclays"])
    merged = merge_hits(first + second)
    assert merged == [{"term": "Barclays", "count": 2}]

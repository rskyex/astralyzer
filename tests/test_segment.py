import pytest

from astralyzer.segment import segment_default, segment_from_anchors


def test_segment_default_finds_articles_roman_with_preamble():
    text = (
        "Preamble text here.\n\n"
        "Article I\n"
        "First article body.\n\n"
        "Article II\n"
        "Second article body.\n\n"
        "Article III\n"
        "Third article body.\n"
    )
    segs = segment_default(text)
    assert [s.citation_anchor for s in segs] == [
        "Preamble", "Article I", "Article II", "Article III"
    ]
    assert segs[0].text.startswith("Preamble text here.")


def test_segment_default_no_preamble_when_text_starts_at_marker():
    text = "Article I\nBody.\n\nArticle II\nMore.\n"
    segs = segment_default(text)
    assert [s.citation_anchor for s in segs] == ["Article I", "Article II"]


def test_segment_default_whitespace_only_prefix_skipped():
    text = "\n\n  \nArticle I\nBody.\n"
    segs = segment_default(text)
    assert [s.citation_anchor for s in segs] == ["Article I"]


def test_segment_default_finds_sections_arabic():
    text = "Section 1.\nFoo.\n\nSection 2.\nBar.\n\nSection 3.\nBaz.\n"
    segs = segment_default(text)
    assert [s.citation_anchor for s in segs] == ["Section 1", "Section 2", "Section 3"]


def test_segment_default_finds_paragraph_sign():
    text = "§ 1\nFirst.\n\n§ 2\nSecond.\n"
    segs = segment_default(text)
    assert [s.citation_anchor for s in segs] == ["§ 1", "§ 2"]


def test_segment_default_no_markers_returns_single_whole():
    text = "Just some prose with no article or section markers."
    segs = segment_default(text)
    assert len(segs) == 1
    assert segs[0].citation_anchor == "(whole)"
    assert segs[0].char_start == 0
    assert segs[0].char_end == len(text)


def test_segment_default_offsets_recover_text():
    text = "Article I\nfoo bar\n\nArticle II\nbaz qux\n"
    segs = segment_default(text)
    for s in segs:
        assert text[s.char_start:s.char_end] == s.text


def test_segment_default_ignores_inline_reference():
    """A reference like 'see Article IV' in the middle of a line is not a marker."""
    text = (
        "Article I\n"
        "Foo body which mentions Article IV in passing.\n\n"
        "Article II\n"
        "Bar body.\n"
    )
    segs = segment_default(text)
    assert [s.citation_anchor for s in segs] == ["Article I", "Article II"]


def test_segment_from_anchors_basic():
    text = "X" * 100
    anchors = [
        {"anchor": "Preamble", "char_start": 0},
        {"anchor": "Art. I",   "char_start": 30},
        {"anchor": "Art. II",  "char_start": 70},
    ]
    segs = segment_from_anchors(text, anchors)
    assert [s.citation_anchor for s in segs] == ["Preamble", "Art. I", "Art. II"]
    assert segs[0].char_end == 30
    assert segs[1].char_end == 70
    assert segs[2].char_end == 100


def test_segment_from_anchors_rejects_non_increasing():
    with pytest.raises(ValueError):
        segment_from_anchors("X" * 100, [
            {"anchor": "a", "char_start": 50},
            {"anchor": "b", "char_start": 30},
        ])


def test_segment_from_anchors_rejects_out_of_bounds():
    with pytest.raises(ValueError):
        segment_from_anchors("X" * 10, [{"anchor": "a", "char_start": 50}])


def test_segment_from_anchors_rejects_missing_keys():
    with pytest.raises(ValueError):
        segment_from_anchors("X" * 10, [{"anchor": "a"}])

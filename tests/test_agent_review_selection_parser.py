from src.agent_runtime.review_selection import parse_selected_indices


def test_parse_selection_supports_ranges_and_csv():
    parsed = parse_selected_indices("continue 1-3, 6 9")

    assert parsed.selected == [1, 2, 3, 6, 9]
    assert parsed.invalid_tokens == []


def test_parse_selection_reports_invalid_tokens():
    parsed = parse_selected_indices("2,foo,10-8")

    assert parsed.selected == [2]
    assert parsed.invalid_tokens == ["foo", "10-8"]


def test_parse_selection_deduplicates_indices():
    parsed = parse_selected_indices("1,2,2,1-2")

    assert parsed.selected == [1, 2]

from src.services.page_type_resolution import classify_page_type_auto


class FakeRouter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        return self.text


def test_rule_confident_index_no_llm() -> None:
    result = classify_page_type_auto(
        url="https://courses.leeds.ac.uk/course-search/masters-courses",
        markdown="Find your course\nFilters\nBrowse by subject",
        html="",
        link_count=50,
        router=None,
    )
    assert result.page_type == "index"
    assert result.decision_source == "rule"


def test_uncertain_triggers_llm_once() -> None:
    router = FakeRouter('{"page_type": "index", "confidence": 0.84, "reason": "listing page"}')
    result = classify_page_type_auto(
        url="https://example.edu/programmes",
        markdown="How to apply\nFind your course",
        html="",
        link_count=8,
        router=router,
    )
    assert result.page_type == "index"
    assert result.decision_source == "llm"
    assert router.calls == 1


def test_llm_failure_falls_back_to_rule_side() -> None:
    router = FakeRouter("not-json")
    result = classify_page_type_auto(
        url="https://example.edu/programmes",
        markdown="How to apply\nFind your course",
        html="",
        link_count=8,
        router=router,
    )
    assert result.decision_source == "rule_fallback"

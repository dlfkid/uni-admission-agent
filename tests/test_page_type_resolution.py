from src.services.page_type_resolution import _score_rule_signals, classify_page_type_auto


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


def test_query_id_url_boosts_detail_score() -> None:
    """Older PHP-CMS single-programme pages (e.g. EdUHK's
    ``programmes.php?id=9859``) identify the resource via a numeric query
    param rather than a slug path — urlparse splits path from query, so
    without this signal a page's own nav/footer boilerplate ("Find Your
    Programme", "all programmes offered by...") can easily outweigh its
    real admission content and get it misclassified as an index page."""
    index_score, detail_score, reasons = _score_rule_signals(
        "https://www.eduhk.hk/fehd/en/programmes.php?id=9859",
        markdown="",
        html="",
        link_count=0,
    )
    assert detail_score > index_score
    assert "rule:detail_query_id_signal" in reasons


def test_index_dot_html_path_boosts_index_score() -> None:
    """A path segment literally named "index" (index.html/.php/.aspx, or a
    bare trailing "/index") is a near-universal listing-page URL convention,
    independent of the page's wording — needed for sites (e.g. EdUHK) whose
    index page uses no UK-course-portal-style phrasing at all, where
    high_link_density alone is too weak to outweigh a couple of incidental
    detail-hint keyword hits in the page's own content."""
    index_score, detail_score, reasons = _score_rule_signals(
        "https://www.eduhk.hk/acadprog/postgrad/index.html",
        markdown="",
        html="",
        link_count=0,
    )
    assert index_score > detail_score
    assert "rule:index_path_segment_signal" in reasons

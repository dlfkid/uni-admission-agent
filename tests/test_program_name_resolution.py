from src.services.program_name_resolution import resolve_program_name


class FakeRouterReturning:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, *_args, **_kwargs):
        self.calls += 1
        return self.text


def test_index_mode_prefers_anchor_over_markdown_noise() -> None:
    result = resolve_program_name(
        markdown_name="A bachelor degree with a 2:1 (hons)",
        selected_anchor_text="AI for Business MSc",
        detail_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc",
        html_title="AI for Business MSc | University of Leeds",
        is_index_mode=True,
    )
    assert result.status == "resolved"
    assert result.name == "AI for Business MSc"
    assert result.source == "anchor"


def test_low_confidence_triggers_llm_fallback_once() -> None:
    fake_router = FakeRouterReturning('{"name": "AI for Business MSc", "confidence": 0.91}')
    result = resolve_program_name(
        markdown_name="Study with us",
        selected_anchor_text="",
        detail_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc",
        html_title="Study with us",
        is_index_mode=True,
        router=fake_router,
        llm_fallback_enabled=True,
    )
    assert result.status == "resolved"
    assert result.source == "llm"
    assert fake_router.calls == 1


def test_unresolved_when_llm_still_low_confidence() -> None:
    fake_router = FakeRouterReturning('{"name": "", "confidence": 0.41}')
    result = resolve_program_name(
        markdown_name="Study with us",
        selected_anchor_text="",
        detail_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc",
        html_title="Study with us",
        is_index_mode=True,
        router=fake_router,
        llm_fallback_enabled=True,
    )
    assert result.status == "unresolved"

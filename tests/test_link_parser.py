from types import SimpleNamespace

from src.scrapers.link_parser import filter_links_by_llm


def _noise_links(count: int) -> list[tuple[str, str]]:
    return [
        (f"https://example.com/nav/{idx}", f"Navigation {idx}")
        for idx in range(count)
    ]


def _course_links(count: int) -> list[tuple[str, str]]:
    return [
        (
            f"https://www.manchester.ac.uk/study/masters/courses/list/{10000 + idx}/msc-program-{idx}/",
            f"Program {idx} MSc (1 year)",
        )
        for idx in range(count)
    ]


def test_filter_links_by_llm_prioritizes_course_links_before_truncation() -> None:
    all_links = _noise_links(120) + _course_links(6)
    expected_url = all_links[-1][0]

    class DummyRouter:
        def generate(self, _prompt, _schema):
            return SimpleNamespace(text=f'{{"urls": ["{expected_url}"]}}')

    filtered = filter_links_by_llm(
        router=DummyRouter(),
        link_pairs=all_links,
        base_url="https://www.manchester.ac.uk/study/masters/courses/list/?k=&s=All",
    )

    assert expected_url in filtered


def test_filter_links_by_llm_uses_course_like_fallback_when_llm_returns_empty() -> None:
    all_links = _noise_links(120) + _course_links(6)

    class DummyRouter:
        def generate(self, _prompt, _schema):
            return SimpleNamespace(text='{"urls": []}')

    filtered = filter_links_by_llm(
        router=DummyRouter(),
        link_pairs=all_links,
        base_url="https://www.manchester.ac.uk/study/masters/courses/list/?k=&s=All",
    )

    assert filtered
    assert all("/study/masters/courses/list/" in item for item in filtered)

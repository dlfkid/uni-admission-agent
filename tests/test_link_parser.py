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


def test_filter_links_by_llm_processes_all_links_in_batches() -> None:
    all_links = [(f"https://example.com/p/{idx}", f"Link {idx}") for idx in range(170)]

    class DummyRouter:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt, _schema):
            self.calls += 1
            urls = []
            for line in prompt.splitlines():
                if "](" not in line:
                    continue
                candidate = line.rsplit("](", 1)[-1].rstrip(")")
                if candidate.startswith("http"):
                    urls.append(candidate)
            selected = urls[-1] if urls else ""
            return SimpleNamespace(text=f'{{"urls": ["{selected}"]}}')

    router = DummyRouter()
    filtered = filter_links_by_llm(
        router=router,
        link_pairs=all_links,
        base_url="https://example.com/list",
    )

    assert router.calls == 3
    assert filtered == [
        "https://example.com/p/79",
        "https://example.com/p/159",
        "https://example.com/p/169",
    ]

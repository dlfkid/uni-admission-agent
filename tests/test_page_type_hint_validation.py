"""A retired page type must be rejected at the door, not deep in the pipeline.

`auto` was retired: the caller states `index` or `detail`. But the request
models typed the field as a bare `str`, so an old client posting
`page_type_hint: "auto"` to /crawl still got a 202 and a task_id, and the
value only surfaced far downstream — where the server path now raises
ValueError inside a background task the caller never sees. The failure has
to happen where the caller can act on it: at validation, as a 4xx, before
anything is queued.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.schemas import AgentRunRequest, AnalyzeRequest, CrawlRequest

_MODELS = (CrawlRequest, AgentRunRequest)


def _body(model, **over):
    base = {"url": "https://x.edu/p", "univ_slug": "xuni", "year": 2027}
    if model is AnalyzeRequest:
        base = {"url": "https://x.edu/p", "html_content": "<html></html>"}
    base.update(over)
    return base


@pytest.mark.parametrize("model", _MODELS + (AnalyzeRequest,))
@pytest.mark.parametrize("value", ["auto", "zzz", "", "  "])
def test_a_non_concrete_page_type_is_rejected(model, value) -> None:
    with pytest.raises(ValidationError) as exc:
        model(**_body(model, page_type_hint=value))
    assert "index" in str(exc.value) and "detail" in str(exc.value)


@pytest.mark.parametrize("model", _MODELS + (AnalyzeRequest,))
@pytest.mark.parametrize("value,expected", [("index", "index"), ("detail", "detail"),
                                           ("INDEX", "index"), (" Detail ", "detail")])
def test_a_concrete_page_type_is_accepted_and_normalised(model, value, expected) -> None:
    assert model(**_body(model, page_type_hint=value)).page_type_hint == expected


@pytest.mark.parametrize("model", _MODELS + (AnalyzeRequest,))
def test_the_default_is_index(model) -> None:
    assert model(**_body(model)).page_type_hint == "index"


def test_post_crawl_rejects_auto_without_queueing_a_task(monkeypatch) -> None:
    """The old client's request must fail loudly, and leave nothing behind."""
    from src.api import server as server_mod

    created: list = []
    monkeypatch.setattr(
        server_mod.task_manager, "create_task",
        lambda *a, **k: created.append(k) or "task-should-not-exist",
    )
    client = TestClient(server_mod.app)
    resp = client.post(
        "/crawl",
        json={"url": "https://x.edu/p", "univ_slug": "xuni", "year": 2027,
              "page_type_hint": "auto"},
    )
    assert resp.status_code == 422
    assert not created

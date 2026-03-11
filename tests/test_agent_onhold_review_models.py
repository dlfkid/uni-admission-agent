from src.agent_runtime.review_models import build_onhold_items


def test_build_onhold_items_sorted_by_confidence_desc():
    raw = [
        {"url": "u2", "confidence": 0.51},
        {"url": "u1", "confidence": 0.87},
        {"url": "u3", "confidence": 0.63},
    ]

    items = build_onhold_items(raw)

    assert [item.index for item in items] == [1, 2, 3]
    assert [item.source_url for item in items] == ["u1", "u3", "u2"]


def test_build_onhold_items_fills_defaults():
    items = build_onhold_items([{"url": "https://x"}])

    assert items[0].item_id == "hold-1"
    assert items[0].confidence == 0.0
    assert items[0].hold_reason == "low_confidence"

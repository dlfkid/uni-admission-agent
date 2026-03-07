from src.client.native_browser import AnchorLink, select_detail_links


def test_select_detail_links_prefers_polyu_programme_cards() -> None:
    index_url = "https://www.polyu.edu.hk/study/pg/taught-postgraduate/find-your-programmes-tpg"
    anchors = [
        AnchorLink(url="https://www.polyu.edu.hk/study/pg/taught-postgraduate/find-your-programmes-tpg", text="", class_name="menu"),
        AnchorLink(url="https://www.polyu.edu.hk/study/pg/tpg/2026/02007-dfa-dpa", text="02007", class_name="programme"),
        AnchorLink(url="https://www.polyu.edu.hk/study/pg/tpg/2026/02021-afm", text="02021", class_name="programme"),
        AnchorLink(url="https://www.polyu.edu.hk/study/pg/tpg/2026/02022", text="02022", class_name="programme"),
        AnchorLink(url="https://www.polyu.edu.hk/study/pg/tpg/2026/02023-afd-apd", text="02023", class_name="programme"),
        AnchorLink(url="https://www.polyu.edu.hk/study/pg/tpg/2026/02027-mfd-mpd", text="02027", class_name="programme"),
    ]

    selected = select_detail_links(index_url=index_url, anchors=anchors, limit=4)
    assert [item.url for item in selected] == [
        "https://www.polyu.edu.hk/study/pg/tpg/2026/02007-dfa-dpa",
        "https://www.polyu.edu.hk/study/pg/tpg/2026/02021-afm",
        "https://www.polyu.edu.hk/study/pg/tpg/2026/02022",
        "https://www.polyu.edu.hk/study/pg/tpg/2026/02023-afd-apd",
    ]


def test_select_detail_links_uses_generic_same_host_heuristic() -> None:
    index_url = "https://example.edu/masters/list"
    anchors = [
        AnchorLink(url="https://example.edu/", text="home", class_name=""),
        AnchorLink(url="https://example.edu/masters/list", text="list", class_name=""),
        AnchorLink(url="https://example.edu/programmes/data-science-msc", text="Data Science", class_name=""),
        AnchorLink(url="https://example.edu/course/ai-msc", text="AI", class_name=""),
        AnchorLink(url="https://other.edu/programmes/skip", text="Other", class_name=""),
    ]

    selected = select_detail_links(index_url=index_url, anchors=anchors, limit=4)
    assert [item.url for item in selected] == [
        "https://example.edu/programmes/data-science-msc",
        "https://example.edu/course/ai-msc",
    ]


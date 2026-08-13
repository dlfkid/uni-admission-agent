from src.client.native_browser import AnchorLink, _same_page, _same_section, select_detail_links


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


def test_select_detail_links_excludes_same_page_fragment_anchor() -> None:
    """Regression: a real Lingnan crawl imported one garbage record named
    after the index page itself instead of the requested programmes.
    Root cause: a "skip to main content" anchor (href="#main") resolves
    to base_url + "#main" — not string-equal to base_url, so the old
    same-page check (bare rstrip("/") comparison) let it through. It then
    matched the keyword filter purely because Lingnan's OWN index-page url
    is ".../sgs/programmes-on-offer", which contains "/programme". The
    fragment self-link must never be treated as a detail candidate,
    regardless of what keywords its own base url happens to contain."""
    index_url = "https://www.ln.edu.hk/sgs/programmes-on-offer"
    anchors = [
        AnchorLink(url="https://www.ln.edu.hk/sgs/programmes-on-offer#main", text="Skip to main content", class_name=""),
        AnchorLink(
            url="https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-artificial-intelligence-and-the-future",
            text="Master of Arts in Artificial Intelligence and the Future",
            class_name="",
        ),
        AnchorLink(
            url="https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-chinese",
            text="Master of Arts in Chinese",
            class_name="",
        ),
    ]

    selected = select_detail_links(index_url=index_url, anchors=anchors, limit=4)
    assert [item.url for item in selected] == [
        "https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-artificial-intelligence-and-the-future",
        "https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-chinese",
    ]


def test_same_page_ignores_fragment_and_trailing_slash() -> None:
    base = "https://www.ln.edu.hk/sgs/programmes-on-offer"
    assert _same_page(base + "#main", base) is True
    assert _same_page(base + "/", base) is True
    assert _same_page(base + "/#main", base) is True
    assert _same_page(base + "/master-of-arts-in-chinese", base) is False


def test_select_detail_links_prefers_nested_over_sitewide_keyword_matches() -> None:
    """Regression: fixing the #main self-link (previous test) still left
    imported_count at 1 on a real Lingnan crawl — the next-highest-DOM-
    order keyword match was "Postgraduate Conference 2026", a news item
    under /sgs/news-events/..., not a programme. Confirmed live via a
    direct CDP capture (detail_limit=50) that the real programme links
    ARE present in the DOM — they just sit behind several sitewide-chrome
    matches in document order: a language switcher
    (/cht/sgs/programmes-on-offer, /chs/sgs/programmes-on-offer), a
    "for-current-students" nav link, and two news-events cards, all of
    which coincidentally contain "programme"/"postgraduate". With
    detail_limit=4 those four noise matches alone filled the cap before
    a single real link was ever reached. Nesting under the index page's
    own path is exactly the signal that tells these apart."""
    index_url = "https://www.ln.edu.hk/sgs/programmes-on-offer"
    anchors = [
        AnchorLink(url="https://www.ln.edu.hk/cht/sgs/programmes-on-offer", text="中文", class_name=""),
        AnchorLink(url="https://www.ln.edu.hk/chs/sgs/programmes-on-offer", text="简体", class_name=""),
        AnchorLink(
            url="https://www.ln.edu.hk/sgs/for-current-students/research-postgraduate-studies/course-taking",
            text="Course Taking", class_name="",
        ),
        AnchorLink(
            url="https://www.ln.edu.hk/sgs/news-events/flagship-events/postgraduate-conference2026",
            text="Postgraduate Conference 2026", class_name="",
        ),
        AnchorLink(
            url="https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-artificial-intelligence-and-the-future",
            text="Master of Arts in Artificial Intelligence and the Future", class_name="",
        ),
        AnchorLink(
            url="https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-chinese",
            text="Master of Arts in Chinese", class_name="",
        ),
    ]

    selected = select_detail_links(index_url=index_url, anchors=anchors, limit=4)
    assert [item.url for item in selected] == [
        "https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-artificial-intelligence-and-the-future",
        "https://www.ln.edu.hk/sgs/programmes-on-offer/master-of-arts-in-chinese",
    ]


def test_same_section_distinguishes_nested_from_sitewide() -> None:
    base = "https://www.ln.edu.hk/sgs/programmes-on-offer"
    assert _same_section(base, base + "/master-of-arts-in-chinese") is True
    assert _same_section(base, "https://www.ln.edu.hk/cht/sgs/programmes-on-offer") is False
    assert _same_section(base, "https://www.ln.edu.hk/sgs/news-events/flagship-events/x") is False
    assert _same_section(base, "https://other.edu/sgs/programmes-on-offer/x") is False


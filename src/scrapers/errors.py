"""Fetch errors that must end a run instead of being retried.

A host that refuses TCP connections is not a flaky page — it is telling us to
stop. When gs.cuhk.edu.hk started refusing, tenacity retried each page three
times, the fetch ladder escalated and retried again, the pipeline moved on to
the next URL and repeated it, and a re-run knocked on the index twelve times
in a minute. Each knock lengthened the ban. HostRefusedError is raised the
first time a refusal is seen and is never retried or swallowed at any layer;
the CLI shows its message instead of a traceback.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_REFUSED_MARKERS = ("ERR_CONNECTION_REFUSED", "ECONNREFUSED", "CONNECTION REFUSED")


def is_connection_refused(exc: BaseException) -> bool:
    """True when *exc* — or anything in its cause/context chain — is a refused connection."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).upper()
        if any(marker in text for marker in _REFUSED_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


class HostRefusedError(RuntimeError):
    """The host actively refused the connection; the whole run must stop now."""

    def __init__(self, url: str, detail: str | None = None) -> None:
        self.url = url
        self.host = (urlsplit(url).netloc or url).lower()
        self.detail = detail
        super().__init__(
            f"{self.host} refused the connection while fetching {url}. The site is "
            "rate-limiting or blocking this address; every further request lengthens "
            "the block. Stopped without retrying — wait (an hour at least, a day after "
            "a repeat) and run again later, ideally with --page-delay."
        )

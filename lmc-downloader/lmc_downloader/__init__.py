"""lmc-downloader: archive an XJTLU Learning Mall Core course with its structure.

The package is deliberately split so each part can be used alone:

``session``  turn a cookie file or header into a usable session
``http``     cookie-carrying GET client with retries and streaming
``crawler``  enumerate sections/activities and resolve real file URLs
``fetcher``  download into the course tree, with resume, dedupe and a manifest
``names``    Windows-safe naming shared with ``build-dir-tree``

Typical programmatic use::

    from lmc_downloader import HttpClient, CookieStore, Crawler, Fetcher
    from lmc_downloader.session import load_cookies

    client = HttpClient(CookieStore(load_cookies("cookies.txt")))
    refs = [ref for activity in outline.activities
            for ref in Crawler(client).resolve(activity)]
    fetcher = Fetcher(client, root="D:/LMC/ISA104")
    results = [fetcher.fetch(ref) for ref in refs]
    fetcher.manifest.save()
"""

from .crawler import Activity, CourseOutline, Crawler, FileRef
from .fetcher import Fetcher, FetchResult, Manifest
from .http import HttpClient, HttpError, Response, SessionExpired
from .names import choose_filename, sanitize_name, unique_path
from .session import CookieError, CookieStore, load_cookies, parse_cookie_header

__version__ = "2.0.0"

__all__ = [
    "Activity",
    "CookieError",
    "CookieStore",
    "CourseOutline",
    "Crawler",
    "FetchResult",
    "Fetcher",
    "FileRef",
    "HttpClient",
    "HttpError",
    "Manifest",
    "Response",
    "SessionExpired",
    "choose_filename",
    "load_cookies",
    "parse_cookie_header",
    "sanitize_name",
    "unique_path",
    "__version__",
]

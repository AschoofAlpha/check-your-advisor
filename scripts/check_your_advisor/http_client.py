"""
HTTP 客户端模块
===============
封装所有 HTTP 请求逻辑，统一处理：
- 指数退避重试（解决原代码 15 处 except Exception: pass）
- UA 池轮换 + 请求指纹随机化（反爬）—— **只给 PDF 下载那条路用**
- 诚实 UA（`polite_headers`）—— 给 keyless 公共 API 用，两者不共用一套头
- 可选代理 IP 池
- 统一超时与错误日志

两套请求头是有意的，见下面 `polite_headers` 的注释：出版商落地页和
OpenAlex/Crossref/DOAJ 的公开 JSON 接口不是同一种对手，把伪装的浏览器指纹发给
后者，与本包对外「不写爬虫、走的是有文档的 API」这句话直接冲突。

Standard library only. This used to be `requests` + `urllib3.Retry`, which was
the last hard third-party dependency in the package and therefore the reason a
fresh clone could not run anything. What it cost to remove, stated plainly:

- **Connection reuse is gone.** `HTTPAdapter(pool_maxsize=20)` kept sockets
  alive across the 8-source download race; `urlopen` opens a new TLS connection
  per request. That is slower and nothing else — a handshake never changes an
  answer. Rebuilding a keep-alive pool over `http.client` means reimplementing
  the hardest part of urllib3 (half-closed sockets, redirects across hosts) and
  is not worth it here.
- **Brotli is gone from Accept-Encoding.** No CPython version ships a brotli
  decoder. gzip and deflate are handled below.
- **`Connection: keep-alive` is no longer sent.** `urllib.request` sets
  `Connection: close` itself, so advertising the opposite would have been a
  header this module cannot actually honour.
- **`stream=` is gone.** It was declared and threaded through the logging, but
  no caller ever passed it.
"""

import codecs
import gzip
import logging
import random
import time
import zlib
from dataclasses import dataclass, field
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

logger = logging.getLogger("check_your_advisor.http")

# ============================================================
# UA 池：解决原代码 L289-293 固定 UA 被指纹识别的问题
# ============================================================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

# 不同域名使用不同的 Accept 头，模拟真实浏览器行为
ACCEPT_PROFILES = {
    "pdf": "application/pdf,application/octet-stream,*/*;q=0.8",
    "html": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "api": "application/json,*/*;q=0.8",
}

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# ============================================================
# 诚实 UA：给 keyless 公共 API 用，**不走上面的 UA 池**
# ============================================================
# The pool above exists for the PDF download race, where the other end is a
# publisher landing page that does not want to be read by a program. A keyless
# public JSON API is the opposite situation: OpenAlex and Crossref both document
# a "polite pool" that *requires* the caller to say who it is, and both route
# identified requests onto faster, more reliable infrastructure. Sending a
# rotating fake Chrome UA to those endpoints is not politeness-neutral — it is
# the exact behaviour their documentation asks callers not to exhibit, and it
# contradicts this package's own statement (journals.py) that its API calls are
# documented API calls rather than scraping.
#
# So API callers pass `polite_headers()` through the `extra_headers` hook on
# `get()`. Same string as `pubmed_api.USER_AGENT`, deliberately duplicated
# rather than imported: `pubmed_api` talks to NCBI through bare `urlopen` and
# does not depend on this module, and a low-level transport importing a
# high-level API client to borrow a constant would be the wrong direction.
PROJECT_USER_AGENT = "check-your-advisor/1.0"

# Headers that only a browser sends. When a caller identifies itself by name,
# these are stripped rather than left attached — `User-Agent: check-your-advisor
# /1.0` beside `Sec-Ch-Ua-Platform: "Windows"` is not an honest request, it is
# an incoherent one.
_BROWSER_ONLY_HEADERS = ("sec-", "dnt")


def polite_headers(mailto: str = "") -> dict:
    """Honest identification for a keyless public API, per its own docs.

    `mailto` is not authentication and not a key. Crossref documents
    `User-Agent: <app>/<version> (mailto:<address>)` as the way into its polite
    pool, and OpenAlex accepts the same address either in the UA or as a query
    parameter. It is included only when the user supplied one via `--email`; the
    default sends the project name alone.

    `Accept-Language` is pinned rather than randomised for the same reason the
    UA is: varying it per request is fingerprint randomisation, which is what
    this function exists to not do.
    """
    ua = f"{PROJECT_USER_AGENT} (mailto:{mailto})" if mailto else PROJECT_USER_AGENT
    return {"User-Agent": ua, "Accept-Language": "en"}


def _random_headers(accept_type: str = "pdf") -> dict:
    """生成随机化的请求头，降低指纹一致性"""
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": ACCEPT_PROFILES.get(accept_type, ACCEPT_PROFILES["pdf"]),
        "Accept-Language": random.choice([
            "en-US,en;q=0.9",
            "en-US,en;q=0.9,zh-CN;q=0.8",
            "en-GB,en;q=0.9",
        ]),
        # No `br`: there is no brotli decoder in the standard library, and
        # advertising an encoding that cannot be decoded turns a good response
        # into an unreadable one. No `Connection` either — urllib sets it.
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
    }
    # 随机添加 Sec-Fetch 头（Chrome 特有）
    if "Chrome" in ua:
        headers.update({
            "Sec-Fetch-Dest": random.choice(["document", "empty"]),
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Ch-Ua-Platform": random.choice(['"Windows"', '"macOS"', '"Linux"']),
        })
    return headers


def _decompress(body: bytes, encoding: str) -> bytes:
    """Undo Content-Encoding, which urllib — unlike requests — does not.

    The deflate branch tries both windows. RFC 7230 says `deflate` is the
    zlib-wrapped form, but a good share of servers send raw DEFLATE with no
    header, and `zlib.decompress(data, -MAX_WBITS)` — the recipe usually quoted
    for this — fails on exactly the standards-correct half. Trying the wrapped
    form first and falling back covers both.
    """
    enc = (encoding or "").strip().lower()
    if not body or enc in ("", "identity"):
        return body
    try:
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error) as e:
        logger.warning("  ⚠ Content-Encoding=%s 解压失败(%s)，按原始字节处理", enc, e)
    return body


@dataclass
class Response:
    """The three members `pdf_utils.HttpResponse` requires, plus `text` and `json()`.

    This class used to carry only the status, the headers and the body, on the
    stated grounds that "every call site reads only" those three. That was false
    when it was written: `download_sources.py` reads `.text` at four call sites
    and calls `.json()` at six more, and every one of them raised
    `AttributeError` instead of returning a document. The `.json()` sites are
    wrapped in `except ValueError`, which does not catch `AttributeError`, so the
    failure propagated out of the source function and killed the download race
    rather than falling through to the next source. Seven of the eight
    open-access sources could not complete a single fetch; only `try_pmc`'s two
    direct PDF URLs, which read `.content` through `save_pdf`, ever worked.

    Restoring the two members is the smaller repair: the callers were written
    against the `requests` response contract and are correct as they stand, so
    the defect is here, in the half-built replacement, not at the ten sites that
    assumed a whole one.
    """

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    content: bytes = b""

    @property
    def text(self) -> str:
        """The body decoded per the charset in Content-Type, UTF-8 when unstated.

        Never raises. A publisher landing page scraped for PDF links is worth
        reading through a few replacement characters, and a decode error here
        would abort a download race over one malformed byte in one candidate.
        """
        return self.content.decode(self._charset(), errors="replace")

    def json(self):
        """The body parsed as JSON, raising `ValueError` on anything else.

        `json.JSONDecodeError` subclasses `ValueError`, which is exactly what
        every caller already catches, so a non-JSON body reaches them as the
        error they were written to expect.
        """
        import json as _json

        return _json.loads(self.text)

    def _charset(self) -> str:
        """Charset from Content-Type, falling back to UTF-8.

        An unknown or misspelled charset falls back rather than raising: servers
        send `charset=utf8`, `charset="utf-8"` and outright typos, and none of
        those is a reason to lose the body.
        """
        content_type = ""
        if self.headers:
            content_type = self.headers.get("content-type", "") or self.headers.get("Content-Type", "")
        _, _, tail = content_type.partition("charset=")
        charset = tail.split(";")[0].strip().strip('"\'')
        if not charset:
            return "utf-8"
        try:
            codecs.lookup(charset)
        except LookupError:
            return "utf-8"
        return charset


class _NoRedirect(HTTPRedirectHandler):
    """Turns urllib's follow-by-default into requests' allow_redirects=False."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RobustHTTPClient:
    """
    带重试、日志的 HTTP 客户端；反爬那套只在调用方不自报身份时生效。

    替代原代码中散落的 requests.get(...) 调用，统一行为。默认走 UA 池（PDF
    下载），调用方传 `extra_headers=polite_headers()` 就改走诚实 UA（公开 API）。
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: int = 30,
        proxy_list: list[str] | None = None,
    ):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        self.proxy_list = proxy_list or []
        self._openers: dict[tuple[str, bool], object] = {}
        self._request_count = 0

    def _opener(self, proxy: str | None, allow_redirects: bool):
        """One opener per (proxy, redirect policy), built once and reused.

        `urlopen` goes through a module-global opener, so per-request proxy
        rotation cannot be expressed with it at all — each combination needs its
        own opener. Caching them keeps that from being a per-request cost.
        """
        key = (proxy or "", allow_redirects)
        if key not in self._openers:
            handlers = []
            if proxy:
                handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
            if not allow_redirects:
                handlers.append(_NoRedirect())
            self._openers[key] = build_opener(*handlers)
        return self._openers[key]

    def _get_proxy(self) -> str | None:
        """从代理池中随机选取"""
        return random.choice(self.proxy_list) if self.proxy_list else None

    def get(
        self,
        url: str,
        accept_type: str = "pdf",
        timeout: int | None = None,
        allow_redirects: bool = True,
        extra_headers: dict | None = None,
    ) -> Response | None:
        """
        发起 GET 请求，带完整的日志和错误处理。

        返回 None 表示网络层失败（超时、连不上）；HTTP 错误状态码会作为
        Response 返回而不是抛出，与原先 requests 的行为一致——调用方一律读
        `resp.status_code`，把 404 变成异常会改变每一个调用点的语义。

        `extra_headers` 里带 `User-Agent` 时，本次请求被当作「调用方自报身份」
        处理：随机 UA 被覆盖，浏览器专有的 Sec-* / DNT 一并去掉。

        谁传、谁不传，是按端点分的，不是按模块分的：`citations` / `openalex` /
        `journal_risk` 的每一处，以及 `download_sources` 里 6 处
        `accept_type="api"` 的目录查询，都传 `polite_headers()`；同一批源函数里
        取落地页和 PDF 二进制的那些请求不传，照旧走 UA 池。不传 `extra_headers`
        时行为与本参数存在之前完全一致。
        """
        headers = _random_headers(accept_type)
        if extra_headers:
            headers.update(extra_headers)
            if any(key.lower() == "user-agent" for key in extra_headers):
                for key in list(headers):
                    if key.lower().startswith(_BROWSER_ONLY_HEADERS):
                        del headers[key]

        effective_timeout = timeout or self.timeout
        proxy = self._get_proxy()
        self._request_count += 1

        domain = urlparse(url).netloc
        logger.debug(
            "GET #%d %s (timeout=%ds, proxy=%s)",
            self._request_count, domain, effective_timeout,
            "yes" if proxy else "no",
        )

        opener = self._opener(proxy, allow_redirects)
        attempt = 0
        while True:
            # 请求间加入随机延迟（0.3~1.5s），模拟人类行为
            time.sleep(random.uniform(0.3, 1.5))
            try:
                req = Request(url, headers=headers)
                with opener.open(req, timeout=effective_timeout) as raw:
                    body = _decompress(raw.read(), raw.headers.get("Content-Encoding", ""))
                    resp = Response(getattr(raw, "status", 200) or 200, raw.headers, body)
            except HTTPError as e:
                # An HTTP error status is a response, not a transport failure.
                body = b""
                try:
                    body = _decompress(e.read(), e.headers.get("Content-Encoding", "") if e.headers else "")
                except Exception:  # noqa: BLE001 - the body is optional here
                    pass
                resp = Response(e.code, e.headers or {}, body)
            except TimeoutError:
                # Must precede URLError: a socket timeout is a TimeoutError and
                # is not a URLError subclass, so the order decides the message.
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(self.backoff_factor * (2 ** (attempt - 1)))
                    continue
                logger.error("  ✗ 超时 (%ds) — %s", effective_timeout, url[:80])
                return None
            except URLError as e:
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(self.backoff_factor * (2 ** (attempt - 1)))
                    continue
                logger.error("  ✗ 连接失败 — %s: %s", domain, str(getattr(e, "reason", e))[:100])
                return None
            except Exception as e:  # noqa: BLE001 - one bad URL must not kill the run
                logger.error("  ✗ 请求异常 — %s: %s", domain, str(e)[:100])
                return None

            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                wait = self._retry_wait(resp, attempt)
                logger.debug("  ↻ %d from %s，%.1fs 后重试（第 %d 次）",
                             resp.status_code, domain, wait, attempt)
                time.sleep(wait)
                continue

            logger.debug(
                "  → %d %s (%d bytes, type=%s)",
                resp.status_code, domain, len(resp.content),
                resp.headers.get("content-type", "?")[:40],
            )

            # 403/429 特殊处理：可能是反爬触发
            if resp.status_code == 403:
                logger.warning("  ⚠ 403 Forbidden from %s — 可能触发反爬", domain)
            elif resp.status_code == 429:
                retry_after = self._retry_after(resp, 30)
                logger.warning("  ⚠ 429 Rate Limited from %s — 等待 %ds", domain, retry_after)
                time.sleep(retry_after)

            return resp

    def _retry_wait(self, resp: Response, attempt: int) -> float:
        """Retry-After when the server sent one, otherwise exponential backoff."""
        after = self._retry_after(resp, 0)
        if after:
            return min(float(after), 60.0)
        return self.backoff_factor * (2 ** (attempt - 1))

    @staticmethod
    def _retry_after(resp: Response, default: int) -> int:
        try:
            return int(resp.headers.get("Retry-After", default))
        except (TypeError, ValueError):
            return default

    @property
    def stats(self) -> dict:
        return {"total_requests": self._request_count}

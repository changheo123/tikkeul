# 공개 페이지를 안전하게 읽어 링크를 뽑는다. 로그인·CAPTCHA·접근 제한은 우회하지 않고 사유만 남긴다
import json
import re
import shutil
import socket
import threading
import ipaddress
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import urllib3.util.connection as u3conn
from lxml import html as LH

from . import forms

MAX_REDIRECTS = 5
TIMEOUT = (5, 10)  # (연결, 응답) 초
MAX_BYTES = 1_500_000  # 페이지 하나에서 읽는 최대 크기
MIN_TEXT = 300  # 본문이 이보다 짧으면 스크립트로 그리는 페이지로 본다

LOGIN_URL = re.compile(r"accounts\.google\.com|nid\.naver\.com|/(?:login|signin|sign_in|member/login)(?:[/?.]|$)", re.I)
SHARE = re.compile(r"sharer|intent/tweet|share\.naver|story\.kakao|kakao\.com/link|addthis|/ad_click|doubleclick|googleadservices|play\.google\.com/store|apps\.apple\.com", re.I)
ONCLICK_URL = re.compile(r"https?://[^\s'\"\\)]+")
_tls = threading.local()
_orig_connect = u3conn.create_connection


class BlockedAddress(OSError):
    """공개되지 않은 IP(내부망·루프백·메타데이터 등)로 연결하려 함."""


def _safe_connect(address, *args, **kw):
    """연결하는 순간 주소를 직접 풀어 공개 IP인지 확인하고 그 IP로 접속한다(DNS rebinding 방어)."""
    if not getattr(_tls, "on", False):
        return _orig_connect(address, *args, **kw)
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = [i[4][0] for i in infos]
    if not ips or not all(ipaddress.ip_address(x.split("%")[0]).is_global for x in ips):
        raise BlockedAddress(f"공개되지 않은 주소: {host}")
    err = None
    for ip in dict.fromkeys(ips):
        try:
            return _orig_connect((ip, port), *args, **kw)
        except OSError as e:
            err = e
    raise err


u3conn.create_connection = _safe_connect


@contextmanager
def guarded():
    """이 안의 HTTP 연결은 공개 IP로만 나간다."""
    prev = getattr(_tls, "on", False)
    _tls.on = True
    try:
        yield
    finally:
        _tls.on = prev


@dataclass
class Page:
    url: str
    final: str = ""
    status: str = "error"  # ok·robots·login·forbidden·not_found·blocked·non_html·too_many·error
    title: str = ""
    text: str = ""
    html: str = ""
    links: list = field(default_factory=list)  # [(주소, 표시 글자, 출처 종류)]
    rendered: bool = False


def _decode(raw, ctype):
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I) or re.search(rb"charset=[\"']?([\w\-]+)", raw[:3000], re.I)
    names = [m.group(1).decode() if m and isinstance(m.group(1), bytes) else (m.group(1) if m else None), "utf-8", "cp949"]
    for n in names:
        if not n:
            continue
        try:
            return raw.decode(n)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def parse(page, source):
    """HTML에서 제목·본문·링크(href, data-*, onclick, iframe, JSON-LD, 메타)를 뽑는다. 메타 주소는 후보로만 쓴다."""
    page.html = source
    try:
        d = LH.fromstring(source)
    except Exception:
        page.status = "error"
        return page
    base = page.final or page.url
    t = d.xpath("//title/text()")
    page.title = re.sub(r"\s+", " ", t[0]).strip() if t else ""
    links = []
    metas = []
    for name in ("og:title", "og:description", "description"):
        metas += d.xpath(f"//meta[@property='{name}' or @name='{name}']/@content")
    for name in ("og:url",):
        links += [(urljoin(base, x), "", "meta") for x in d.xpath(f"//meta[@property='{name}']/@content")]
    links += [(urljoin(base, x), "", "meta") for x in d.xpath("//link[@rel='canonical']/@href")]
    for s in d.xpath("//script[@type='application/ld+json']/text()"):
        try:
            links += [(u, "", "jsonld") for u in ONCLICK_URL.findall(json.dumps(json.loads(s), ensure_ascii=False))]
        except ValueError:
            continue
    for bad in d.xpath("//script|//style|//noscript|//nav|//header|//footer|//aside"):
        bad.drop_tree()
    label = lambda a: re.sub(r"\s+", " ", a.text_content() or "").strip()[:60]
    for a in d.xpath("//a[@href]"):
        links.append((urljoin(base, a.get("href")), label(a), "a"))
    for el in d.xpath("//*[@data-url or @data-href or @data-link]"):
        for k in ("data-url", "data-href", "data-link"):
            if el.get(k):
                links.append((urljoin(base, el.get(k)), label(el), "data"))
    for el in d.xpath("//*[@onclick]"):
        links += [(u, label(el), "onclick") for u in ONCLICK_URL.findall(el.get("onclick"))]
    for f in d.xpath("//iframe[@src]"):
        links.append((urljoin(base, f.get("src")), "", "iframe"))
    seen, out = set(), []
    for u, lab, kind in links:
        u = u.split("#")[0]
        if u.startswith("http") and not SHARE.search(u) and u not in seen:
            seen.add(u)
            out.append((u, lab, kind))
    page.links = out
    body = d.xpath("//body")
    text = body[0].text_content() if body else d.text_content()
    page.text = re.sub(r"[ \t\r\f\v]+", " ", " \n".join([page.title, *metas, text]))
    page.text = "\n".join(x.strip() for x in page.text.splitlines() if x.strip())
    return page


def fetch_page(f, url):
    """리다이렉트를 한 번씩 검사하며 따라가 공개 페이지를 읽는다. 실패는 사유로 돌려준다(재시도·우회 없음)."""
    page = Page(url=url, final=url)
    cur = url
    with guarded():
        for _ in range(MAX_REDIRECTS + 1):
            page.final = cur
            if LOGIN_URL.search(cur):
                page.status = "login"
                return page
            if not forms.is_safe_url(cur):
                page.status = "blocked"
                return page
            if not f.allowed(cur):
                page.status = "robots"
                return page
            try:
                r = f.get(cur, check_robots=False, allow_redirects=False, stream=True, timeout=TIMEOUT)
            except BlockedAddress:
                page.status = "blocked"
                return page
            except Exception as e:
                page.status = "blocked" if "공개되지 않은 주소" in str(e) else "error"
                return page
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                cur = urljoin(cur, r.headers["Location"])
                r.close()
                continue
            if r.status_code in (401, 403, 451):
                page.status = "forbidden"
                r.close()
                return page
            if r.status_code in (404, 410):
                page.status = "not_found"
                r.close()
                return page
            if r.status_code >= 400:
                page.status = "error"
                r.close()
                return page
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                page.status = "non_html"
                r.close()
                return page
            raw, size = [], 0
            try:
                for chunk in r.iter_content(65536):
                    raw.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BYTES:
                        break
            finally:
                r.close()
            page.status = "ok"
            return parse(page, _decode(b"".join(raw), ctype))
    page.status = "too_many"
    return page


def looks_dynamic(page):
    return page.status == "ok" and len(page.text) < MIN_TEXT


class Renderer:
    """정적 분석으로 부족한 공개 페이지만 Chrome으로 그린다. 버튼은 누르지 않고 만들어진 링크만 읽는다."""

    def __init__(self, f, limit=25, cap_sec=150, url_ok=None):
        self.f, self.limit, self.cap, self.count = f, limit, cap_sec, 0
        self.t0 = time.time()
        self.pw = self.browser = None
        self.url_ok = url_ok or forms.is_safe_url
        self.unavailable = False

    def _start(self):
        if self.browser or self.unavailable:
            return
        chrome = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
        for p in ("C:/Program Files/Google/Chrome/Application/chrome.exe",):
            if not chrome and os.path.exists(p):
                chrome = p
        try:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(headless=True, executable_path=chrome, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        except Exception:
            self.unavailable = True

    def render(self, url):
        """그린 결과의 Page. 예산 초과·브라우저 없음·robots 차단이면 None."""
        if self.count >= self.limit or time.time() - self.t0 > self.cap or not self.f.allowed(url):
            return None
        self._start()
        if self.unavailable:
            return None
        self.count += 1
        ctx = None
        try:
            ctx = self.browser.new_context(user_agent="Mozilla/5.0 (compatible; tikkeul-bot/1.0)", locale="ko-KR", accept_downloads=False)
            ok = {}

            def route(r):
                # 내부 주소로 가는 모든 요청과 무거운 자원을 막는다
                host = urlparse(r.request.url).scheme + "://" + urlparse(r.request.url).netloc + "/"
                if host not in ok:
                    ok[host] = self.url_ok(r.request.url)
                if not ok[host] or r.request.resource_type in ("image", "media", "font"):
                    return r.abort()
                return r.continue_()

            ctx.route("**/*", route)
            pg = ctx.new_page()
            pg.set_default_timeout(12000)
            resp = pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_timeout(1500)
            page = Page(url=url, final=pg.url, rendered=True)
            if LOGIN_URL.search(pg.url):
                page.status = "login"
                return page
            code = resp.status if resp else 0
            page.status = "ok" if code < 400 else ("not_found" if code in (404, 410) else "forbidden" if code in (401, 403) else "error")
            if page.status != "ok":
                return page
            return parse(page, pg.content())
        except Exception:
            return None
        finally:
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass

    def close(self):
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

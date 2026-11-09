# 수집기 공통 도구: robots.txt 준수, 호스트별 요청 간격, 재시도, 텍스트·날짜 처리
import re
import time
import datetime as dt
import urllib.robotparser
from urllib.parse import urlparse, urljoin

import requests
from lxml import html as LH

UA = "Mozilla/5.0 (compatible; tteum-events-bot/1.0)"
KST = dt.timezone(dt.timedelta(hours=9))


def now_kst():
    return dt.datetime.now(KST)


def today_kst():
    return now_kst().date()


class Fetcher:
    """robots.txt를 확인하고 같은 호스트에는 최소 간격을 두고 요청한다."""

    def __init__(self, min_interval=1.0, timeout=20, retries=2):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = {}
        self._robots = {}
        self.count = 0

    def allowed(self, url):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                # robots.txt가 없거나(4xx) 정상 텍스트가 아니면 제한 없음으로 본다
                if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                    rp.parse(r.text.splitlines())
                else:
                    rp.parse([])
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return rp.can_fetch(UA, url)

    def _wait(self, host):
        gap = time.time() - self._last.get(host, 0)
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last[host] = time.time()

    def get(self, url, check_robots=True, **kw):
        """성공 시 Response, robots 차단이면 PermissionError, 재시도 후 실패면 예외."""
        if check_robots and not self.allowed(url):
            raise PermissionError(f"robots.txt 차단: {url}")
        host = urlparse(url).netloc
        err = None
        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                self.count += 1
                kw.setdefault("timeout", self.timeout)
                r = self.s.get(url, **kw)
                if r.status_code >= 500 or r.status_code == 429:
                    err = RuntimeError(f"HTTP {r.status_code}: {url}")
                    time.sleep(2 * (attempt + 1))
                    continue
                return r
            except requests.RequestException as e:
                err = e
                time.sleep(2 * (attempt + 1))
        raise err

    def doc(self, url, encoding=None):
        r = self.get(url)
        r.raise_for_status()
        if encoding:
            r.encoding = encoding
        elif not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        d = LH.fromstring(r.text)
        d.make_links_absolute(url)
        return d


def text_of(node):
    """노드의 보이는 텍스트(스크립트·스타일 제외), 줄 단위 정리."""
    if node is None:
        return ""
    node = LH.fromstring(LH.tostring(node, encoding="unicode"))
    for bad in node.xpath(".//script|.//style"):
        bad.drop_tree()
    for el in node.xpath(".//br|.//p|.//li|.//div|.//tr|.//td|.//th|.//dd|.//dt|.//h1|.//h2|.//h3|.//h4|.//span"):
        el.tail = "\n" + (el.tail or "")
    t = node.text_content()
    lines = [re.sub(r"[ \t ]+", " ", x).strip() for x in t.splitlines()]
    return "\n".join(x for x in lines if x)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---- 날짜 ----
_DATE = re.compile(r"(?:(20\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)\s*일?")


def _mk(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


def find_dates(text, ref_year=None):
    """문자열에서 날짜를 순서대로 뽑는다. 연도가 없으면 앞 날짜의 연도(없으면 기준 연도)를 쓴다."""
    ref_year = ref_year or today_kst().year
    out, last_y = [], None
    for m in _DATE.finditer(text or ""):
        y, mo, d = m.group(1), m.group(2), m.group(3)
        if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
            continue
        yy = int(y) if y else (last_y or ref_year)
        v = _mk(yy, mo, d)
        if v:
            # 연도 없이 '1/5'처럼 앞 날짜보다 이르면 다음 해로 넘어간 것으로 본다
            if not y and out and v < out[-1]:
                v = _mk(yy + 1, mo, d) or v
            out.append(v)
            last_y = v.year
    return out


def period(text, ref_year=None):
    """'A ~ B' 형태의 응모 기간. 확인되지 않으면 (None, None)."""
    for line in (text or "").splitlines() if "\n" in (text or "") else [text or ""]:
        m = re.search(r"([^~∼]*\d{1,2}\s*[.\-/월]\s*\d{1,2}[^~∼]{0,14})[~∼]([^~∼]{0,40})", line)
        if not m:
            continue
        a = find_dates(m.group(1), ref_year)
        b = find_dates(m.group(2), a[-1].year if a else ref_year)
        if a and b:
            s, e = a[-1], b[0]
            if e < s and e.year == s.year and not re.search(r"20\d{2}", m.group(2)):
                e = _mk(e.year + 1, e.month, e.day) or e
            if e >= s:
                return s, e
    return None, None


def iso(d):
    return d.isoformat() if d else None


def norm_date(v):
    """여러 날짜 표기를 YYYY-MM-DD로. 해석할 수 없으면 None(임의로 추측하지 않음)."""
    if v is None or v == "":
        return None
    if isinstance(v, dt.date):
        return v.isoformat()
    s = str(v).strip()
    try:
        return dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    m = re.match(r"^(20\d{2}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", s)  # 연도가 있어야 확정한다
    if m:
        y = int(m.group(1))
        d = _mk(y if y > 99 else 2000 + y, m.group(2), m.group(3))
        return d.isoformat() if d else None
    return None


def absurl(base, href):
    return urljoin(base, href) if href else None

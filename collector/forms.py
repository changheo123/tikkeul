# 실제 응모 폼 링크 추출. 텍스트·HTML에서 후보를 찾고, 단축 링크는 이동 주소를 확인해 폼일 때만 확정한다
import html as htmllib
import ipaddress
import re
import socket
from urllib.parse import unquote, urlparse

# 앞에 있는 것일수록 우선. 긴 주소 폼(direct)을 단축 링크(short)보다 먼저 쓴다
_B = r"(?<![\w.\-])(?<!\w/)(?:https?:)?(?://)?(?:www\.)?"
DIRECT = [
    ("naver", re.compile(_B + r"((?:m\.)?form\.naver\.com/response/[\w\-]+)", re.I | re.A)),
    ("google", re.compile(_B + r"(docs\.google\.com/forms(?:/u/\d+)?/d/(?:e/)?[\w\-]+)(/[A-Za-z]+)?", re.I | re.A)),
    ("moaform", re.compile(_B + r"((?:[a-z0-9\-]+\.)?moaform\.com/q/[\w\-]+)", re.I | re.A)),
    ("smore", re.compile(_B + r"((?:[a-z0-9\-]+\.)?smore\.im/form/[\w\-]+)", re.I | re.A)),
]
SHORT = [
    ("gle", re.compile(_B + r"(forms\.gle/[\w\-]+)", re.I | re.A)),
    ("naverme", re.compile(_B + r"(naver\.me/[\w\-]+)", re.I | re.A)),
    ("urlkr", re.compile(_B + r"(url\.kr/[\w\-]+)", re.I | re.A)),
    ("bitly", re.compile(_B + r"(bit\.ly/[\w\-]+)", re.I | re.A)),
    ("me2do", re.compile(_B + r"(me2\.do/[\w\-]+)", re.I | re.A)),
    ("hangl", re.compile(_B + r"(han\.gl/[\w\-]+)", re.I | re.A)),
]
MAX_REDIRECTS = 5  # 단축 링크 추적 횟수 제한
SHORT_TIMEOUT = 8  # 한 번의 요청 제한 시간(초)
SHORT_HOST = re.compile(r"^https?://(?:www\.)?(?:forms\.gle|naver\.me|url\.kr|bit\.ly|me2\.do|han\.gl)/", re.I)
# 구글 폼에서 응답 화면으로 인정하는 경로(/edit 같은 편집 화면은 참여자가 열 수 없다)
GOOGLE_OK = {None, "/viewform", "/formResponse", "/closedform"}


def _scan(patterns, text):
    """[(위치, 주소)]: 프로토콜을 붙이고 구글 폼의 편집 화면 주소는 뺀다."""
    found = []
    for name, rx in patterns:
        for m in rx.finditer(text):
            if name == "google":
                suffix = m.group(2)
                if suffix not in GOOGLE_OK or suffix == "/closedform":
                    continue
                found.append((m.start(), "https://" + m.group(1) + (suffix or "")))
            else:
                found.append((m.start(), "https://" + m.group(1)))
    return found


def candidates(text, links=()):
    """(직접 폼 목록, 단축 링크 목록). HTML 엔티티·URL 인코딩을 풀어 href와 일반 텍스트를 함께 검사하고 중복을 없앤다."""
    src = htmllib.unescape(" ".join([text or "", *[x for x in links if x]]))
    src = src + " " + unquote(src)
    direct = []
    for name, rx in DIRECT:  # 폼 종류 우선순위 → 글에 나온 순서
        direct += [u for _, u in sorted(_scan([(name, rx)], src))]
    short = [u for _, u in sorted(_scan(SHORT, src))]
    return list(dict.fromkeys(direct)), list(dict.fromkeys(short))


def is_form(url):
    """이 주소가 직접 폼 주소이면 정리한 주소를 돌려준다."""
    d, _ = candidates(url or "")
    return d[0] if d else None


def _ips(host):
    """호스트가 가리키는 IP 목록."""
    return [i[4][0] for i in socket.getaddrinfo(host, None)]


def is_safe_url(url):
    """외부 공개 주소만 허용한다: http(s), 표준 포트, 계정 정보 없음, 사설·루프백·링크로컬 IP 금지."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or p.username or p.password or not p.hostname:
        return False
    try:
        if p.port not in (None, 80, 443):
            return False
        addrs = _ips(p.hostname)
    except (ValueError, OSError):
        return False
    return bool(addrs) and all(ipaddress.ip_address(x.split("%")[0]).is_global for x in addrs)


def _follow(f, url):
    """리다이렉트를 한 번씩 안전 검사하며 따라간다(목적지 본문은 받지 않음). (마지막 주소, 상태)
    상태: 'form' 폼 도착, 'not_form' 다른 곳·응답 끝, 'blocked' 안전하지 않은 주소, 'error' 오류"""
    cur = url
    for _ in range(MAX_REDIRECTS):
        if is_form(cur):
            return cur, "form"
        if not is_safe_url(cur):
            return cur, "blocked"
        r = f.get(cur, allow_redirects=False, timeout=SHORT_TIMEOUT)
        loc = r.headers.get("Location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            return cur, ("not_form" if r.status_code < 400 else "error")
        cur = requests_join(cur, loc)
        if not is_form(cur) and not SHORT_HOST.match(cur):
            return cur, "not_form"  # 폼도 단축 주소도 아닌 곳으로 이동
    return cur, ("form" if is_form(cur) else "error")  # 횟수 제한 초과


def resolve_short(f, url, cache, today, retry_days=3):
    """단축 링크가 실제 폼으로 이어지면 그 폼 주소, 아니면 None.
    결과: (폼 주소 또는 None, 'form'|'not_form'|'unverified')"""
    c = cache.get(url)
    if c and (c["status"] != "error" or (_days(today, c["at"]) < retry_days)):
        if c["status"] == "form":
            return c["final"], "form"
        return None, ("unverified" if c["status"] == "error" else "not_form")
    try:
        cur, status = _follow(f, url)
    except Exception:
        cur, status = url, "error"
    final = is_form(cur) if status == "form" else None
    cache[url] = {"final": final, "status": status, "at": today, "to": cur[:200]}
    if final:
        return final, "form"
    return None, ("unverified" if status == "error" else "not_form")


SHORTENERS = re.compile(r"^https?://(?:www\.)?(?:is\.gd|tinyurl\.com|vo\.la|bit\.ly|han\.gl|url\.kr|me2\.do|buly\.kr|c11\.kr|t\.ly|abit\.ly|shorturl\.at)/", re.I)


def expand(f, url, cache, today):
    """일반 단축 주소를 이동 주소(Location)만 읽어 실제 주소로 푼다. 단축 주소가 아니거나 못 풀면 그대로 돌려준다."""
    if not url or not SHORTENERS.match(url):
        return url
    if url in cache:
        return cache[url]
    cur = url
    try:
        for _ in range(MAX_REDIRECTS):
            if not is_safe_url(cur):
                return url  # 안전하지 않은 주소로는 요청하지 않는다
            r = f.get(cur, allow_redirects=False, timeout=SHORT_TIMEOUT)
            loc = r.headers.get("Location")
            if r.status_code not in (301, 302, 303, 307, 308) or not loc:
                break
            cur = requests_join(cur, loc)
            if not SHORTENERS.match(cur):
                break
    except Exception:
        return url  # 다음 실행에서 다시 시도(결과를 캐시하지 않음)
    cache[url] = cur
    return cur


def requests_join(base, loc):
    from urllib.parse import urljoin
    return urljoin(base, loc)


def _days(today, at):
    import datetime as dt
    return (dt.date.fromisoformat(today) - dt.date.fromisoformat(at)).days


def pick(text, links, resolver=None):
    """대표 응모 폼을 고른다. 결과: (form_url, 검증 안 된 단축 링크 목록)"""
    direct, short = candidates(text, links)
    if direct:
        return direct[0], []
    pending = []
    for u in short:
        if resolver is None:
            pending.append(u)
            continue
        final, st = resolver(u)
        if final:
            return final, []
        if st == "unverified":
            pending.append(u)
    return None, pending

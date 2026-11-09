# 실제 응모 폼 링크 추출. 텍스트·HTML에서 후보를 찾고, 단축 링크는 이동 주소를 확인해 폼일 때만 확정한다
import html as htmllib
import re
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
]
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


def resolve_short(f, url, cache, today, retry_days=3):
    """단축 링크의 이동 주소만 따라가(목적지 페이지 내용은 받지 않음) 폼이면 그 주소, 아니면 None.
    결과: (폼 주소 또는 None, 'form'|'not_form'|'unverified')"""
    c = cache.get(url)
    if c and (c["status"] != "error" or (_days(today, c["at"]) < retry_days)):
        if c["status"] == "form":
            return c["final"], "form"
        return None, ("not_form" if c["status"] == "not_form" else "unverified")
    cur, status, final = url, "error", None
    try:
        for _ in range(4):
            r = f.get(cur, allow_redirects=False)
            loc = r.headers.get("Location")
            if r.status_code not in (301, 302, 303, 307, 308) or not loc:
                status = "not_form" if r.status_code < 400 else "error"
                break
            cur = requests_join(cur, loc)
            final = is_form(cur)
            if final:
                status = "form"
                break
            host = urlparse(cur).netloc.lower()
            if "naver.me" not in host and "forms.gle" not in host:
                status = "not_form"  # 폼이 아닌 다른 사이트로 이동
                break
    except Exception:
        status = "error"
    cache[url] = {"final": final, "status": status, "at": today, "to": cur[:200]}
    if status == "form":
        return final, "form"
    return None, ("not_form" if status == "not_form" else "unverified")


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

# 응모 폼·유튜브가 바로 열리는 출처
#  - 유벤트(yuvent.co.kr): 크리에이터 영상 이벤트. 공개 API, robots.txt 허용. 영상 주소가 곧 참여 주소
#  - 공공이벤트 레이더(gonggongevent.blogspot.com): 공공기관 이벤트 글. 피드 허용, 본문의 폼·유튜브·공식 글 주소를 쓴다
import datetime as dt
import re

from lxml import html as LH

from ..common import KST, find_dates, period, text_of, today_kst
from .. import forms
from ..extract import build_event

YV = "https://yuvent.co.kr/api/events"
GG = "https://gonggongevent.blogspot.com/feeds/posts/default"
_SKIP_HOST = re.compile(r"blogspot\.com|blogger\.com|schema\.org|gstatic\.com|googleusercontent\.com|fonts\.google|draft\.blogger", re.I)
_YT = re.compile(r"youtube\.com/(?:watch|shorts|post/)|youtu\.be/", re.I)
_IG = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel)/", re.I)
_OFFICIAL = re.compile(r"\.go\.kr|\.or\.kr", re.I)
_DEADLINE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일(?:\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?)?")
_GENERIC_CAT = re.compile(r"이벤트|마감|설문|퀴즈|댓글|안내|정보|경품|인스타|유튜브|YouTube|SNS|가이드")
_PRIZE = re.compile(r"명|권|상품|커피|기프티콘|쿠폰|포인트")
_GIVE = re.compile(r"경품|추첨|증정|기프티콘|나눔|선물|상품권")


def _day(value):
    """ISO 시각을 한국 날짜로. 연도가 적힌 값만 받는다."""
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(KST).date()


def _anchor(month, day, pub):
    """마감의 월·일을 게시일 해에 붙인다. 게시일보다 훨씬 이르면 다음 해다."""
    try:
        d = dt.date(pub.year, month, day)
    except ValueError:
        return None
    if d < pub - dt.timedelta(days=7):
        try:
            d = dt.date(pub.year + 1, month, day)
        except ValueError:
            return None
    return d


def _remember_form(ev, text, links):
    direct, short = forms.candidates(text or "", links)
    if direct:
        ev["form_url"] = direct[0]
        for u in direct:
            if u not in ev.setdefault("formLinks", []):
                ev["formLinks"].append(u)
    for u in short:
        if u not in ev.setdefault("formLinks", []):
            ev["formLinks"].append(u)
    return ev


def _choose(links, direct):
    """바로 열리는 주소 우선순위: 유튜브·인스타 게시물·공식 사이트·그 외·폼."""
    yt = [u for u in links if _YT.search(u)]
    ig = [u for u in links if _IG.search(u)]
    official = [u for u in links if _OFFICIAL.search(u)]
    other = [u for u in links if u not in yt + ig + official and "instagram.com" not in u and "facebook.com" not in u]
    pool = yt or ig or official or other or direct
    return pool[0] if pool else None


# ---- 유벤트 ----
def _yuvent_item(it):
    title = it.get("title") or ""
    desc = (it.get("description") or "")[:4000]
    vid = it.get("youtubeVideoId")
    url = it.get("videoUrl") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
    if not _YT.search(url):
        return None, "영상 주소 없음"
    pub = _day(it.get("publishedAt"))
    start, end = _day(it.get("startDate")), _day(it.get("endDate"))
    ref = pub.year if pub else None
    if end is None:
        s, e = period(desc, ref)
        start, end = start or s, e
    if end is None:
        for line in desc.splitlines() or [desc]:
            if re.search(r"까지|마감", line):
                ds = find_dates(line, ref)
                if ds:
                    end = _anchor(ds[-1].month, ds[-1].day, pub) if pub else ds[-1]
                    break
    today = today_kst()
    if end and end < today:
        return None, "마감"
    blob = title + "\n" + desc
    if not (_GIVE.search(blob) or end or forms.candidates(desc)[0]):
        return None, "경품·마감·폼 없음"
    org = (it.get("channel") or {}).get("name")
    ev, why = build_event(
        src="yv", sid=it.get("id") or url, title=title, url=url, org=org, body=desc,
        start=start, end=end, plat_hint="yt", extra_tags=["유튜브 참여"])
    if not ev:
        return None, why
    return _remember_form(ev, desc, []), None


def yuvent(f, state, cfg):
    """최신 진행 이벤트만 몇 쪽. 설명에 적힌 폼은 그대로 저장하고, 마감 연도는 게시일을 기준으로 한다."""
    events, skipped, seen, listed = [], {}, [], 0
    size = cfg.get("page_size", 20)
    for page in range(1, cfg.get("max_pages", 3) + 1):
        r = f.get(YV, params={"page": page, "limit": size, "status": "ACTIVE", "sort": "publishedAt"})
        r.raise_for_status()
        data = r.json()
        items = data.get("data") or []
        if not items:
            break
        for it in items:
            listed += 1
            ev, why = _yuvent_item(it)
            if ev:
                events.append(ev)
                seen.append(ev["id"])
            else:
                skipped[str(it.get("id") or listed)] = why
        if page >= (data.get("meta") or {}).get("totalPages", page):
            break
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


# ---- 공공이벤트 블로그 ----
def _html_bits(fragment):
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment or "")
    try:
        d = LH.fromstring("<div>" + fragment + "</div>")
    except Exception:
        return [], ""
    links = []
    for h in d.xpath(".//a/@href"):
        h = (h or "").split("#")[0].strip()
        if h.startswith("http") and not _SKIP_HOST.search(h):
            links.append(h)
    return list(dict.fromkeys(links)), text_of(d)[:4000]


def _gg_item(entry):
    title = (entry.get("title") or {}).get("$t") or ""
    if "마감" not in title:
        return None, "마감이 없는 안내 글"
    cats = [c.get("term") or "" for c in entry.get("category") or []]
    if "마감된 이벤트" in cats:
        return None, "종료·안내 글"
    pub = _day((entry.get("published") or {}).get("$t"))
    parts = [p.strip() for p in re.split(r"\s*[|｜]\s*", title) if p.strip()]
    deadline = next((p for p in parts if "마감" in p), "")
    m = _DEADLINE.search(deadline)
    if not m or not pub:
        return None, "마감일 없음"
    end = _anchor(int(m.group(1)), int(m.group(2)), pub)
    if not end:
        return None, "마감일 해석 불가"
    if end < today_kst():
        return None, "마감"
    name = next((p for p in parts if "마감" not in p and "이벤트" in p), None)
    if name is None:
        name = next((p for p in parts if "마감" not in p), title)
    prize = parts[-1] if len(parts) >= 3 and parts[-1] != name and _PRIZE.search(parts[-1]) else None
    org = next((c for c in cats if len(c) >= 2 and not _GENERIC_CAT.search(c)), None)
    links, text = _html_bits((entry.get("content") or {}).get("$t") or "")
    post = next((l.get("href") for l in entry.get("link") or [] if l.get("rel") == "alternate"), None)
    direct, short = forms.candidates(text, links)
    url = _choose(links, direct)
    if not url:
        return None, "바로 여는 주소 없음"
    hour, minute = m.group(3), m.group(4)
    ev, why = build_event(
        src="gg", sid=post or title, title=name, url=url, org=org, body=text, end=end,
        prize=prize, list_url=post, links=direct + short + links[:6])
    if not ev:
        return None, why
    if hour:
        ev["endTime"] = f"{int(hour):02d}:{int(minute or 0):02d}"
    return _remember_form(ev, text, links + short), None


def gonggongevent(f, state, cfg):
    """최신 글의 피드. 제목의 'N월 N일 마감'은 글 게시일 해에 붙이고, 이미 지난 글은 진행 중으로 넣지 않는다."""
    events, skipped, seen, listed = [], {}, [], 0
    size = cfg.get("page_size", 20)
    for page in range(cfg.get("max_pages", 3)):
        start = 1 + page * size
        r = f.get(GG, params={"alt": "json", "max-results": size, "start-index": start})
        r.raise_for_status()
        entries = (r.json().get("feed") or {}).get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        if not entries:
            break
        fresh = 0
        for entry in entries:
            listed += 1
            ev, why = _gg_item(entry)
            fresh += ev is not None or why == "마감"
            if ev:
                events.append(ev)
                seen.append(ev["id"])
            else:
                skipped[(entry.get("title") or {}).get("$t", "")[:40] or str(listed)] = why
        # 이 쪽이 전부 오래된 글이면 더 뒤는 보지 않는다
        pubs = [_day((e.get("published") or {}).get("$t")) for e in entries]
        pubs = [d for d in pubs if d]
        if pubs and max(pubs) < today_kst() - dt.timedelta(days=50):
            break
        if not fresh and page > 0:
            break
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}

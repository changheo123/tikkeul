# events.kr(이벤트 코리아): 경품 이벤트 모음 사이트. robots.txt에서 목록·상세 수집 허용
import re

from ..common import text_of, find_dates, period
from ..extract import build_event

BASE = "https://events.kr/"
# events.kr 응모형태 → (사이트 종류, 조건 많음 사유)
FORM = {
    "작문/댓글": ("댓글", None), "퀴즈응모": ("퀴즈", None), "설문/투표": ("설문", None),
    "체험단/테스터": ("체험단", None), "단순응모": ("기타", None), "인증샷": ("기타", None),
    "사전신청": ("기타", None), "공모전": ("기타", "공모전"), "공유/초대": ("기타", "공유·게시형"),
    "소문내기": ("기타", "공유·게시형"), "회원가입": ("기타", "신규 회원가입 필요"),
}
PLATS = {"인스타그램": "insta", "유튜브": "yt", "블로그": "naver", "카페": "naver", "X": "x", "트위터": "x",
         "텔레그램": "tg", "쇼핑몰": "shop", "쿠팡": "shop", "홈페이지": "web", "앱": "web", "카카오": "web"}


def list_ids(f, max_pages):
    ids = []
    for page in range(1, max_pages + 1):
        d = f.doc(f"{BASE}?page={page}")
        found = [int(m) for m in re.findall(r"event\.php\?id=(\d+)", " ".join(d.xpath("//a/@href")))]
        new = [i for i in dict.fromkeys(found) if i not in ids]
        if not new:
            break
        ids += new
    return ids


def parse(f, eid):
    url = f"{BASE}event.php?id={eid}"
    d = f.doc(url)
    h1 = d.xpath("//h1")
    if not h1:
        return None, "제목 없음"
    raw_title = h1[0].text_content().strip()
    lines = text_of(d.xpath("//body")[0]).splitlines()
    try:
        i = lines.index(raw_title, 2)  # 상단 <title> 다음에 나오는 본문 제목
    except ValueError:
        i = 0
    head = lines[i + 1:i + 8]
    start = end = None
    plat = form = None
    for ln in head:
        if start is None and "~" in ln:
            start, end = period(ln)
        if ln in PLATS and not plat:
            plat = PLATS[ln]
        if ln in FORM and not form:
            form = ln
    org = None
    if "주최" in lines:
        k = lines.index("주최")
        org = lines[k + 1] if k + 1 < len(lines) else None
    try:
        a, b = lines.index("이벤트 소개"), lines.index("태그")
        body = "\n".join(lines[a + 1:b])
    except ValueError:
        body = "\n".join(lines[i + 1:i + 40])
    # 주최 원문(참여 링크): 본문에 있는 외부 링크 중 첫 번째
    links = [h for h in d.xpath("//h1/following::a/@href")
             if h.startswith("http") and not re.search(r"events\.kr|webworks|ad_click|kakao\.com/link|facebook\.com/sharer", h)]
    orig = links[0] if links else None
    if not org:
        m = re.match(r"\[([^\]]+)\]", raw_title)
        org = m.group(1) if m else None
    title = re.sub(r"^\[[^\]]+\]\s*", "", raw_title)
    title = re.sub(r"\s*\(~\s*\d{1,2}/\d{1,2}\)\s*$", "", title)
    kind, reason = FORM.get(form, (None, None))
    if start is None:
        start, end = period(body)
    ev, why = build_event(
        src="ek", sid=str(eid), title=title, url=orig or url, org=org, body=body, start=start, end=end,
        kind_hint=kind, extra_reasons=[reason] if reason else [], list_url=url, plat_hint=plat, links=links)
    if ev and not orig:
        ev["tags"].append("원문 미확인")
    return ev, why


def collect(f, state, cfg):
    st = state.setdefault("events_kr", {"parsed": {}})
    ids = list_ids(f, cfg.get("max_pages", 5))
    events, skipped, budget = [], {}, cfg.get("max_details", 40)
    for eid in ids:
        key = str(eid)
        if key in st["parsed"]:
            continue  # 이미 읽은 글은 다시 받지 않는다(요청 수 절약)
        if budget <= 0:
            break
        budget -= 1
        try:
            ev, why = parse(f, eid)
        except Exception as e:  # 글 하나가 실패해도 나머지는 계속
            skipped[key] = f"오류: {e}"[:120]
            continue
        st["parsed"][key] = ev["id"] if ev else ("skip:" + (why or ""))
        if ev:
            events.append(ev)
        else:
            skipped[key] = why
    seen = [st["parsed"][str(i)] for i in ids if str(i) in st["parsed"] and not str(st["parsed"][str(i)]).startswith("skip:")]
    return {"events": events, "seen": seen, "listed": len(ids), "skipped": skipped}

# 공모전 포털의 이벤트 게시판: 콘테스트코리아(기업주최 이벤트), 위비티(이벤트)
# 두 곳 모두 robots.txt 허용, 약관은 '상업적 이용' 금지 → 본문은 옮기지 않고 제목·주최·기간·경품·링크만 쓴다
# 상세 페이지는 한 번 읽으면 캐시해 다시 받지 않는다
import re

from .. import forms
from ..common import text_of, find_dates, period, clean, today_kst
from ..extract import build_event

CK = "https://www.contestkorea.com/boad/bd_event/"
CK_LIST = CK + "egolist.php?bd=11&int_item1=0&int_ing=1&pg={}"


def _ck_detail(f, st, seq, title, start, end, cache):
    url = f"{CK}egoread.php?bd=11&seq={seq}"
    d = f.doc(url)
    lines = text_of(d.xpath("//body")[0]).splitlines()
    i = next((k for k, x in enumerate(lines) if x.startswith("제공 기관명")), None)
    org = lines[i].replace("제공 기관명", "").strip() or (lines[i + 1] if i is not None else None) if i is not None else None
    j = next((k for k, x in enumerate(lines) if "함께 게재중인" in x), len(lines))
    body = "\n".join(lines[(i or 0) + 1:j])
    official, apply_to = None, None
    for a in d.xpath("//a[@href]"):
        label = clean(a.text_content())
        if label == "주최사 공고 바로가기" and a.get("href", "").startswith("http"):
            official = a.get("href")
        elif label == "참여하기" and a.get("href", "").startswith("http"):
            apply_to = a.get("href")
    official = forms.expand(f, official, cache, today_kst().isoformat()) if official else None
    apply_to = forms.expand(f, apply_to, cache, today_kst().isoformat()) if apply_to else None
    m = re.match(r"\[([^\]]+)\]\s*(.+)", title)
    org = org or (m.group(1) if m else None)
    tname = m.group(2) if m else title
    # 제목 뒤쪽 ' - 경품 ○명 추첨' 표기는 주최 쪽 입력값이라 경품으로 쓸 수 있다
    tail = re.search(r"\s-\s(.+)$", tname)
    prize = tail.group(1) if tail and re.search(r"명|증정|추첨|쿠폰|상품권", tail.group(1)) else None
    # 원문 공지와 응모 링크를 구분: 공지가 있으면 url, '참여하기'는 응모 링크 후보
    target = official or apply_to or url
    return build_event(src="ck", sid=seq, title=tname, url=target, org=org, body=body, start=start, end=end,
                       prize=prize, list_url=url, links=[x for x in (apply_to, official) if x])


def contestkorea(f, state, cfg):
    st = state.setdefault("contestkorea", {"parsed": {}})
    cache = state.setdefault("expand", {})
    events, skipped, seen, listed, budget = [], {}, [], 0, cfg.get("max_details", 30)
    done = set()
    for page in range(1, cfg.get("max_pages", 8) + 1):
        d = f.doc(CK_LIST.format(page))
        new = 0
        for li in d.xpath("//div[@class='media_list']//li"):
            href = "".join(li.xpath(".//div[@class='txt_area']/a/@href"))
            mm = re.search(r"seq=(\d+)", href)
            if not mm or mm.group(1) in done:
                continue
            seq = mm.group(1)
            done.add(seq)
            new += 1
            listed += 1
            title = clean(" ".join(li.xpath(".//div[@class='txt']//text()")))
            when = clean(" ".join(li.xpath(".//div[@class='date']/text()")))
            ds = find_dates(when)
            start, end = (ds[0], ds[1]) if len(ds) >= 2 else (None, None)
            if end and end < today_kst():
                continue
            if seq in st["parsed"]:
                if not st["parsed"][seq].startswith("skip:"):
                    seen.append(st["parsed"][seq])
                continue
            if budget <= 0:
                continue
            budget -= 1
            try:
                ev, why = _ck_detail(f, st, seq, title, start, end, cache)
            except Exception as e:
                skipped[seq] = f"오류: {type(e).__name__}"
                continue
            st["parsed"][seq] = ev["id"] if ev else "skip:" + (why or "")
            if ev:
                events.append(ev)
                seen.append(ev["id"])
            else:
                skipped[seq] = why
        if not new:
            break  # 더 이상 새 글이 없으면 멈춘다
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


WV = "https://www.wevity.com/"


def wevity(f, state, cfg):
    st = state.setdefault("wevity", {"parsed": {}})
    cache = state.setdefault("expand", {})
    events, skipped, seen, listed = [], {}, [], 0
    nos = []
    for page in range(1, cfg.get("max_pages", 3) + 1):
        d = f.doc(f"{WV}?c=event&s=1&gub=1&gbn=list&gp={page}")
        found = [re.search(r"ix=(\d+)", h).group(1) for h in d.xpath("//ul[@class='list']//div[@class='tit']/a/@href") if "ix=" in h]
        fresh = [x for x in found if x not in nos]
        if not fresh:
            break
        nos += fresh
    listed = len(nos)
    today = today_kst()
    for ix in nos:
        if ix in st["parsed"]:
            if not st["parsed"][ix].startswith("skip:"):
                seen.append(st["parsed"][ix])
            continue
        url = f"{WV}?c=event&s=1&gub=1&gbn=view&ix={ix}"
        try:
            d = f.doc(url)
            lines = text_of(d.xpath("//body")[0]).splitlines()
            # 왼쪽 메뉴에도 같은 글자('주최사' 등)가 있어 본문의 '경품종류' 뒤부터 찾는다(앞의 메뉴 항목은 '전체' 다음에 '경품종류별')
            k0 = next(n for n, x in enumerate(lines) if x == "경품종류")

            def after(label):
                k = next((n for n in range(k0, len(lines)) if lines[n] == label), None)
                return lines[k + 1] if k is not None and k + 1 < len(lines) else ""

            title = lines[k0 - 2] if lines[k0 - 1].startswith("조회수") else lines[k0 - 1]
            s, e = period(after("행사기간"))
            k1 = next((n for n, x in enumerate(lines) if x.startswith("행사내용")), None)
            body = "\n".join(lines[k1:k1 + 60]) if k1 is not None else ""
            ann = find_dates(after("당첨자발표일"))
            home = after("홈페이지")
            home = forms.expand(f, home, cache, today.isoformat()) if home.startswith("http") else None
            ev, why = build_event(src="wv", sid=ix, title=title, url=home or url, org=after("주최사") or None, body=body,
                                  start=s, end=e, announce=ann[0] if ann else None, list_url=url, links=[home] if home else [])
        except Exception as ex:
            skipped[ix] = f"오류: {type(ex).__name__}"
            continue
        st["parsed"][ix] = ev["id"] if ev else "skip:" + (why or "")
        if ev and e and e >= today:
            events.append(ev)
            seen.append(ev["id"])
        elif not ev:
            skipped[ix] = why
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}

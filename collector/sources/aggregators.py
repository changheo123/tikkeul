# 경품 이벤트 모음 사이트·기업·방송사 이벤트 목록
#  - 이벤트하우스(eventhouse.kr): 플랫폼별 진행 이벤트 목록(robots.txt 없음). 응모 링크는 로그인 필요라 쓰지 않고 목록 정보만 사용
#  - korissue(korissue.mycafe24.com): 오늘·내일 마감 이벤트와 주최 원문 응모 링크(robots.txt 없음)
#  - 다나와 이벤트(event.danawa.com): 다나와 자체 무료 이벤트 목록(robots.txt 허용)
#  - SBS 이벤트(www.sbs.co.kr/event): 방송사 공식 이벤트 목록(robots.txt 없음)
import re
import datetime as dt
from urllib.parse import urlencode

from ..common import text_of, find_dates, clean, today_kst
from ..extract import build_event


def guess_year_dates(text):
    """'10/07~ 10/28 (10/30)'처럼 연도가 없는 날짜: 오늘 기준 앞뒤 6개월 안으로 연도를 맞춘다."""
    t = today_kst()
    m0 = re.search(r"(\d{1,2})\s*/\s*\d{1,2}", text or "")
    ref = t.year
    if m0:
        mo = int(m0.group(1))
        if mo - t.month > 6:
            ref -= 1
        elif t.month - mo > 6:
            ref += 1
    return find_dates(text, ref)


def numbered(method):
    """'① 팔로우 ② 댓글' → ['1. 팔로우', '2. 댓글']"""
    parts = [x.strip() for x in re.split(r"\s*[①-⑩]\s*", method or "") if x.strip()]
    return [f"{i}. {x}" for i, x in enumerate(parts, 1)]


def tidy_prize(p):
    """모음 사이트 경품 표기 정리: '상품-20명' → '상품 20명', 가려진 인원(00명)·'(포함 총)' 제거."""
    if not p:
        return None
    p = re.sub(r"\(포함 ?총\)", "", p)
    p = re.sub(r"[-+]?\s*(?<!\d)0+\s*명", "", p)  # 사이트가 가린 인원(00명)만 지우고 20명 같은 수는 그대로
    p = re.sub(r"\s*[-+]\s*(\d[\d,]*)\s*명", r" \1명", p)
    p = re.sub(r"\s+", " ", p).strip(" -+")
    return p or None


# 응모형태 → (사이트 종류, 조건 많음 사유)
def map_form(form):
    f = form or ""
    if re.search(r"구매|결제", f):
        return None, "유료"
    if "퀴즈" in f or "퍼즐" in f:
        return "퀴즈", None
    if "설문" in f:
        return "설문", None
    if "투표" in f:
        return "투표", None
    if re.search(r"댓글|기대평|응원|후기|작문", f):
        return "댓글", None
    if "회원가입" in f:
        return "기타", "신규 회원가입 필요"
    if re.search(r"공유|소문|리그램|스크랩", f):
        return "기타", "공유·게시형"
    if re.search(r"체험|리뷰어|서포터즈", f):
        return "체험단", None
    if "공모" in f:
        return "기타", "공모전"
    return None, None


# ---- 이벤트하우스 ----
EH = "https://www.eventhouse.kr/site/"
EH_PLATS = {"website.php": "web", "instagram.php": "insta", "youtube.php": "yt", "blog.php": "naver", "facebook.php": "web"}


# 목록 제목 앞 아이콘 번호 → 플랫폼(검색 결과에는 플랫폼별 페이지 구분이 없다)
EH_ICON = {"1": "web", "2": "web", "3": "naver", "4": "naver", "5": "x", "6": "web", "7": "insta", "9": "yt"}


def _eh_page(f, page_file, page):
    return _eh_rows(f, f"{EH}{page_file}?p=1&page={page}")


def _eh_rows(f, url):
    d = f.doc(url)
    rows = []
    for row in d.xpath("//div[contains(@class,'list_row')]"):
        a = row.xpath(".//div[@class='list_no']/a/@href")
        no = re.search(r"no=(\d+)", a[0]).group(1) if a else None
        det = row.getnext()
        if not no or det is None or "list_detail" not in (det.get("class") or ""):
            continue
        fields = {}
        for td in det.xpath(".//div[contains(@class,'detail_td')]"):
            k = clean(" ".join(td.xpath("./div[contains(@class,'td_left')]//text()"))).strip("ㆍ ")
            v = text_of((td.xpath("./div[contains(@class,'td_r')]") or [None])[0])
            if k:
                fields[k] = clean(v)
        # 목록의 숨은 칸에 1등 외 전체 경품이 들어 있다
        fields["전체경품"] = [clean(x) for x in det.xpath(".//div[contains(@class,'div_gift_sort')]//li/text()") if clean(x)]
        subj = clean(" ".join(row.xpath(".//div[contains(@class,'list_subject')]//text()")))
        icon = re.search(r"list_(\d+)\.gif", " ".join(row.xpath(".//div[contains(@class,'list_subject')]//img/@src")))
        rows.append((no, subj, fields, EH_ICON.get(icon.group(1)) if icon else None))
    return rows


def _eh_add(no, subj, fd, plat, today, events, seen, skipped):
    """목록 항목 하나를 이벤트로 만든다. 진행 중이면 True."""
    ds = find_dates(fd.get("응모기간", ""))
    start, end = (ds[0], ds[1]) if len(ds) >= 2 else (None, ds[0] if ds else None)
    if end and end < today:
        return False
    m = re.match(r"\[([^\]]+)\]\s*(.+?)\s*(\d[\d,]*)?$", subj)
    title = m.group(2) if m else subj
    org = fd.get("주최회사") or (m.group(1) if m else None)
    kind, reason = map_form(fd.get("응모형태"))
    if reason == "유료":
        skipped[no] = "유료 응모형태"
        return True
    ann = find_dates(fd.get("발표일정", ""))
    body = "\n".join([f"응모형태: {fd.get('응모형태', '')}", "참여 방법", *numbered(fd.get("응모방법", ""))])
    url = f"{EH}view.php?no={no}"
    ev, why = build_event(src="eh", sid=no, title=title + " 이벤트" if "이벤트" not in title else title, url=url,
                          org=org, body=body, start=start, end=end, announce=ann[0] if ann else None,
                          prize=tidy_prize(fd.get("경품내역")), kind_hint=kind, extra_reasons=[reason] if reason else [],
                          list_url=url, plat_hint=plat)
    if ev:
        ev["tags"].append("원문 미확인")
        if fd.get("전체경품"):
            ev["prizeAll"] = [tidy_prize(x) for x in fd["전체경품"]]
        events.append(ev)
        seen.append(ev["id"])
    else:
        skipped[no] = why
    return True


def eventhouse_search(f, state, cfg):
    """이벤트하우스 상세 검색: 경품 분류(가전·디지털, 노트북·태블릿), 플랫폼(X·카페), 경품 내역 키워드. 진행 중인 경품만."""
    today = today_kst()
    events, skipped, seen, listed, done = [], {}, [], 0, set()
    queries = [dict(q, smode=1, mgid=1) for q in cfg.get("searches", [])]
    queries += [{"smode": 1, "mgid": 1, "msview": 4, "keyword": k} for k in cfg.get("keywords", [])]
    for q in queries:
        for page in range(1, cfg.get("max_pages", 3) + 1):
            rows = _eh_rows(f, f"{EH}index.php?" + urlencode({**q, "page": page}))
            fresh = [r for r in rows if r[0] not in done]
            if not fresh:
                break  # 결과가 없거나 페이지 번호를 넘어 같은 목록이 반복되면 멈춘다
            for no, subj, fd, plat in fresh:
                done.add(no)
                listed += 1
                if q.get("mplat") == 5:
                    plat = "x"
                _eh_add(no, subj, fd, plat, today, events, seen, skipped)
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


def eventhouse(f, state, cfg):
    st = state.setdefault("eventhouse", {})
    now = dt.datetime.now().timestamp()
    # 전체 훑기는 full_every_min 간격, 그 사이에는 각 플랫폼 앞쪽 몇 쪽만
    # 전체 경품 목록(prizeAll)을 기존 이벤트에도 채우기 위해 처음 한 번은 전체를 훑는다
    full = now - st.get("lastFull", 0) >= cfg.get("full_every_min", 360) * 60 or not st.get("prizeAllDone")
    max_pages = cfg.get("max_pages", 12) if full else cfg.get("quick_pages", 2)
    today = today_kst()
    events, skipped, seen, listed = [], {}, [], 0
    for page_file, plat in EH_PLATS.items():
        for page in range(1, max_pages + 1):
            rows = _eh_page(f, page_file, page)
            if not rows:
                break
            live_on_page = 0
            for no, subj, fd, _icon in rows:
                listed += 1
                live_on_page += _eh_add(no, subj, fd, plat, today, events, seen, skipped)
            if page > 1 and live_on_page == 0:
                break  # 등록순 목록이라 진행 중인 글이 없는 쪽이 나오면 그 뒤는 모두 지난 글
    if full:
        st["lastFull"] = now
        st["prizeAllDone"] = True
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


# ---- korissue: 오늘·내일 마감 + 원문 응모 링크 ----
KOR = "https://korissue.mycafe24.com/"
KOR_PLAT = {"홈페이지": "web", "페이스북": "web", "블로그": "naver", "인스타": "insta", "유튜브": "yt"}


def _kor_page(d, state, events, skipped, seen):
    """korissue 목록 HTML(마감별·주최사별 공통)을 읽는다. 주최사 번호는 다음 조회를 위해 기억한다. 읽은 건수를 돌려준다."""
    parents = state.setdefault("korissue", {}).setdefault("parents", {})
    listed = 0
    for g in d.xpath("//div[@class='g']"):
        sub = g.getnext()
        if sub is None or "gsub" not in (sub.get("class") or ""):
            continue
        listed += 1
        head = clean(g.text_content()).lstrip("● ").strip()
        m = re.match(r"\[\s*([^\]]+?)\s*\]\s*(.+)", head)
        org, title = (m.group(1), m.group(2)) if m else (None, head)
        for oc in sub.xpath(".//span[@class='sitemore']/@onclick"):
            pm = re.search(r"\?parent=(\d+)&keyword=([^'\"]+)", oc)
            if pm:
                parents.setdefault(pm.group(1), {"kw": pm.group(2), "at": None})
        fields, links = {}, {}
        for p in sub.xpath("./p"):
            t = clean(p.text_content())
            mm = re.match(r"ㆍ\s*([^ㆍ]+?)\s*ㆍ\s*(.*)", t)
            if mm:
                fields[mm.group(1)] = mm.group(2)
            for a in p.xpath(".//a[@href]"):
                links[clean(a.text_content())] = a.get("href")
        num = fields.get("응모번호", "")
        plat = next((v for k, v in KOR_PLAT.items() if k in num), None)
        sid = (re.search(r"(\d{4,})-\d+", num) or re.search(r"(\d+)", g.get("id") or "0")).group(1)
        ds = guess_year_dates(fields.get("응모기간", ""))
        start, end = (ds[0], ds[1]) if len(ds) >= 2 else (None, None)
        ann = ds[2] if len(ds) >= 3 else None
        if end and end < today_kst():
            continue  # 주최사 검색은 이미 끝난 이벤트도 돌려준다
        kind, reason = map_form(fields.get("응모형태"))
        if reason == "유료":
            skipped[sid] = "유료 응모형태"
            continue
        orig = links.get("응모주소") or links.get("폼양식")
        if not orig or not orig.startswith("http"):
            skipped[sid] = "응모 주소 없음"
            continue
        prize = tidy_prize(fields.get("경품내역"))
        body = "\n".join([f"응모형태: {fields.get('응모형태', '')}", "참여 방법", *numbered(fields.get("응모메모", ""))])
        ev, why = build_event(src="ki", sid=sid, title=title, url=orig, org=org, body=body, start=start, end=end,
                              announce=ann, prize=prize, kind_hint=kind, extra_reasons=[reason] if reason else [],
                              plat_hint=plat, links=[v for k, v in links.items() if k != "홈"])
        if ev:
            if links.get("폼양식") and links.get("폼양식") != orig:
                ev["needs"] = [f"응모 양식: {links['폼양식']}"]
            full = [x.strip(" ㆍ") for x in text_of((sub.xpath(".//div[@class='gsort']") or [None])[0]).splitlines() if x.strip(" ㆍ")]
            if full:
                ev["prizeAll"] = [tidy_prize(x) for x in full]
            events.append(ev)
            seen.append(ev["id"])
        else:
            skipped[sid] = why
    return listed


def korissue(f, state, cfg):
    events, skipped, seen, listed = [], {}, [], 0
    for opt in cfg.get("options", [2, 3]):  # 2=오늘 마감, 3=내일 마감
        listed += _kor_page(f.doc(f"{KOR}?option={opt}"), state, events, skipped, seen)
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


def korissue_orgs(f, state, cfg):
    """목록에서 본 주최사의 진행 이벤트를 주최사별로 읽어 원본 응모 링크를 일찍 확보한다.
    사이트가 화면에 연결해 둔 주최사 번호만 쓴다(번호를 임의로 넘겨 가며 조회하지 않음)."""
    parents = state.setdefault("korissue", {}).setdefault("parents", {})
    t = today_kst()
    revisit = cfg.get("revisit_days", 3)
    todo = [k for k, v in parents.items() if not v.get("at") or (t - dt.date.fromisoformat(v["at"])).days >= revisit]
    todo.sort(key=lambda k: parents[k].get("at") or "")
    events, skipped, seen, listed = [], {}, [], 0
    for pid in todo[:cfg.get("per_run", 60)]:
        listed += _kor_page(f.doc(f"{KOR}?parent={pid}&keyword={parents[pid]['kw']}"), state, events, skipped, seen)
        parents[pid]["at"] = t.isoformat()
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


# ---- 다나와 이벤트 ----
DNW = "https://event.danawa.com"


def danawa(f, state, cfg):
    events, skipped, seen, listed = [], {}, [], 0
    seen_urls = set()
    for page in range(1, cfg.get("max_pages", 3) + 1):
        d = f.doc(f"{DNW}/event/lists?page={page}")
        rows = d.xpath("//table[contains(@class,'list_tbl')]//tbody/tr")
        if not rows:
            break
        live = 0
        for tr in rows:
            state_txt = clean(" ".join(tr.xpath("./td[@class='state']//text()")))
            a = tr.xpath("./td[@class='title']//a")
            if not a or "진행" not in state_txt or a[0].get("href") in seen_urls:
                continue  # 페이지 번호를 무시하고 같은 목록을 주면 이미 본 글은 건너뛴다
            seen_urls.add(a[0].get("href"))
            live += 1
            listed += 1
            title = clean(a[0].text_content())
            url = a[0].get("href")
            ds = find_dates(clean(" ".join(tr.xpath("./td[@class='due']//text()"))))
            ann = find_dates(clean(" ".join(tr.xpath("./td[@class='date']//text()"))))
            typ = clean(" ".join(tr.xpath("./td[@class='type']//text()")))
            org = clean(" ".join(tr.xpath("./td[@class='company']//text()")))
            kind, reason = map_form(typ)
            if reason == "유료":
                continue
            ev, why = build_event(src="dn", sid=url, title=title if "이벤트" in title else title + " 이벤트", url=url,
                                  org=f"다나와 · {org}" if org else "다나와", body=f"이벤트 유형: {typ}",
                                  start=ds[0] if len(ds) >= 2 else None, end=ds[1] if len(ds) >= 2 else None,
                                  announce=ann[0] if ann else None, kind_hint=kind or ("체험단" if "체험" in title else None),
                                  extra_reasons=[reason] if reason else [], extra_tags=["로그인 필요"])
            if ev:
                ev["needs"] = ["다나와 회원 로그인 후 응모"]
                events.append(ev)
                seen.append(ev["id"])
            else:
                skipped[url] = why
        if not live:
            break
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


# ---- SBS 이벤트(방송사) ----
SBS = "https://www.sbs.co.kr/event"


def sbs(f, state, cfg):
    d = f.doc(SBS)
    events, skipped, seen, listed = [], {}, [], 0
    done = set()
    for a in d.xpath("//a[contains(@href,'div=event_bottom')]"):
        url = re.sub(r"[?&]div=event_bottom", "", a.get("href"))
        if url in done:
            continue
        done.add(url)
        lines = text_of(a).splitlines()
        if not lines:
            continue
        listed += 1
        title = lines[0]
        ds = find_dates(" ".join(lines[1:3]))
        if len(ds) < 2:
            skipped[title] = "기간 없음(상시 이벤트)"
            continue
        # 앱 설치·신규회원·유료 이용권 이벤트는 무료 경품 이벤트가 아니어서 뺀다
        if re.search(r"이용권|구독자|신규회원|앱 설치|포럼", title):
            skipped[title] = "가입·이용권 조건"
            continue
        ev, why = build_event(src="sb", sid=url, title=title if "이벤트" in title else title + " 이벤트", url=url,
                              org="SBS", body=title, start=ds[0], end=ds[1], kind_hint="라디오" if re.search(r"FM|라디오|방청|본방|시청", title) else None)
        if ev:
            events.append(ev)
            seen.append(ev["id"])
        else:
            skipped[title] = why
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}

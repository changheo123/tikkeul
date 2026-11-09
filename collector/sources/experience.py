# 무료 체험단 모집 사이트(모두 robots.txt 허용, 서버가 목록 HTML을 그대로 줌)
#  - 리뷰노트(reviewnote.co.kr), 미블(mrblog.net), 강남맛집(강남맛집.net)
# 카드의 'N일 남음·신청·모집·배송형/방문형'을 읽는다. 선결제·페이백·구매평 체험단은 유료로 보고 뺀다
import re
import datetime as dt

from ..common import text_of, clean, today_kst
from ..extract import build_event

SITES = {
    "reviewnote": {"src": "rn", "org": "리뷰노트", "lists": ["https://www.reviewnote.co.kr/campaigns"],
                   "href": r"reviewnote\.co\.kr/campaigns/(\d+)$"},
    # /campaigns 목록은 스크립트로 그려져 서버 HTML에 카드가 있는 첫 화면을 쓴다
    "mrblog": {"src": "mb", "org": "미블", "lists": ["https://www.mrblog.net/"], "layout": "region",
               "href": r"mrblog\.net/campaigns/(\d+)$"},
    "gangnam": {"src": "gm", "org": "강남맛집 체험단", "lists": ["https://xn--939au0g4vj8sq.net/cp/"],
                "href": r"/cp/\?id=(\d+)$"},
}
META = re.compile(r"^(?:선착순\s*\d*|\d+\s*일\s*남음|\d+|일 남음|남음|D-?Day|D-\d+|신청\s*[\d,]+\s*명?|/?\s*모집\s*[\d,]+\s*명?|배송형?|방문형?|기자단|재택|포장|Blog.*|블로그|인스타그램|유튜브|클립|릴스|마감임박|NEW|HOT|프리미엄|/)$", re.I)


def _card(a):
    """링크에서 위로 올라가며 남은 날짜가 들어 있는 가장 작은 카드 상자를 찾는다."""
    box = a
    for _ in range(4):
        t = text_of(box)
        if re.search(r"남음|D-?Day|D-\d+", t) and len(t) < 700:
            return t
        if box.getparent() is None:
            break
        box = box.getparent()
    return None


def _parse(text):
    flat = " ".join(text.splitlines())
    m = re.search(r"(\d+)\s*/?\s*일\s*남음", flat)
    days = int(m.group(1)) if m else (0 if re.search(r"D-?Day", flat, re.I) else None)
    m2 = re.search(r"D-(\d+)", flat)
    if days is None and m2:
        days = int(m2.group(1))
    typ = next((t for t in ("배송형", "방문형", "기자단", "재택", "포장") if t in flat), None)
    rec = re.search(r"모집\s*([\d,]+)", flat)
    lines = [x for x in (clean(l) for l in text.splitlines())
             if x and not META.match(x) and not re.fullmatch(r"[\d,\s/]+", x) and not re.fullmatch(r"신청\s*[\d,]+\s*명?\s*/\s*모집\s*[\d,]+\s*명?", x)]
    return days, typ, (int(rec.group(1).replace(",", "")) if rec else None), lines


def collect_site(f, key, cfg):
    s = SITES[key]
    today = today_kst()
    events, skipped, seen, listed, done = [], {}, [], 0, set()
    allow = set(cfg.get("types", ["배송형", "재택", "기자단", "방문형", "포장"]))
    cap = cfg.get("max_items", 80)
    for lu in s["lists"]:
        d = f.doc(lu)
        for a in d.xpath("//a[@href]"):
            m = re.search(s["href"], a.get("href"))
            if not m or m.group(1) in done:
                continue
            text = _card(a)
            if not text:
                continue
            days, typ, rec, lines = _parse(text)
            if days is None or not lines:
                continue  # 이미지 링크처럼 제목이 없는 링크는 넘기고 같은 캠페인의 제목 링크를 기다린다
            done.add(m.group(1))
            listed += 1
            if typ and typ not in allow:
                continue
            atext = [x for x in (clean(l) for l in text_of(a).splitlines()) if x and not META.match(x)]
            if len(atext) == 1 and lines[0].startswith(atext[0]):
                # 리뷰노트: 링크 글자가 제목, 같은 줄 뒤쪽이 제공 내역
                title, offer = atext[0], lines[0][len(atext[0]):].strip() or None
            elif s.get("layout") == "region" and len(lines) >= 3:
                # 미블: 지역 / 업체명 / 제공 내역
                title, offer = f"[{lines[0]}] {lines[1]}", lines[2]
            else:
                title = lines[0]
                offer = next((x for x in lines[1:] if re.search(r"원|제공|체험|이용권|식사|상품|1개|세트|\+", x)), lines[1] if len(lines) > 1 else None)
            if title == offer:
                offer = None
            body = "\n".join(lines)
            ev, why = build_event(
                src=s["src"], sid=m.group(1), title=f"{title} 체험단" if "체험단" not in title else title,
                url=a.get("href"), org=s["org"], body=body, end=today + dt.timedelta(days=days),
                prize=f"{offer} (체험 제공{f', {rec}명 모집' if rec else ''})" if offer else (f"체험 제공, {rec}명 모집" if rec else "체험 제공"),
                kind_hint="체험단", extra_tags=(["방문 필요"] if typ == "방문형" else []))
            if ev:
                ev["needs"] = ["블로그·SNS 후기 작성 조건", "신청 후 선정되어야 참여" + (f"({typ})" if typ else "")]
                ev["mode"] = "선정"
                ev["count"] = rec
                events.append(ev)
                seen.append(ev["id"])
                if len(events) >= cap:
                    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}
            else:
                skipped[m.group(1)] = why
    return {"events": events, "seen": seen, "listed": listed, "skipped": skipped}


def reviewnote(f, state, cfg):
    return collect_site(f, "reviewnote", cfg)


def mrblog(f, state, cfg):
    return collect_site(f, "mrblog", cfg)


def gangnam(f, state, cfg):
    return collect_site(f, "gangnam", cfg)

# 모음 사이트 링크를 주최 원문으로 바꾸고, 빠진 경품명을 공개된 글에서 보완한다. 확인되지 않으면 기존 값을 그대로 둔다
import datetime as dt
import difflib
import re
import time
from urllib.parse import unquote, urlencode

from .common import Fetcher
from .enrich import GENERIC, prize_items
from .extract import find_count, find_prize, pay_required, tidy
from . import forms

AGG_URL = re.compile(r"events\.kr/|t\.me/|eventhouse\.kr/|contestkorea\.com/|wevity\.com/")
URL = re.compile(r"https?://[^\s\"'<>)\]]+")
NOT_ORIGINAL = re.compile(r"korissue|\.(?:png|jpe?g|gif|webp)(?:\?|$)", re.I)
# 로그인·스크립트 전용이거나 약관상 읽지 않는 곳은 페이지를 열지 않는다
PRIZE_NO_FETCH = re.compile(r"instagram\.com|youtube\.com|youtu\.be|blog\.naver\.com|cafe\.naver\.com|facebook\.com|//x\.com/|twitter\.com|t\.me/|eventhouse\.kr|reviewnote\.co\.kr|mrblog\.net|forms\.gle|docs\.google\.com|form\.naver\.com|naver\.me")
# 유의사항 문장은 경품 안내가 아니다
NOTICE = re.compile(r"배송|반송|취소|제외|불가|전송|지급|변경|참여\s*시|개인정보|유의|원고료|재판매|발표")
STRICT = re.compile(r"상품권|기프티콘|쿠폰|커피|아메리카노|스타벅스|투썸|메가커피|컴포즈|네이버페이|금액권|배달의민족|배민|치킨|피자|케이크|에어팟|갤럭시|아이패드")
TITLE_PRIZE = re.compile(
    r"(?:^|[\s!,])((?:네이버페이|배달의민족|배민|스타벅스|투썸|메가커피|컴포즈|이디야|올리브영|호텔|숙박권|커피|아메리카노|기프티콘|문화상품권|모바일\s*상품권|상품권|치킨|해피콘|에어팟|갤럭시|아이패드|다이어리)[^!,]*?)"
    r"\s*(?:도\s*)?(?:증정|받기|받자|받아요|받으세요|쏩니다|드려요|드립니다)")
VERSION = 1


def no_prize(e):
    """쓸 만한 경품명이 없는 이벤트."""
    return not prize_items(e)


def via_orgs(db):
    """아직 모음 사이트 링크인 진행 중 이벤트의 주최명(주최사별 원문 조회 우선순위용)."""
    return sorted({_norm(e.get("org")) for e in db["events"].values()
                   if e.get("status") == "active" and AGG_URL.search(e.get("url") or "") and e.get("org")} - {""})


def _norm(s):
    return re.sub(r"\(주\)|㈜|주식회사|\.\.+|…|\s", "", unquote(s or "")).lower()


def is_priority(kw, orgs):
    k = _norm(kw)
    return len(k) >= 2 and any(o.startswith(k) or k in o for o in orgs)


def adopt_known_originals(db):
    """이미 저장된 글에 주최 원문 링크가 딱 하나 적혀 있으면 대표 링크로 쓴다(여러 개면 어느 것인지 알 수 없어 그대로 둔다)."""
    n = 0
    for e in db["events"].values():
        if e.get("status") != "active" or e.get("src") not in ("ek", "ledger") or not AGG_URL.search(e.get("url") or ""):
            continue
        text = " ".join([e.get("rawText") or "", *(e.get("srcUrls") or [])])
        found = {u.rstrip(".,") for u in URL.findall(text) if not AGG_URL.search(u) and not NOT_ORIGINAL.search(u)}
        if len(found) == 1:
            e["url"] = found.pop()
            e["tags"] = [t for t in e.get("tags") or [] if t != "원문 미확인"]
            n += 1
    return n


def _from_text(text, strict=False):
    lines = [x for x in (text or "").splitlines() if x.strip() and not NOTICE.search(x)]
    p = find_prize("\n".join(lines))
    if not p or (strict and not STRICT.search(p)):
        return None
    return p


def _from_title(title):
    m = TITLE_PRIZE.search(title or "")
    return tidy(m.group(1)) if m else None


def _page_text(f, url):
    d = f.doc(url)
    for bad in d.xpath("//script|//style|//nav|//header|//footer"):
        bad.drop_tree()
    return "\n".join(x.strip() for x in d.text_content().splitlines() if x.strip())


def _set(e, prize, how):
    # 유료 조건이 섞인 문구는 경품으로 쓰지 않는다
    if not prize or pay_required(prize):
        return False
    probe = dict(e, prize=prize, prizeAll=None)
    if not prize_items(probe):
        return False  # '선물'처럼 실제 경품명이 아니면 채우지 않는다
    e["prize"], e["prizeFrom"] = prize, how
    if not e.get("count"):
        e["count"] = find_count(prize)
    return True


def restore_truncated(db, today, cfg):
    """출처가 잘라 놓은 경품명('메가커피 달콤한 하..')을 이벤트하우스 공개 검색의 같은 글로 복원한다.
    제목이 거의 같고 경품명의 앞부분이 일치할 때만 바꾼다."""
    from .extract import norm_title
    from .sources.aggregators import EH, _eh_rows, tidy_prize
    res = {"checked": 0, "restored": 0, "err": 0}
    f = Fetcher(min_interval=cfg.get("eh_search_interval_sec", 1.2), timeout=10, retries=0)
    t, limit = dt.date.fromisoformat(today), cfg.get("truncated_checks_per_run", 40)
    for e in db["events"].values():
        p = e.get("prize") or ""
        if e.get("status") != "active" or ".." not in p or len(e.get("prizeAll") or []) > 1:
            continue
        c = e.get("truncChecked")
        if res["checked"] >= limit or (c and (t - dt.date.fromisoformat(c)).days < 7):
            continue
        head = p.split("..")[0].strip()
        if len(head) < 2:
            continue
        res["checked"] += 1
        e["truncChecked"] = today
        try:
            rows = _eh_rows(f, f"{EH}index.php?" + urlencode({"smode": 1, "mgid": 1, "msview": 4, "keyword": " ".join(head.split()[:2])}))
        except Exception:
            res["err"] += 1
            continue
        title = norm_title(e.get("title"))
        for _no, subj, fd, _plat in rows:
            full = tidy_prize(fd.get("경품내역"))
            name = norm_title(re.sub(r"\s*\d[\d,]*$", "", subj))  # 제목 끝의 인원 숫자 제외
            if full and ".." not in full and full.startswith(head) and difflib.SequenceMatcher(None, title, name).ratio() >= 0.9:
                e["prize"], e["prizeFrom"] = full, "복원"
                res["restored"] += 1
                break
    return res


def fill_prizes(db, today, cfg):
    """경품명이 없는 진행 중 이벤트: 저장된 글 → 제목 → 공개 공지 페이지 순으로, 글에 적힌 경품만 채운다."""
    res = {"text": 0, "title": 0, "page": 0, "pages": 0, "page_err": 0, "left": 0}
    f = Fetcher(min_interval=cfg.get("min_interval_sec", 1.0), timeout=10, retries=0)
    t = dt.date.fromisoformat(today)
    limit, cap, t0 = cfg.get("prize_pages_per_run", 80), cfg.get("prize_pages_sec", 150), time.time()
    for e in sorted((x for x in db["events"].values() if x.get("status") == "active" and no_prize(x)), key=lambda x: x.get("end") or "9999"):
        if _set(e, _from_text(e.get("rawText")), "본문"):
            res["text"] += 1
            continue
        if _set(e, _from_title(e.get("title")), "제목"):
            res["title"] += 1
            continue
        res["left"] += 1
        url = e.get("url") or ""
        checked = e.get("prizeChecked")
        if not url.startswith("http") or PRIZE_NO_FETCH.search(url) or forms.is_form(url) or (checked and (t - dt.date.fromisoformat(checked)).days < 7):
            continue
        if res["pages"] >= limit or time.time() - t0 > cap:
            continue
        res["pages"] += 1
        e["prizeChecked"] = today
        try:
            text = _page_text(f, url)
        except Exception:
            res["page_err"] += 1
            continue
        if _set(e, _from_text(text, strict=True), "공지"):
            res["page"] += 1
            res["left"] -= 1
    return res

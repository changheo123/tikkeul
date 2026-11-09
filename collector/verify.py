# 응모 링크 검증. A등급만 응모 버튼에 쓰고, 불확실하면 이벤트 공지로 안내한다(추측해서 등록하지 않음)
# 역할: url=수집 원본 주소(변경 안 함) / form_url=본문에서 찾은 폼 후보 / original_url=확인된 주최자 공지 / entry_url=A등급 응모 주소
import datetime as dt
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from . import forms, linktrace
from .backfill import AGG_URL
from .common import Fetcher, period
from .enrich import prize_items

# 약관·robots상 페이지를 읽지 않는 곳(공식 공지에서 직접 연결될 때만 인정)
NO_FETCH = re.compile(r"instagram\.com|facebook\.com|//(?:www\.)?x\.com/|twitter\.com|youtube\.com|youtu\.be|cafe\.naver\.com|t\.me/|moaform\.com", re.I)
POST_LINK = re.compile(r"instagram\.com/(?:p|reel)/[\w\-]+|youtube\.com/(?:watch\?|shorts/)|youtu\.be/[\w\-]+", re.I)
# 수집기가 바로 그 페이지에서 글을 읽어 온 출처(공지 자체가 이벤트 원문)
SELF_SOURCE = {"rn", "mb", "dn", "sb", "bs", "te", "mo", "ledger", "ck", "wv"}
CLOSED = re.compile(r"더 이상 응답을 받지|응답을 받지 않|응답이 마감|설문이 종료|이벤트가 종료되었|종료된 이벤트입니다|이벤트가 마감되었|접수가 마감되었|모집이 마감되었|모집이 종료되었|존재하지 않는 (?:페이지|이벤트|게시)|삭제된 (?:게시|페이지)|페이지를 찾을 수 없")
ENTRY_CUE = re.compile(r"응모|참여하기|참여 ?방법|신청|이벤트 ?참여|댓글|설문|투표|퀴즈|체험단")
LINK_CUE = re.compile(r"응모|참여|신청|설문|바로 ?가기|이벤트|제출|클릭|폼|신청서")
PERIOD_LINE = re.compile(r"(?:응모|이벤트|참여|접수)\s*기간")
# 목록·첫 화면은 응모 페이지가 아니다(게시판 목록, 사이트 첫 화면)
LIST_PAGE = re.compile(r"(?:list|List)(?:\.\w+)?$|boardlist|bbs/list", re.I)
HOME_PAGE = re.compile(r"^/?(?:[\w\-]+/)?(?:index|main|home)(?:\.\w+)?$", re.I)
YEAR_NEAR = re.compile(r"(?:기간|발표|참여|응모|접수)[^0-9]{0,12}((?:20)?\d{2})\s*[.년/\-]\s*\d{1,2}\s*[.월/\-]\s*\d{1,2}")
FUTURE_DAYS = 45  # 연도 없는 목록 표기를 미래로 잘못 해석한 것으로 볼 기준
STOP = set("이벤트 경품 경품이벤트 받자 받아요 받아가자 챙기자 노리자 얻자 도전 참여 참여하고 참여로 하고 풀고 남기고 댓글 댓글로 퀴즈 설문 설문하고 응모 인증 인증하고 증정 선물 커피 상품권 추첨 무료 기념 안내 공지 네이버페이 기프티콘 쿠폰 받기 맞히고 작성 작성하고 구독 구독하고 팔로우 팔로우하고 EVENT event".split())
PARTICLE = re.compile(r"(?:으로|에서|하고|하면|해서|이랑|까지|부터|로|을|를|은|는|이|가|의|에|와|과|도)$")
PRIZE_STOP = set("모바일 상품권 쿠폰 기프티콘 금액권 교환권 세트 상당 포인트 이벤트".split())

TTL = {"A": 7, "B": 3, "C": 7}  # 등급별 재검증 주기(일)
URL_TTL = {"not_found": 7, "closed": 14, "robots": 14, "forbidden": 7, "login": 7, "blocked": 30, "error": 1}
STATUS = {"A": "verified", "B": "needs_review", "C": "unverified"}
BUTTON_TYPES = ("form", "website", "instagram", "youtube", "blog", "other")


def entry_type(url):
    u = url or ""
    if not u:
        return "unknown"
    if forms.is_form(u) or forms.SHORT_HOST.match(u) and re.search(r"forms\.gle|naver\.me", u):
        return "form"
    if "instagram.com" in u:
        return "instagram"
    if re.search(r"youtube\.com|youtu\.be", u):
        return "youtube"
    if re.search(r"blog\.naver\.com|cafe\.naver\.com|tistory\.com|brunch\.co\.kr", u):
        return "blog"
    if re.search(r"facebook\.com|//(?:www\.)?x\.com|twitter\.com|t\.me/", u):
        return "other"
    return "website"


def _norm(s):
    return re.sub(r"\s+", "", s or "").lower()


def tokens(title):
    out = []
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", title or ""):
        if len(w) > 3:
            w = PARTICLE.sub("", w)
        if len(w) >= 2 and w not in STOP and not w.isdigit():
            out.append(w)
    return list(dict.fromkeys(out))


def _date_forms(end):
    y, m, d = int(end[:4]), int(end[5:7]), int(end[8:10])
    return [f"{y}-{m:02}-{d:02}", f"{y}.{m:02}.{d:02}", f"{y}.{m}.{d}", f"{m}.{d}", f"{m:02}.{d:02}", f"{m}/{d}", f"{m:02}/{d:02}", f"{m}월{d}일"]


def identity(e, page):
    """페이지가 이 이벤트의 것인지 판단하는 근거들."""
    text = _norm(page.text)
    res = {}
    tk = tokens(e.get("title"))
    found = [w for w in tk if w.lower() in text]
    res["title_ratio"] = round(len(found) / len(tk), 2) if tk else 0
    res["title"] = bool(tk) and ((len(found) >= 2 and len(found) / len(tk) >= 0.6) or (len(tk) == 1 and len(found) == 1 and len(tk[0]) >= 4))
    org = re.sub(r"\(주\)|㈜|주식회사|\.\.+|…|\([^)]*\)|\s", "", e.get("org") or "").lower()
    res["org"] = len(org) >= 2 and org in text
    pz = [w for i in prize_items(e)[:1] for w in tokens(i) if w not in PRIZE_STOP]
    res["prize"] = bool(pz) and sum(w.lower() in text for w in pz) / len(pz) >= 0.5
    end = e.get("end")
    res["date"] = bool(end) and any(x in text for x in _date_forms(end))
    res["conflict"] = False
    if end:
        target = dt.date.fromisoformat(end)
        for line in page.text.splitlines():
            if PERIOD_LINE.search(line):
                _s, _e = period(line, target.year)
                if _e and abs((_e - target).days) > 2:
                    res["conflict"] = True
                    break
    res["stale"] = stale_year(e, page)
    return res


def stale_year(e, page):
    """페이지의 기간·발표일 연도가 수집된 마감 연도보다 앞서면 지난 행사다(제목의 연도도 본다)."""
    end = e.get("end")
    if not end:
        return False
    y = int(end[:4])
    titled = [int(x) for x in re.findall(r"(20\d{2})", e.get("title") or "")]
    if titled and max(titled) < y:
        return True
    years = {(lambda n: 2000 + n if n < 100 else n)(int(m.group(1))) for m in YEAR_NEAR.finditer(page.text)}
    return bool(years) and max(years) < y


def is_list_page(url, self_src):
    """게시판 목록이나 사이트 첫 화면인가. 수집기가 글을 읽어 온 원본 페이지는 첫 화면 규칙을 적용하지 않는다."""
    path = urlparse(url).path
    if LIST_PAGE.search(path):
        return True
    return not self_src and (path in ("", "/") or bool(HOME_PAGE.match(path)))


def confirmed(idn):
    if idn["conflict"] or idn.get("stale"):
        return False
    return (idn["title"] and (idn["org"] or idn["prize"] or idn["date"])) or (idn["org"] and idn["prize"] and idn["date"])


def _rec(grade, kind, reason, code, today, entry=None, original=None, evidence=None):
    return {"entry_type": kind, "entry_url": entry if grade == "A" else None, "original_url": original,
            "candidate_url": entry if grade != "A" else None, "verification_grade": grade, "verification_reason": reason, "verification_code": code,
            "verification_evidence_url": evidence, "verification_status": STATUS[grade], "last_checked_at": today}


class Checker:
    def __init__(self, f, today, urls, renderer=None, short_cache=None):
        self.f, self.today, self.urls, self.renderer = f, today, urls, renderer
        self.short = short_cache if short_cache is not None else {}
        self.requests = 0

    def page(self, url):
        """URL별 실패 결과는 주기 동안 다시 요청하지 않는다."""
        c = self.urls.get(url)
        if c and c["status"] in URL_TTL and (dt.date.fromisoformat(self.today) - dt.date.fromisoformat(c["at"])).days < URL_TTL[c["status"]]:
            return linktrace.Page(url=url, final=url, status=c["status"])
        self.requests += 1
        # 네이버 블로그 PC 주소는 본문이 프레임 안에 있어, 같은 글의 모바일 주소로 읽는다(저장은 원래 주소)
        read = re.sub(r"^https?://blog\.naver\.com/", "https://m.blog.naver.com/", url)
        p = linktrace.fetch_page(self.f, read)
        if read != url:
            p.url = url
            if p.status == "ok":
                p.final = url
        if linktrace.looks_dynamic(p) or (p.status == "ok" and "form.naver.com" in p.final):
            r = self.renderer.render(p.final) if self.renderer else None
            if r is not None:
                p = r
        if p.status == "ok" and CLOSED.search(p.text[:4000]):
            p.status = "closed"
        if p.status in URL_TTL:
            self.urls[url] = {"status": p.status, "at": self.today}
        return p

    def forms_on(self, page):
        """페이지에 직접 연결된 폼. (폼 목록, 애매 여부)"""
        visible = [(u, lab) for u, lab, kind in page.links if kind != "meta"]
        direct, short = forms.candidates(" ".join([page.text, *[u for u, _ in visible]]))
        found = list(direct)
        for s in short[:6]:
            final, _st = forms.resolve_short(self.f, s, self.short, self.today)
            if final and final not in found:
                found.append(final)
        if len(found) <= 1:
            return found, False
        cued = [x for x in found if any(forms.is_form(u) == x and LINK_CUE.search(lab or "") for u, lab in visible)]
        return (cued, False) if len(cued) == 1 else (found, True)

    def check_form(self, e, url):
        """폼 자체를 열어 본다. (상태 코드, 페이지 또는 None, 근거)"""
        if NO_FETCH.search(url):
            return "no_fetch", None, None
        p = self.page(url)
        if p.status != "ok":
            return p.status, p, None
        return "ok", p, identity(e, p)

    def verify(self, e):
        today = self.today
        url = e.get("url") or ""
        form = e.get("form_url")
        src = e.get("src")
        is_agg = bool(AGG_URL.search(url))
        kind = entry_type(url if not is_agg else form or "")

        # 1) 폼을 공지 없이 스스로 확인(폼 제목·설명이 이벤트와 일치)
        def from_form(extra=None):
            if not form:
                return None
            st, p, idn = self.check_form(e, form)
            if st == "ok" and idn and idn.get("stale"):
                return _rec("C", "form", "폼에 적힌 기간이 지난 행사와 일치", "stale_year", today, entry=form, original=extra, evidence=p.final)
            if st == "ok" and idn and confirmed(idn):
                return _rec("A", "form", "폼 제목·설명이 이벤트와 일치", "form_self", today, entry=form, original=extra, evidence=p.final)
            if st == "closed":
                return _rec("C", "form", "응답이 마감된 폼", "closed", today, entry=form, original=extra, evidence=p.final if p else None)
            if st in ("not_found",):
                return _rec("C", "form", "폼 페이지를 찾을 수 없음", "not_found", today, entry=form, original=extra)
            reason = {"login": "로그인이 필요한 폼이라 내용을 확인하지 못함", "robots": "robots.txt로 폼을 읽지 않음", "no_fetch": "약관·robots상 폼을 읽지 않음",
                      "forbidden": "접근이 제한된 폼", "error": "폼 접속 오류", "blocked": "안전하지 않은 주소"}.get(st, "폼과 이벤트의 일치를 확인하지 못함")
            return _rec("B", "form", reason, "form_" + st if st != "ok" else "identity_unconfirmed", today, entry=form, original=extra)

        # 2) 모음 사이트 글: 로그인 전용이면 추적하지 않고, 공개 글이면 본문의 외부 링크를 원문 후보로 본다
        if is_agg:
            if "eventhouse.kr" in url:
                r = from_form()
                return r or _rec("C", "unknown", "모음 사이트의 응모 링크가 로그인 전용이라 원문을 확인하지 못함", "aggregator_login", today)
            p = self.page(url)
            cands = [u for u, _lab, k in p.links if k != "meta" and not AGG_URL.search(u)] if p.status == "ok" else []
            if len(cands) != 1:
                r = from_form()
                return r or _rec("C", "unknown", "모음 글에서 주최 원문을 하나로 특정하지 못함", "no_original", today)
            url, is_agg = cands[0], False
            kind = entry_type(url)

        # 3) 약관·robots상 읽지 않는 SNS: 폼으로 확인되면 A, 아니면 확인 필요
        if NO_FETCH.search(url):
            r = from_form()
            if r and r["verification_grade"] != "B":
                return r
            return _rec("B", kind, "공식 게시물 후보지만 약관·robots상 내용을 읽을 수 없어 이벤트 일치를 확인하지 못함", "sns_no_fetch", today, entry=url)
        if not url.startswith("http"):
            return from_form() or _rec("C", "unknown", "확인 가능한 링크 없음", "no_link", today)
        if forms.is_form(url) or kind == "form":
            form = forms.is_form(url) or form or url
            r = from_form()
            if r:
                return r

        # 4) 공식 공지 페이지 열기
        p = self.page(url)
        if p.status != "ok":
            r = from_form()
            if r and r["verification_grade"] == "A":
                return r
            reason = {"closed": "종료·마감 안내가 있는 페이지", "not_found": "페이지를 찾을 수 없음", "login": "로그인이 필요한 페이지", "robots": "robots.txt로 페이지를 읽지 않음",
                      "forbidden": "접근이 제한된 페이지", "blocked": "안전하지 않은 주소", "non_html": "웹 페이지가 아님", "too_many": "리다이렉트가 너무 많음"}.get(p.status, "페이지 접속 오류")
            grade = "C" if p.status in ("closed", "not_found", "blocked", "too_many") else "B"
            return _rec(grade, kind, reason, p.status, today, entry=url)
        idn = identity(e, p)
        if idn["stale"]:
            return _rec("C", kind, "페이지에 적힌 기간이 지난 행사와 일치", "stale_year", today, entry=url, evidence=p.final)
        self_src = src in SELF_SOURCE and idn["title_ratio"] >= 0.6 and not idn["conflict"]
        if not (confirmed(idn) or self_src):
            r = from_form()
            if r and r["verification_grade"] == "A":
                return r
            code = "date_conflict" if idn["conflict"] else "identity_unconfirmed"
            why = "페이지의 응모 기간이 수집 정보와 다름" if idn["conflict"] else "페이지와 이벤트의 일치를 확인하지 못함"
            return _rec("C" if idn["conflict"] else "B", kind, why, code, today, entry=url, evidence=p.final)

        if is_list_page(p.final, src in SELF_SOURCE):
            r = from_form()
            if r and r["verification_grade"] == "A":
                return r
            return _rec("B", kind, "게시판 목록이나 사이트 첫 화면이라 이 이벤트의 응모 페이지로 볼 수 없음", "list_page", today, entry=url, evidence=p.final)

        # 5) 공식 공지 확인 → 공지에서 직접 연결된 실제 응모 페이지
        original = p.final
        found, ambiguous = self.forms_on(p)
        if ambiguous:
            return _rec("B", "form", "공지에 응모 폼이 여러 개라 하나로 특정하지 못함", "multiple_forms", today, entry=found[0], original=original, evidence=p.final)
        if found:
            st, fp, _ = self.check_form(e, found[0])
            if st in ("closed", "not_found"):
                return _rec("C", "form", "공지에 연결된 폼이 마감되었거나 없음", st, today, entry=found[0], original=original, evidence=p.final)
            if st in ("ok", "no_fetch", "robots", "login"):
                note = "" if st == "ok" else " (폼 내용은 약관·로그인으로 확인 불가)"
                return _rec("A", "form", "공식 공지에서 폼으로 직접 연결" + note, "official_form", today, entry=found[0], original=original, evidence=p.final)
            return _rec("B", "form", "공지에 연결된 폼에 접속하지 못함", "form_" + st, today, entry=found[0], original=original, evidence=p.final)
        posts = list(dict.fromkeys(u for u, _l, k in p.links if k in ("a", "data", "onclick") and POST_LINK.search(u)))
        if len(posts) == 1 and not NO_FETCH.search(url):
            return _rec("A", entry_type(posts[0]), "공식 공지에서 게시물로 직접 연결 (게시물 내용은 약관·robots상 확인 불가)", "official_sns", today, entry=posts[0], original=original, evidence=p.final)
        if ENTRY_CUE.search(p.text):
            return _rec("A", kind if kind in BUTTON_TYPES else "website", "공식 공지에서 이벤트 일치와 참여 안내를 확인", "official_page", today, entry=p.final, original=original, evidence=p.final)
        return _rec("B", kind, "공지는 확인했지만 참여 방법을 찾지 못함", "no_entry_cue", today, entry=p.final, original=original, evidence=p.final)


def _bucket(e):
    """재분석 우선순위(작을수록 먼저)."""
    url = e.get("url") or ""
    if not e.get("verification_grade") and not e.get("form_url") and not AGG_URL.search(url):
        return 1
    if AGG_URL.search(url):
        return 2
    if e.get("form_url") and not e.get("verification_evidence_url"):
        return 3
    if entry_type(url) in ("instagram", "youtube", "blog"):
        return 4
    return 5


def end_stale(e, reason, history, today):
    """지난 행사로 확인된 이벤트는 삭제하지 않고 종료 처리한다."""
    if e.get("status") == "active":
        e["status"], e["ended_reason"] = "ended", reason
        history.setdefault(e["id"], []).append({"at": today, "field": "status", "old": "active", "new": "ended", "reason": reason})


def apply_record(e, rec, history, today):
    """검증 결과를 이벤트에 반영한다. 이전 값이 바뀌면 근거와 함께 이력에 남긴다. url·form_url은 지우거나 바꾸지 않는다."""
    if rec.get("verification_code") == "stale_year":
        end_stale(e, "페이지의 기간이 지난 행사로 확인됨", history, today)
    keep_verified = e.get("verified_at")
    for k in ("entry_url", "original_url", "entry_type", "verification_grade"):
        old, new = e.get(k), rec.get(k)
        if old and old != new:
            history.setdefault(e["id"], []).append({"at": today, "field": k, "old": old, "new": new, "reason": rec.get("verification_reason")})
            history[e["id"]] = history[e["id"]][-5:]
    for k in ("entry_type", "entry_url", "original_url", "verification_grade", "verification_reason", "verification_evidence_url", "verification_status", "last_checked_at"):
        e[k] = rec.get(k)
    e["verified_at"] = (keep_verified or today) if rec["verification_grade"] == "A" else None


def run(db, store, today, cfg, renderer_cls=linktrace.Renderer):
    """예산 안에서 기한이 된 진행 중 이벤트를 검증한다. 한 건의 실패가 전체를 멈추지 않는다."""
    events = store.setdefault("events", {})
    urls = store.setdefault("urls", {})
    history = store.setdefault("history", {})
    res = {"checked": 0, "failed": 0, "requests": 0, "rendered": 0, "no_request": 0}
    limit = cfg.get("verify_pages_per_run", 120)
    t = dt.date.fromisoformat(today)
    # 연도 없는 목록을 먼 미래로 잘못 해석해 들어온 이벤트는 지난 행사다
    for e in db["events"].values():
        if e.get("status") == "active" and e.get("src") == "ki" and e.get("start") and (dt.date.fromisoformat(e["start"]) - t).days > FUTURE_DAYS:
            end_stale(e, "연도 없는 목록 표기를 미래로 잘못 해석함", history, today)
            res["future_fixed"] = res.get("future_fixed", 0) + 1
    active = [e for e in db["events"].values() if e.get("status") == "active"]
    for e in active:
        if e["id"] in events:
            apply_record(e, events[e["id"]], history, events[e["id"]].get("last_checked_at") or today)
    active = [e for e in active if e.get("status") == "active"]
    due = []
    for e in active:
        r = events.get(e["id"])
        if r and (t - dt.date.fromisoformat(r["last_checked_at"])).days < TTL[r["verification_grade"]]:
            continue
        due.append(e)
    visible = lambda e: not e.get("is_restricted") and e.get("grade") != "X"
    due.sort(key=lambda e: (not visible(e), _bucket(e), e.get("end") or "9999"))
    res["due"] = len(due)
    if limit > 0 and due:
        f = Fetcher(min_interval=cfg.get("verify_min_interval_sec", 1.5), timeout=10, retries=0)
        rend = renderer_cls(f, limit=cfg.get("verify_render_per_run", 25), cap_sec=cfg.get("verify_render_sec", 150))
        chk = Checker(f, today, urls, rend, store.setdefault("short", {}))
        t0, cap = time.time(), cfg.get("verify_sec", 240)
        try:
            for e in due:
                if chk.requests >= limit or time.time() - t0 > cap:
                    break
                before = chk.requests
                try:
                    rec = chk.verify(e)
                except Exception as x:  # 한 건의 오류는 기록만 하고 넘어간다
                    res["failed"] += 1
                    rec = _rec("B" if e.get("form_url") else "C", entry_type(e.get("url")), "검증 중 오류", "exception:" + type(x).__name__, today)
                events[e["id"]] = rec
                apply_record(e, rec, history, today)
                res["stale_ended"] = res.get("stale_ended", 0) + (rec.get("verification_code") == "stale_year")
                res["checked"] += 1
                res["no_request"] += chk.requests == before
        finally:
            rend.close()
        res["requests"], res["rendered"] = chk.requests, rend.count
    # 끝난 이벤트의 검증 기록은 남기되 URL 실패 캐시는 오래된 것부터 정리한다
    for u, c in list(urls.items()):
        if (t - dt.date.fromisoformat(c["at"])).days > 60:
            del urls[u]
    return res


def load(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"events": {}, "urls": {}, "history": {}, "short": {}}

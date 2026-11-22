# 원문 공지 읽기: 이벤트의 공지(notice) 페이지를 한 번 읽어 응모 조건에 쓸 문장만 저장하고(data/notices.json),
# 빌드 때 그 문장에서 응모 방식·발표 방법·로그인·개인정보·인증샷·구매 조건·응모 횟수를 뽑는다.
# 원칙: 근거 문장이 있을 때만 값을 쓴다. 이 이벤트의 페이지라는 근거가 없으면 문장을 쓰지 않는다.
# 예절: robots.txt 준수, 호스트별 요청 간격, 동시 호스트 수 제한, 짧은 시간 제한, 실행당 새 요청 상한, 실패해도 계속.
import datetime as dt
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from . import forms, linktrace, verify
from .common import Fetcher
from .conditions import PERSONAL, actions_of

STORE_V = 1  # 저장 형식(바꾸면 다시 읽는다)
# 약관·로그인·스크립트 때문에 읽지 않는 곳(기존 수집·검증 규칙과 같다): SNS, 로그인 전용 모음·체험단 사이트, 폼
NO_FETCH = re.compile(verify.NO_FETCH.pattern + r"|eventhouse\.kr|reviewnote\.co\.kr|mrblog\.net|pf\.kakao\.com|"
                      r"forms\.gle|docs\.google\.com|form\.naver\.com|naver\.me|walla\.my|smore\.im|typeform\.com|tally\.so", re.I)
# 실패 뒤 다시 읽기까지 기다리는 날(성공·불일치는 출처 주소가 바뀌기 전에는 다시 읽지 않는다)
RETRY_DAYS = {"error": 2, "too_many": 7, "blocked": 30, "robots": 30, "forbidden": 14, "login": 30, "not_found": 14,
              "non_html": 30, "empty": 14, "list_page": 30}
MAX_TRIES = 3  # 같은 주소로 실패한 횟수가 이만큼이면 주소가 바뀔 때까지 그만 읽는다
MAX_CHARS = 3000

# ---- 저장할 문장 고르기 ----
HEAD = re.compile(r"(?:참여|응모|신청|이벤트\s*참여)\s*(?:방법|방식|안내)|당첨(?:자)?\s*(?:발표|안내)|발표\s*안내|유의\s*사항|주의\s*사항|개인\s*정보")
KEEP = re.compile(r"참여|응모|신청|발표|당첨|추첨|로그인|회원|개인\s*정보|수집\s*항목|성명|연락처|휴대|전화\s*번호|주소|이메일|생년|"
                  r"인증|캡[처쳐]|스크린\s*샷|사진|구매|구입|결제|영수증|1인|계정|중복|매크로|자동|DM|메시지|문자|알림톡|카카오톡|"
                  r"댓글|폼|설문|퀴즈|팔로우|구독|좋아요|공유|리그램|방문|후기|리뷰|입력|기재", re.I)
SECTION_LINES = 8


def pick_lines(text):
    """페이지 글에서 응모 조건 문장만(제목 아래 절차 블록 + 핵심어가 든 줄). 메뉴 등은 버린다."""
    out, block = [], 0
    for ln in (text or "").splitlines():
        ln = NOISE.sub(" ", ln).strip()
        if not ln or len(ln) < 2:
            continue
        if HEAD.search(ln) and len(ln) <= 40:
            block = SECTION_LINES
            out.append(ln)
            continue
        keyword = KEEP.search(ln) and 4 <= len(ln) <= 300
        # 제목 아래 블록에서는 핵심어가 없어도 번호·글머리 줄이나 문장 길이의 줄은 남긴다('② 정답 적기'). 짧은 메뉴 이름은 버린다
        in_block = block > 0 and (NUM.match(ln) or len(ln) >= 15)
        if keyword or in_block:
            out.append(ln[:300])
        block -= 1
    out = list(dict.fromkeys(out))
    res, n = [], 0
    for ln in out:
        if n + len(ln) + 1 > MAX_CHARS:
            break
        res.append(ln)
        n += len(ln) + 1
    return "\n".join(res)


# ---- 어느 페이지를 읽을지 ----
def target(e):
    """(읽을 주소, 사유). 공지 주소가 읽을 수 없는 곳이면 출처 상세 글. 없으면 (None, 사유)."""
    notice = e.get("original_url") or e.get("url")
    cands = [notice, e.get("listUrl")]
    reason = "no_url"
    for u in cands:
        if not u or not str(u).startswith("http"):
            continue
        if NO_FETCH.search(u) or forms.is_form(u):
            reason = "skipped_policy"
            continue
        if verify.is_list_page(u, False):
            reason = "list_page"
            continue
        return u, None
    return None, reason


def due(e, c, today):
    """이 이벤트의 공지를 이번에 읽어야 하나."""
    url, _ = target(e)
    if not url:
        return False
    if not c or c.get("url") != url or c.get("v") != STORE_V:
        return True  # 새 이벤트, 또는 출처(공지) 주소가 바뀜
    st = c.get("status")
    if st in ("ok", "mismatch"):
        return False
    if c.get("tries", 0) >= MAX_TRIES:
        return False
    wait = RETRY_DAYS.get(st, 3)
    return (dt.date.fromisoformat(today) - dt.date.fromisoformat(c["at"])).days >= wait


def read_one(f, e, url, today):
    """한 페이지를 읽어 저장 기록을 만든다. 예외는 'error'로 돌려준다(호출한 쪽이 계속 진행)."""
    rec = {"v": STORE_V, "url": url, "at": today}
    try:
        read = re.sub(r"^https?://blog\.naver\.com/", "https://m.blog.naver.com/", url)  # PC 블로그 본문은 프레임 안
        p = linktrace.fetch_page(f, read)
    except Exception as x:
        return rec | {"status": "error", "error": type(x).__name__}
    if p.status != "ok":
        return rec | {"status": p.status}
    if p.final != read and (NO_FETCH.search(p.final) or forms.is_form(p.final)):
        return rec | {"status": "login" if linktrace.LOGIN_URL.search(p.final) else "skipped_policy"}
    if p.final != read and verify.is_list_page(p.final, False):
        return rec | {"status": "list_page"}
    if len(p.text) < 80:
        return rec | {"status": "empty"}  # 스크립트로 그리는 페이지(브라우저는 쓰지 않는다)
    idn = verify.identity(e, p)
    # 이 이벤트의 페이지라는 근거(제목 일부·경품·마감일 중 하나, 기간 충돌·지난 행사 아님)가 없으면 문장을 쓰지 않는다
    if not verify.partial(idn):
        return rec | {"status": "mismatch"}
    return rec | {"status": "ok", "final": p.final if p.final != read else None, "text": pick_lines(p.text)}


def run(db, store, today, cfg, fetcher_cls=Fetcher, reader=read_one):
    """기한이 된 진행 중 이벤트의 공지를 예산 안에서 읽는다. 한 건·한 호스트의 실패가 전체를 멈추지 않는다."""
    ev = store.setdefault("events", {})
    limit = cfg.get("notice_pages_per_run", 80)
    cap = cfg.get("notice_sec", 120)
    workers = max(1, cfg.get("notice_workers", 4))
    gap = cfg.get("notice_min_interval_sec", 2.0)
    res = {"due": 0, "fetched": 0, "ok": 0, "failed": 0, "skipped_budget": 0, "pruned": 0, "sec": 0.0}
    # 사라진 이벤트의 기록은 지운다(저장소 크기 관리)
    for k in [k for k in ev if k not in db["events"]]:
        del ev[k]
        res["pruned"] += 1
    todo = [e for e in db["events"].values() if e.get("status") == "active" and due(e, ev.get(e["id"]), today)]
    # 새로 들어온 이벤트(아직 읽은 적 없음) → 최근 등록 → 마감 가까운 순. 점수가 아니라 처리 순서일 뿐이다
    todo.sort(key=lambda e: (e["id"] in ev, -(int((e.get("firstSeen") or "0000-00-00").replace("-", "")) or 0), e.get("end") or "9999"))
    res["due"] = len(todo)
    if limit <= 0 or not todo:
        return res
    todo, rest = todo[:limit], todo[limit:]
    res["skipped_budget"] = len(rest)
    by_host = {}
    for e in todo:
        by_host.setdefault(urlparse(target(e)[0]).netloc.lower(), []).append(e)
    t0 = time.time()
    lock = threading.Lock()

    def host_job(items):
        f = fetcher_cls(min_interval=gap, timeout=10, retries=0)  # 호스트마다 하나(같은 호스트는 차례로, 간격을 둔다)
        for e in items:
            if time.time() - t0 > cap:
                with lock:
                    res["skipped_budget"] += 1
                continue
            url = target(e)[0]
            try:
                rec = reader(f, e, url, today)
            except Exception as x:
                rec = {"v": STORE_V, "url": url, "at": today, "status": "error", "error": type(x).__name__}
            with lock:
                old = ev.get(e["id"]) or {}
                if rec["status"] not in ("ok", "mismatch"):
                    rec["tries"] = (old.get("tries", 0) if old.get("url") == url else 0) + 1
                ev[e["id"]] = rec
                res["fetched"] += 1
                res["ok"] += rec["status"] == "ok"
                res["failed"] += rec["status"] in ("error", "too_many")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in [pool.submit(host_job, items) for items in by_host.values()]:
            fut.result()
    res["hosts"] = len(by_host)
    res["sec"] = round(time.time() - t0, 1)
    return res


def load(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"events": {}}


def text_for(store, e):
    """빌드에서 쓸 공지 문장과 상태. (문장 또는 None, {status, url, fetched_on})"""
    c = (store or {}).get("events", {}).get(e["id"])
    url, why = target(e)
    if not c or c.get("url") != url:
        return None, {"status": why or "pending", "url": url, "fetched_on": None}
    return (c.get("text") or None) if c.get("status") == "ok" else None, {"status": c.get("status"), "url": url, "fetched_on": c.get("at")}


# ---- 공지 문장에서 사실 뽑기 ----
HOWTO = re.compile(r"(?:참여|응모|이벤트\s*참여)\s*(?:방법|방식)\s*[:：]?\s*(.*)")
STOP_HEAD = re.compile(r"(?:응모|참여|이벤트)?\s*기간|당첨|발표|경품|상품|혜택|유의|주의|문의|개인\s*정보|대상")
NUM = re.compile(r"^\s*(?:STEP\s*\d+|\d+\s*[.)]|[①-⑩]|[-·•*▶▷►→✔✅☑]|\(\d+\))\s*", re.I)
# 사이트 화면 문구(댓글 위젯의 '로그인이 필요합니다', 푸터의 '이메일무단수집거부' 등)는 이벤트 조건이 아니다
NOISE = re.compile(r"[^\n]{0,30}로그인이\s*필요(?:합니다|해요)?\.?|이메일\s*(?:무단\s*)?수집\s*거부|무단\s*수집\s*거부|"
                   r"구독\s*신청을\s*취소[^\n]*|로그인이\s*필요\s*없으신[^\n]*")
# 참여 행동과 이어진 로그인만(예: '로그인 후 참여', '시청(로그인 필수)'). 화면 안내 '로그인이 필요합니다'만으로는 쓰지 않는다
LOGIN_YES = re.compile(r"로그인\s*(?:후|하여|해서|하신\s*후|하고)\s*[^\n.]{0,15}(?:참여|응모|신청|투표|시청|이용|작성|댓글)|"
                       r"(?:참여|응모|신청|투표|시청)[^\n.]{0,20}[(\[]\s*로그인\s*(?:필수|필요)|"
                       r"회원\s*가입\s*(?:후|필요|필수)|(?:로그인|회원)\s*(?:한\s*)?(?:고객|회원)\s*(?:만|대상)|회원\s*(?:만|전용)\s*(?:참여|응모|신청)")
LOGIN_NO = re.compile(r"비회원(?:도|으로도?)?\s*(?:참여|응모|신청)\s*가능|로그인\s*(?:없이|불필요|필요\s*없)|회원\s*가입\s*(?:없이|불필요)")
FIELD_CTX = re.compile(r"입력|기재|수집|제출|남겨|적어|항목|정확히")
FIELD_MIN = 10  # 이보다 짧은 줄('상세주소입력' 같은 화면 이름표)은 입력 항목 근거로 쓰지 않는다
PROOF = re.compile(r"(?:인증\s*샷|인증\s*사진|캡[처쳐]|스크린\s*샷|영수증)[^\n]{0,25}(?:첨부|업로드|제출|등록|올려|남겨|필수|보내|인증)|"
                   r"(?:첨부|업로드|제출)[^\n]{0,10}(?:인증\s*샷|캡[처쳐]|스크린\s*샷)")
PURCHASE = re.compile(r"(?:구매|구입|결제|주문)\s*(?:후|하신|한|완료)[^\n]{0,15}(?:응모|참여|신청|추첨)|"
                      r"(?:구매|구입|결제)\s*(?:고객|회원)\s*(?:대상|한정|만)|(?:구매|결제)\s*(?:내역|영수증)\s*(?:인증|첨부|제출)|"
                      r"(?:원\s*이상)\s*(?:구매|결제)\s*시")


# 절차 문장으로 볼 동사·행동(제목·표 머리·공유 위젯 줄을 거른다)
STEP_VERB = re.compile(r"하기|하고|하면|하세요|합니다|해\s*주세요|해주세요|후|남기|남겨|작성|참여|제출|팔로우|구독|시청|입력|클릭|접속|응모|완료|태그|찍|올리|풀")
STEP_NOISE = re.compile(r"이벤트\s*!?$|링크\s*(?:만들기|복사)|^※|당첨|발표|선정")
# 공지의 발표 방법은 발표·당첨자 연락을 말하는 문장에서만(‘당첨 확률 UP’, ‘미정’ 같은 문장은 제외)
ANN_SENT = re.compile(r"[^\n.!?]*(?:발표|당첨(?:자|\s*시|되신|\s*안내)|개별\s*(?:안내|연락|통보))[^\n.!?]*")
ANN_SKIP = re.compile(r"확률|미정|정하지|합산|사용기|후기|예정일|있는지\s*확인")


def announce(text):
    """공지 문장의 발표 방법(dm·comment·notice·sms·email)과 근거 문장. 없으면 ([], None)."""
    from .agentfeed import _ANN
    for m in ANN_SENT.finditer(NOISE.sub(" ", text or "")):
        sent = m.group(0)
        if ANN_SKIP.search(sent):
            continue
        found = [k for k, rx in _ANN if rx.search(sent)]
        if found:
            return found, sent.strip()[:100]
    return [], None


def _quote(text, m, width=60):
    """근거 문장(앞뒤 조금). 피드에서 사람이·봇이 바로 확인할 수 있게 짧게."""
    s = max(0, m.start() - 15)
    return re.sub(r"\s+", " ", text[s:m.end() + 25]).strip()[:width + 40]


def notice_steps(text):
    """'참여 방법' 블록의 절차 문장. 행동 종류가 있는 줄만, 최대 6개. 없으면 []."""
    lines = (text or "").splitlines()
    out = []
    for i, ln in enumerate(lines):
        m = HOWTO.search(ln)
        if not m:
            continue
        ok = lambda x: x and actions_of(x) and STEP_VERB.search(x) and not STEP_NOISE.search(x) and 6 <= len(x) <= 160
        rest = NUM.sub("", m.group(1)).strip()
        if ok(rest):
            out.append(rest)
        for nxt in lines[i + 1:i + 1 + SECTION_LINES]:
            if HOWTO.search(nxt) or (STOP_HEAD.search(nxt) and len(nxt) <= 25 and not actions_of(nxt)):
                break
            s = NUM.sub("", nxt).strip()
            if ok(s):
                out.append(s)
        if out:
            break
    return list(dict.fromkeys(out))[:6]


def facts(text):
    """공지 문장 → 사실과 근거. 근거가 없으면 해당 키 없음(=모름)."""
    out, ev = {}, []
    if not text:
        return out, ev
    text = NOISE.sub(" ", text)

    def note(field, value, m):
        out[field] = value
        ev.append({"field": field, "value": value, "source": "notice", "quote": _quote(text, m)})

    m = LOGIN_NO.search(text)
    if m:
        note("login_required", False, m)
    m = LOGIN_YES.search(text)
    if m:
        note("login_required", True, m)  # 둘 다 있으면 필요 쪽(보수적)
    fields = []
    quote = None
    for ln in text.splitlines():
        if len(ln.strip()) >= FIELD_MIN and FIELD_CTX.search(ln):
            new = [k for k, rx in PERSONAL if rx.search(ln) and k not in fields]
            if new and quote is None:
                quote = ln.strip()[:100]
            fields += new
    if fields:
        out["form_fields"] = fields
        ev.append({"field": "form_fields", "value": fields, "source": "notice", "quote": quote})
    m = PROOF.search(text)
    if m:
        note("proof_required", True, m)
    m = PURCHASE.search(text)
    if m:
        note("purchase_required", True, m)
    st = notice_steps(text)
    if st:
        out["steps"] = st
        ev.append({"field": "steps", "value": len(st), "source": "notice", "quote": st[0][:100]})
    return out, ev

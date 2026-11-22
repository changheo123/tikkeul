# AI 에이전트용 피드(_site/feed/). 화면 HTML·JS를 읽지 않고 이벤트를 받아 가도록 고정 스키마로 낸다.
# 원칙: 근거가 있을 때만 값을 쓴다. 모르면 null(조건) 또는 "unknown"(분류). '없음'을 추측하지 않는다.
# 필드 이름·의미·열거값은 schema_version의 주 번호(2.x) 안에서 바꾸거나 지우지 않는다(추가만 한다).
import datetime as dt
import re
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from .conditions import conditions, steps
from .enrich import prize_items

SCHEMA_VERSION = "2.0"
TZ = "+09:00"
NEW_DAYS = 30  # feed/new/에 남기는 일자 파일 수
ATOM_MAX = 100

ENTRY_METHODS = [
    "instagram_comment", "instagram_follow", "instagram_like", "instagram_share", "instagram_dm",
    "x_repost", "x_follow", "x_reply",
    "youtube_comment", "youtube_subscribe", "youtube_watch", "youtube",
    "blog_comment", "blog_follow", "blog_post",
    "naver_form", "google_form", "other_form", "web_form",
    "other", "unknown",
]
ANNOUNCE_METHODS = ["dm", "comment", "notice", "sms", "email", "unknown"]

# ---- 응모 방식 ----
_PLAT_WORD = [("insta", r"인스타|instagram|릴스|스토리|리그램"), ("yt", r"유튜브|youtube|구독"),
              ("x", r"(?<![A-Za-z])X(?![A-Za-z])|트위터|twitter|리트윗|리포스트|재게시"), ("naver", r"블로그|카페|이웃")]
_PLAT_WORD = [(k, re.compile(p, re.I)) for k, p in _PLAT_WORD]
_SNS = ("insta", "yt", "x", "naver")
_MAP = {
    "insta": {"comment": "instagram_comment", "tag_friend": "instagram_comment", "follow": "instagram_follow",
              "like": "instagram_like", "share": "instagram_share", "dm": "instagram_dm"},
    "x": {"share": "x_repost", "follow": "x_follow", "comment": "x_reply", "tag_friend": "x_reply"},
    "yt": {"comment": "youtube_comment", "tag_friend": "youtube_comment", "follow": "youtube_subscribe", "watch": "youtube_watch"},
    "naver": {"comment": "blog_comment", "tag_friend": "blog_comment", "follow": "blog_follow", "share": "blog_post", "review": "blog_post"},
}
_KIND_ACTION = {"댓글": "comment"}  # 절차가 없을 때만 쓰는 약한 근거(출처가 붙인 응모 형태)


def form_kind(url):
    """폼 주소 → naver_form·google_form·other_form. 폼이 아니면 None."""
    if not url:
        return None
    h = urlparse(url).netloc.lower()
    p = urlparse(url).path
    if h.endswith("form.naver.com") or h == "naver.me":
        return "naver_form"
    if (h == "docs.google.com" and p.startswith("/forms")) or h == "forms.gle":
        return "google_form"
    if re.search(r"moaform\.com|smore\.im|typeform\.com|tally\.so|walla\.my|surveymonkey", h):
        return "other_form"
    return None


def _step_platform(text, sns):
    """절차 문장의 플랫폼: 문장에 적혀 있으면 그것, 아니면 이벤트의 SNS가 하나일 때만 그것. 모르면 None."""
    named = [k for k, rx in _PLAT_WORD if rx.search(text)]
    named = [k for k in named if k in sns] or named
    if len(named) == 1:
        return named[0]
    return sns[0] if len(sns) == 1 and not named else None


def entry_methods(e, st, entry):
    sns = [p for p in e.get("plats") or [] if p in _SNS]
    out = []
    for s in st:
        plat = _step_platform(s["text"], sns)
        s["platform"] = {"insta": "instagram", "yt": "youtube", "x": "x", "naver": "naver_blog"}.get(plat)
        for a in s["actions"]:
            m = _MAP.get(plat, {}).get(a)
            if m:
                out.append(m)
            elif a in ("form", "quiz", "survey") and plat is None:
                out.append("web_form")
    for u in (e.get("form_url"), entry.get("url") if entry.get("type") == "form" else None):
        k = form_kind(u)
        if k:
            out.append(k)
    if not st:
        act = _KIND_ACTION.get(e.get("kind"))
        if act and len(sns) == 1:
            out.append(_MAP[sns[0]][act])
        elif sns == ["yt"]:
            out.append("youtube")
    if not out and st:
        out.append("other")  # 절차는 있지만 위 분류에 해당하지 않음
    out = list(dict.fromkeys(out))
    if "web_form" in out and any(m in out for m in ("naver_form", "google_form", "other_form")):
        out.remove("web_form")  # 폼 종류를 알면 그쪽으로
    return out or ["unknown"]


# ---- 당첨 발표 방법 ----
_ANN_CTX = re.compile(r"[^\n.!?]*(?:당첨|발표|개별\s*(?:안내|연락|통보)|선정\s*(?:안내|연락))[^\n.!?]*")
_ANN = [
    ("dm", r"(?<![A-Za-z])DM(?![A-Za-z])|다이렉트\s*메시지|개별\s*메시지|쪽지"),
    ("comment", r"댓글로|대댓글|답글로|댓글\s*(?:을\s*)?(?:통해|달아)"),
    ("notice", r"공지|게시판|홈페이지|누리집|게시물\s*(?:에|을|로|통해)|피드|스토리|본문\s*(?:에|수정)|채널\s*(?:에|커뮤니티)|커뮤니티"),
    ("sms", r"문자|SMS|MMS|LMS|알림톡|카카오톡|카톡"),
    ("email", r"이메일|e-?mail|메일로|메일\s*(?:발송|안내)"),
]
_ANN = [(k, re.compile(p, re.I)) for k, p in _ANN]


def announce_methods(text):
    ctx = " ".join(m.group(0) for m in _ANN_CTX.finditer(text or ""))
    out = [k for k, rx in _ANN if rx.search(ctx)]
    return out or ["unknown"]


# ---- 제한(1인 응모 횟수·SNS 계정 조건) ----
# 응모 횟수로 읽는 것은 '1인/계정당 N회 + 참여·응모·신청'일 때만('1인 1회 제공'·'당첨은 1인 1회'는 아님)
_PER_N = re.compile(r"(?:1인|1\s*계정|계정|ID|아이디)\s*당?\s*(?:최대\s*)?(\d{1,2})\s*회\s*(?:만|까지)?\s*(?:참여|응모|신청)")
_PER_DAY = re.compile(r"(?:하루|1일|매일)\s*(?:최대\s*)?(\d{1,2})\s*회\s*(?:만|까지)?\s*(?:참여|응모|신청)")
_DUP_ENTRY_NO = re.compile(r"중복\s*(?:응모|참여)\s*(?:불가|불가능|제한|안\s*됨|안됩니다|시\s*제외|금지)")
_DUP_ENTRY_OK = re.compile(r"중복\s*(?:응모|참여)\s*(?:가능|허용|OK)|여러\s*번\s*(?:응모|참여)\s*가능|매일\s*(?:응모|참여)\s*가능")
_DUP_WIN_NO = re.compile(r"중복\s*당첨\s*(?:불가|불가능|제한|안\s*됨|없음|안됩니다|시\s*제외)")
_DUP_WIN_OK = re.compile(r"중복\s*당첨\s*(?:가능|허용)")
_PUBLIC = re.compile(r"비공개\s*계정[^\n.]{0,15}(?:제외|불가|당첨\s*취소|응모\s*불가|인정\s*안)|공개\s*계정(?:으로|만|이어야|필수)")
_FOLLOWERS = re.compile(r"팔로워\s*(?:수\s*)?(\d[\d,]*)\s*(?:명|인)?\s*이상")
_SUB_ACCT = re.compile(r"(?:부계정|이벤트\s*(?:전용|용)\s*계정|체리피커|유령\s*계정|신규\s*계정)[^\n.]{0,20}(?:제외|불가|취소|당첨\s*제외)")
# 자동화 금지 문구만('자동 응모 처리됩니다'처럼 시스템이 접수한다는 뜻, 일반적인 '부정 응모'는 제외)
_NO_MACRO = re.compile(r"매크로|자동화?\s*프로그램|(?:자동|봇|bot)\s*(?:으로\s*)?(?:응모|참여)[^\n.]{0,12}(?:금지|불가|제외|취소|무효)", re.I)


def _tri(text, yes, no):
    return True if yes.search(text) else False if no.search(text) else None


def limits(e, text, cond):
    m = _PER_N.search(text)
    per = int(m.group(1)) if m else (1 if _DUP_ENTRY_NO.search(text) else None)
    d = _PER_DAY.search(text)
    fm = _FOLLOWERS.search(text)
    return {
        "per_person_limit": per,
        "per_day_limit": int(d.group(1)) if d else None,
        "duplicate_entry_allowed": _tri(text, _DUP_ENTRY_OK, _DUP_ENTRY_NO),
        "duplicate_win_allowed": _tri(text, _DUP_WIN_OK, _DUP_WIN_NO),
        "sns_account": {
            "required": cond.get("sns_account_required"),
            "platforms": cond.get("sns_platforms") or [],
            "public_account_required": True if _PUBLIC.search(text) else None,
            "min_followers": int(fm.group(1).replace(",", "")) if fm else None,
            "event_only_account_excluded": True if _SUB_ACCT.search(text) else None,
        },
    }


# ---- 자동 응모 판단 ----
def auto_ok(e, cond, entry, methods, text):
    """(값, 막는 이유 목록). true=사람 계정·로그인·인증 없이 검증된 폼 주소로 응모가 끝나는 것이 확인됨.
    false=자동 응모를 막는 조건이 확인됨. null=판단할 근거가 부족함. 이벤트 약관 허락을 뜻하지 않는다."""
    blockers = []
    if _NO_MACRO.search(text):
        blockers.append("terms_forbid_automation")
    if cond.get("login_required") or cond.get("signup_required"):
        blockers.append("login_required")
    if cond.get("sns_account_required") or any(m.split("_")[0] in ("instagram", "x", "youtube", "blog") for m in methods):
        blockers.append("sns_action_required")
    if cond.get("proof_required") or cond.get("photo_required"):
        blockers.append("proof_or_photo_required")
    if cond.get("visit_required"):
        blockers.append("visit_required")
    if cond.get("review_required"):
        blockers.append("review_required")
    if (cond.get("eligibility") or {}).get("restricted"):
        blockers.append("eligibility_restricted")
    if blockers:
        return False, blockers
    form = bool(entry.get("form_kind"))  # 검증된 응모 주소 자체가 폼이어야 한다
    if entry.get("verified") and form and cond.get("steps_known"):
        return True, []
    missing = []
    if not entry.get("verified"):
        missing.append("entry_url_not_verified")
    if not form:
        missing.append("not_a_form")
    if not cond.get("steps_known"):
        missing.append("steps_unknown")
    return None, missing


# ---- 날짜 ----
def _at(d, time=None, end=False):
    if not d:
        return None
    t = time if time and re.fullmatch(r"\d{2}:\d{2}", time) else ("23:59" if end else "00:00")
    return f"{d}T{t}:{'59' if end and not time else '00'}{TZ}"


def added(e):
    on = e.get("firstSeen")
    return on, e.get("addedAt")  # addedAt은 등록 시각 기록을 시작한 뒤 들어온 이벤트에만 있다


def item(e, raw_text, aliases):
    text = " ".join([e.get("title") or "", *(e.get("steps") or []), *(e.get("needs") or []), raw_text or ""])
    st = steps(e)
    cond = conditions(dict(e, rawText=raw_text))
    g = e.get("verification_grade")
    eurl = e.get("entry_url") if g == "A" else e.get("candidate_url") if g == "B" else None
    entry = {"url": eurl, "type": e.get("entry_type") if eurl else None, "grade": g, "verified": g == "A",
             "form_kind": form_kind(eurl), "reason": e.get("verification_reason")}
    methods = entry_methods(e, st, entry)
    ok, why = auto_ok(e, cond, entry, methods, text)
    on, at = added(e)
    items = prize_items(e)
    notice = e.get("original_url") or e.get("url")
    return {
        "id": e["id"],
        "merged_ids": sorted(aliases.get(e["id"], [])),
        "status": "ended" if e.get("status") == "ended" else "active",
        "added_on": on,
        "added_at": at,
        "title": e.get("title"),
        "organizer": e.get("org"),
        "kind": e.get("kind"),
        "entry_methods": methods,
        "auto_ok": ok,
        "auto_blockers": why,
        "entry_url": eurl,
        "entry": entry,
        "notice_url": notice,
        "source_url": e.get("listUrl") if e.get("listUrl") and e.get("listUrl") != notice else None,
        "prize": {
            "name": items[0] if items else None,
            "items": items,
            "categories": e.get("pcats") or [],
            "value_krw": e.get("value"),
            "winners": e.get("count"),
            "raw": e.get("prize"),
        },
        "starts_at": _at(e.get("start")),
        "ends_at": _at(e.get("end"), e.get("endTime"), end=True),
        "ends_at_time_known": bool(e.get("end") and e.get("endTime")),
        "announce_at": _at(e.get("announce")),
        "announce_methods": announce_methods(text),
        "selection": e.get("mode"),
        "limits": limits(e, text, cond),
        "steps": st,
        "conditions": cond,
    }


def build(events, raw, aliases, site, today, generated_at):
    """events: build.py의 enrich된 이벤트(진행 중+종료). 반환: {상대 경로: 내용(dict 또는 str)}"""
    live = [x for x in events if x.get("status") != "ended" and (not x.get("end") or x["end"] >= today)]
    rows = {x["id"]: item(x, raw.get(x["id"]), aliases) for x in events}
    head = {"schema": "tikkeul.agent-feed", "schema_version": SCHEMA_VERSION, "schema_url": site + "feed/schema.json",
            "docs_url": site + "feed/README.md", "generated_at": generated_at, "timezone": "Asia/Seoul", "site": site}
    feed_live = sorted((rows[x["id"]] for x in live), key=lambda r: (r["ends_at"] or "9999", r["id"]))
    out = {"feed/events.json": head | {"count": len(feed_live), "events": feed_live}}
    start = dt.date.fromisoformat(today) - dt.timedelta(days=NEW_DAYS - 1)
    days = []
    for i in range(NEW_DAYS):
        d = (start + dt.timedelta(days=i)).isoformat()
        evs = sorted((r for r in rows.values() if r["added_on"] == d), key=lambda r: (r["added_at"] or "", r["id"]))
        out[f"feed/new/{d}.json"] = head | {"date": d, "count": len(evs), "events": evs}
        days.append({"date": d, "count": len(evs), "url": f"{site}feed/new/{d}.json"})
    out["feed/new/index.json"] = head | {"days": days[::-1]}
    newest = sorted(rows.values(), key=lambda r: (r["added_at"] or (r["added_on"] or "") + "T00:00:00" + TZ, r["id"]), reverse=True)[:ATOM_MAX]
    out["feed/atom.xml"] = atom(newest, site, generated_at)
    out["feed/schema.json"] = SCHEMA
    return out


def atom(rows, site, generated_at):
    host = urlparse(site).netloc
    parts = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="ko">',
             "<title>티끌모아 티끌 – 새 이벤트</title>", f"<id>{escape(site)}feed/atom.xml</id>",
             f'<link rel="self" href="{escape(site)}feed/atom.xml"/>', f'<link rel="alternate" href="{escape(site)}"/>',
             f"<updated>{generated_at}</updated>", "<author><name>티끌모아 티끌</name></author>"]
    for r in rows:
        when = r["added_at"] or f"{r['added_on']}T00:00:00{TZ}"
        summ = " / ".join(x for x in [r["prize"]["name"], f"마감 {r['ends_at'][:10]}" if r["ends_at"] else None,
                                       "응모: " + ",".join(r["entry_methods"])] if x)
        parts += ["<entry>", f"<id>tag:{host},2026:event/{escape(r['id'])}</id>", f"<title>{escape(r['title'] or r['id'])}</title>",
                  f'<link rel="alternate" href="{escape(r["notice_url"])}"/>',
                  *([f'<link rel="related" title="entry" href="{escape(r["entry_url"])}"/>'] if r["entry_url"] else []),
                  f"<published>{when}</published>", f"<updated>{when}</updated>",
                  f"<summary>{escape(summ)}</summary>", f"<category term=\"{escape(r['status'])}\"/>", "</entry>"]
    parts.append("</feed>")
    return "\n".join(parts) + "\n"


# ---- JSON Schema ----
_S = {"type": ["string", "null"]}
_B = {"type": ["boolean", "null"]}
_I = {"type": ["integer", "null"]}
_DT = {"type": ["string", "null"], "format": "date-time", "description": "ISO 8601, 한국 시간(+09:00)"}
EVENT = {
    "type": "object",
    "required": ["id", "status", "added_on", "added_at", "title", "entry_methods", "auto_ok", "entry_url", "notice_url",
                 "prize", "starts_at", "ends_at", "announce_at", "announce_methods", "limits", "conditions"],
    "properties": {
        "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$",
               "description": "고유·불변 ID. 자동 수집분은 '출처 코드-출처 글 키의 SHA-1 앞 10자', 사람 확인 장부분은 장부 ID. "
                              "처음 등록될 때 정해지고 원문·응모 주소가 바뀌어도 그대로다"},
        "merged_ids": {"type": "array", "items": {"type": "string"}, "description": "같은 이벤트로 합쳐진 다른 출처의 ID(예전에 받은 ID 대조용)"},
        "status": {"enum": ["active", "ended"]},
        "added_on": {"type": ["string", "null"], "format": "date", "description": "이 사이트에 처음 등록된 날짜(한국 날짜). feed/new/ 파일 기준"},
        "added_at": {**_DT, "description": "처음 등록된 시각. 시각 기록 이전(2026-11-22 이전) 등록분은 null"},
        "title": {"type": "string"}, "organizer": _S, "kind": _S,
        "entry_methods": {"type": "array", "minItems": 1, "items": {"enum": ENTRY_METHODS},
                          "description": "응모 방식(복수). 근거가 없으면 [\"unknown\"]. web_form은 폼 종류를 모르는 웹 신청·퀴즈·설문"},
        "auto_ok": {**_B, "description": "true: 검증된 폼 주소로 로그인·SNS·인증 없이 응모가 끝남이 확인됨. false: 막는 조건이 확인됨. null: 근거 부족. 약관상 허락을 뜻하지 않는다"},
        "auto_blockers": {"type": "array", "items": {"enum": ["terms_forbid_automation", "login_required", "sns_action_required", "proof_or_photo_required",
                                                              "visit_required", "review_required", "eligibility_restricted",
                                                              "entry_url_not_verified", "not_a_form", "steps_unknown"]},
                          "description": "auto_ok가 false면 막는 조건, null이면 부족한 근거"},
        "entry_url": {**_S, "description": "실제 응모 주소(폼·게시물). 검증 등급 A·B일 때만, 아니면 null"},
        "entry": {"type": "object", "properties": {
            "url": _S, "type": {"enum": ["form", "website", "instagram", "youtube", "blog", "other", "unknown", None]},
            "grade": {"enum": ["A", "B", "C", None], "description": "A=공식 공지와 연결 확인, B=근거 있으나 미확인, C=응모 주소 미확보"},
            "verified": {"type": "boolean"}, "form_kind": {"enum": ["naver_form", "google_form", "other_form", None]}, "reason": _S}},
        "notice_url": {"type": "string", "format": "uri", "description": "이벤트 공지(원문) 주소"},
        "source_url": {**_S, "description": "이 이벤트를 찾은 모음 사이트 글 주소(원문과 다를 때)"},
        "prize": {"type": "object", "properties": {
            "name": _S, "items": {"type": "array", "items": {"type": "string"}}, "categories": {"type": "array", "items": {"type": "string"}},
            "value_krw": {**_I, "description": "경품명에 적힌 금액 중 최대(원). 적혀 있지 않으면 null"},
            "winners": {**_I, "description": "총 당첨 인원"}, "raw": _S}},
        "starts_at": {**_DT, "description": "응모 시작. 시각을 모르면 그날 00:00:00"},
        "ends_at": {**_DT, "description": "응모 마감. 시각을 모르면 그날 23:59:59(ends_at_time_known=false)"},
        "ends_at_time_known": {"type": "boolean"},
        "announce_at": {**_DT, "description": "당첨 발표일. 시각을 모르면 00:00:00"},
        "announce_methods": {"type": "array", "minItems": 1, "items": {"enum": ANNOUNCE_METHODS},
                             "description": "당첨 발표·연락 방법. sms에는 알림톡·카카오톡 포함. 근거 없으면 [\"unknown\"]"},
        "selection": _S,
        "limits": {"type": "object", "properties": {
            "per_person_limit": {**_I, "description": "1인(계정) 응모 가능 횟수. 1인 1회=1"},
            "per_day_limit": _I, "duplicate_entry_allowed": _B, "duplicate_win_allowed": _B,
            "sns_account": {"type": "object", "properties": {
                "required": _B, "platforms": {"type": "array", "items": {"type": "string"}},
                "public_account_required": _B, "min_followers": _I, "event_only_account_excluded": _B}}}},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "order": {"type": "integer"}, "text": {"type": "string"}, "actions": {"type": "array", "items": {"type": "string"}},
            "platform": {"enum": ["instagram", "youtube", "x", "naver_blog", None]}}}},
        "conditions": {"type": "object", "description": "참여 조건. 값이 null이면 확인 안 됨(없음이 아님). 키 목록은 data/feed.schema.json의 conditions와 같다"},
    },
}
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "tikkeul.agent-feed/" + SCHEMA_VERSION,
    "title": "티끌모아 티끌 에이전트 피드",
    "description": "무료 경품 이벤트. null=확인 안 됨, \"unknown\"=분류 근거 없음. 2.x 안에서는 필드를 추가만 하고 바꾸거나 지우지 않는다.",
    "type": "object",
    "required": ["schema", "schema_version", "generated_at"],
    "properties": {
        "schema": {"const": "tikkeul.agent-feed"},
        "schema_version": {"type": "string", "pattern": "^2\\."},
        "generated_at": {"type": "string", "format": "date-time"},
        "date": {"type": "string", "format": "date", "description": "feed/new/YYYY-MM-DD.json에서만"},
        "count": {"type": "integer"},
        "events": {"type": "array", "items": {"$ref": "#/$defs/event"}},
        "days": {"type": "array", "description": "feed/new/index.json에서만"},
    },
    "$defs": {"event": EVENT},
}

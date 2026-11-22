# 기계용 피드(data/feed.json)와 스키마(data/feed.schema.json). 필드 이름과 의미는 버전 안에서 바꾸지 않는다
import datetime as dt

from .conditions import conditions, steps
from .enrich import prize_items

SCHEMA_VERSION = "1.0"
GRADE_POLICY = {
    "A": "공식 공지와 응모 주소의 연결이 확인됨(entry.verified=true)",
    "B": "이벤트와 연결된 근거는 있지만 로그인·SNS 제한 등으로 확인이 끝나지 않음(entry.verified=false)",
    "C": "응모 주소를 확인하지 못했거나 오류(entry.url=null, notice_url만 사용)",
}
NULL_RULE = "조건 값이 null이면 확인되지 않았다는 뜻이며 '없음'이 아니다. true/false는 수집한 글에 근거가 있을 때만 쓴다."


def _entry(e):
    g = e.get("verification_grade")
    url = e.get("entry_url") if g == "A" else e.get("candidate_url") if g == "B" else None
    return {"url": url, "type": e.get("entry_type") if url else None, "grade": g, "verified": g == "A",
            "destination_checked": bool(e.get("destination_checked")) if url else None, "reason": e.get("verification_reason")}


def item(e, today):
    end = e.get("end")
    return {
        "id": e["id"],
        "title": e.get("title"),
        "organizer": e.get("org"),
        "source": e.get("src"),
        "kind": e.get("kind"),
        "platforms": e.get("plats") or [],
        "listed_by_default": e.get("is_restricted") is not True and e.get("grade") != "X",
        "prize": {"name": e.get("card"), "items": prize_items(e), "categories": e.get("pcats") or [], "winners": e.get("count"),
                  "max_value_krw": e.get("value"), "raw": e.get("prize")},
        "period": {"start": e.get("start"), "end": end, "end_time": e.get("endTime"), "announce": e.get("announce"), "timezone": "Asia/Seoul",
                   "days_left": (dt.date.fromisoformat(end) - dt.date.fromisoformat(today)).days if end else None},
        "entry": _entry(e),
        "notice_url": e.get("original_url") or e.get("url"),
        "steps": steps(e),
        "conditions": conditions(e),
        "first_seen": e.get("firstSeen"),
    }


def build(live, meta, site, today, generated_at):
    events = [item(e, today) for e in live]
    return {
        "schema": "tikkeul.feed", "schema_version": SCHEMA_VERSION, "schema_url": site + "data/feed.schema.json",
        "generated_at": generated_at, "timezone": "Asia/Seoul", "site": site, "count": len(events),
        "grade_policy": GRADE_POLICY, "null_rule": NULL_RULE,
        "sources": [{"name": s["label"], "status": s["st"], "last_ok": s.get("last")} for s in meta.get("sources", []) if s.get("key")],
        "events": events,
    }


_TRI = {"type": ["boolean", "null"]}
_STR = {"type": ["string", "null"]}
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "티끌모아 티끌 이벤트 피드",
    "description": "진행 중인 무료 경품 이벤트. " + NULL_RULE,
    "type": "object",
    "required": ["schema", "schema_version", "generated_at", "count", "events"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "generated_at": {"type": "string", "description": "생성 시각(한국 시간, ISO 8601)"},
        "count": {"type": "integer"},
        "grade_policy": {"type": "object"},
        "sources": {"type": "array", "description": "출처별 수집 상태(status: on·err·off·no·ledger)"},
        "events": {"type": "array", "items": {
            "type": "object",
            "required": ["id", "title", "prize", "period", "entry", "notice_url", "steps", "conditions"],
            "properties": {
                "id": {"type": "string", "description": "바뀌지 않는 이벤트 ID"},
                "title": {"type": "string"}, "organizer": _STR, "source": _STR, "kind": _STR,
                "platforms": {"type": "array", "items": {"type": "string"}},
                "listed_by_default": {"type": "boolean", "description": "사이트 기본 목록 노출 여부(참여 제한·조건 많음이면 false)"},
                "prize": {"type": "object", "properties": {"name": _STR, "items": {"type": "array"}, "categories": {"type": "array"},
                                                            "winners": {"type": ["integer", "null"]}, "max_value_krw": {"type": ["integer", "null"]}, "raw": _STR}},
                "period": {"type": "object", "properties": {"start": _STR, "end": _STR, "end_time": _STR, "announce": _STR,
                                                             "timezone": {"type": "string"}, "days_left": {"type": ["integer", "null"]}}},
                "entry": {"type": "object", "properties": {
                    "url": _STR, "type": {"enum": ["form", "website", "instagram", "youtube", "blog", "other", "unknown", None]},
                    "grade": {"enum": ["A", "B", "C", None]}, "verified": {"type": "boolean"}, "destination_checked": _TRI, "reason": _STR}},
                "notice_url": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object", "properties": {
                    "order": {"type": "integer"}, "text": {"type": "string"},
                    "actions": {"type": "array", "items": {"enum": ["follow", "like", "comment", "share", "tag_friend", "quiz", "survey", "form", "proof",
                                                                    "photo", "watch", "visit", "dm", "signup", "login", "review"]}}}}},
                "conditions": {"type": "object", "properties": {
                    **{k: _TRI for k in ("login_required", "signup_required", "sns_account_required", "follow_required", "like_required", "comment_required",
                                          "share_required", "tag_friend_required", "quiz", "survey", "form_required", "photo_required", "proof_required",
                                          "watch_required", "review_required", "visit_required", "one_entry_per_person", "free_confirmed", "tax_possible")},
                    "steps_known": {"type": "boolean"}, "sns_platforms": {"type": "array"}, "proof_detail": _STR,
                    "personal_info": {"type": ["array", "null"], "items": {"enum": ["name", "phone", "address", "email", "birth", "sns_id"]}},
                    "eligibility": {"type": "object"}, "selection": _STR, "winners": {"type": ["integer", "null"]},
                    "time_minutes": {"type": ["object", "null"]}}},
                "first_seen": _STR,
            }}},
    },
}

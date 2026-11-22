# 에이전트 피드(feed/) 규칙 테스트
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import agentfeed as A
from collector.enrich import enrich

SITE = "https://example.github.io/tikkeul/"


def ev(**kw):
    base = {"id": "t-0123456789", "title": "테스트 이벤트", "org": "주최", "kind": "댓글", "tags": [], "reasons": [], "needs": [], "steps": [],
            "prize": "스타벅스 아메리카노 10명", "count": 10, "start": "2026-11-20", "end": "2026-11-30", "endTime": None, "announce": "2026-12-05",
            "url": "https://www.instagram.com/p/abc/", "src": "t", "status": "active", "firstSeen": "2026-11-22", "cand": True,
            "is_restricted": False, "needs_proof": False, "verification_grade": "C", "entry_url": None, "candidate_url": None}
    base.update(kw)
    return enrich(base)


def test_entry_methods_insta_steps():
    r = A.item(ev(steps=["계정 팔로우", "댓글로 친구 태그"]), None, {})
    assert r["entry_methods"] == ["instagram_follow", "instagram_comment"]
    assert r["steps"][0]["platform"] == "instagram"


def test_entry_methods_form_kinds_and_unknown():
    r = A.item(ev(url="https://example.com/ev", steps=["퀴즈 풀기", "폼 제출"], form_url="https://forms.gle/abc",
                  verification_grade="A", entry_url="https://forms.gle/abc", entry_type="form"), None, {})
    assert r["entry_methods"] == ["google_form"]  # 폼 종류를 알면 web_form은 빼다
    assert A.form_kind("https://form.naver.com/response/x") == "naver_form"
    assert A.form_kind("https://example.com/") is None
    # 절차도 폼도 없고 플랫폼이 웹뿐이면 unknown(추측하지 않음)
    assert A.item(ev(url="https://example.com/ev", kind="기타"), None, {})["entry_methods"] == ["unknown"]


def test_mixed_platform_step_not_guessed():
    # 인스타·유튜브가 함께인데 문장에 플랫폼이 없으면 플랫폼별 값으로 나누지 않는다
    e = ev(url="https://www.instagram.com/p/x/", srcUrls=[], platHint="yt", steps=["댓글 작성"])
    e["plats"] = ["insta", "yt"]
    assert A.item(e, None, {})["entry_methods"] == ["other"]
    e["steps"] = ["유튜브 댓글 작성"]
    assert A.item(e, None, {})["entry_methods"] == ["youtube_comment"]


def test_announce_methods():
    assert A.announce_methods("당첨자는 인스타그램 DM으로 개별 안내") == ["dm"]
    assert A.announce_methods("당첨자 발표는 공지사항 게시판에서") == ["notice"]
    assert A.announce_methods("당첨 시 문자로 연락드립니다") == ["sms"]
    assert A.announce_methods("댓글 남기면 추첨") == ["unknown"]


def test_limits():
    r = A.item(ev(), "계정당 1회 응모 가능합니다. 비공개 계정은 추첨에서 제외돼요. 중복 당첨 불가", {})
    L = r["limits"]
    assert L["per_person_limit"] == 1 and L["duplicate_win_allowed"] is False and L["sns_account"]["public_account_required"] is True
    assert A.item(ev(), "헤어라인 1회 체험권, 1인 1회 제공", {})["limits"]["per_person_limit"] is None
    assert A.item(ev(), "1인 최대 10회 참여 가능", {})["limits"]["per_person_limit"] == 10
    assert A.item(ev(), None, {})["limits"]["duplicate_entry_allowed"] is None


def test_auto_ok():
    form = dict(url="https://example.go.kr/ev", kind="설문", steps=["설문 참여", "폼 제출"], verification_grade="A",
                entry_url="https://form.naver.com/response/x", entry_type="form", form_url="https://form.naver.com/response/x")
    r = A.item(ev(**form), None, {})
    assert r["auto_ok"] is True and r["auto_blockers"] == []
    r = A.item(ev(**form), "매크로 사용 시 당첨 취소", {})
    assert r["auto_ok"] is False and "terms_forbid_automation" in r["auto_blockers"]
    assert A.item(ev(**form), "댓글 작성 완료 시 자동 응모 처리됩니다", {})["auto_ok"] is True
    r = A.item(ev(steps=["팔로우", "댓글"]), None, {})
    assert r["auto_ok"] is False and r["auto_blockers"] == ["sns_action_required"]
    # 검증되지 않은 주소는 판단 보류(null)
    r = A.item(ev(**(form | {"verification_grade": "B", "candidate_url": form["entry_url"]})), None, {})
    assert r["auto_ok"] is None and "entry_url_not_verified" in r["auto_blockers"]


def test_dates_iso_kst():
    r = A.item(ev(endTime="18:00"), None, {})
    assert r["starts_at"] == "2026-11-20T00:00:00+09:00"
    assert r["ends_at"] == "2026-11-30T18:00:00+09:00" and r["ends_at_time_known"] is True
    r = A.item(ev(start=None), None, {})
    assert r["starts_at"] is None and r["ends_at"] == "2026-11-30T23:59:59+09:00" and r["ends_at_time_known"] is False
    assert r["announce_at"] == "2026-12-05T00:00:00+09:00"


def test_entry_url_only_for_grade_a_b():
    assert A.item(ev(verification_grade="C", candidate_url="https://x.com/a"), None, {})["entry_url"] is None
    assert A.item(ev(verification_grade="B", candidate_url="https://x.com/a"), None, {})["entry_url"] == "https://x.com/a"


def test_build_files_and_new_by_day():
    events = [ev(id="t-a", firstSeen="2026-11-22", addedAt="2026-11-22T10:23:05+09:00"),
              ev(id="t-b", firstSeen="2026-11-21"),
              ev(id="t-c", firstSeen="2026-11-22", status="ended", end="2026-11-21")]
    out = A.build(events, {}, {"t-a": {"x-old"}}, SITE, "2026-11-22", "2026-11-22T11:00:00+09:00")
    assert [r["id"] for r in out["feed/events.json"]["events"]] == ["t-a", "t-b"]
    assert out["feed/events.json"]["events"][0]["merged_ids"] == ["x-old"]
    day = out["feed/new/2026-11-22.json"]
    assert {r["id"] for r in day["events"]} == {"t-a", "t-c"} and day["count"] == 2
    assert out["feed/new/index.json"]["days"][0] == {"date": "2026-11-22", "count": 2, "url": SITE + "feed/new/2026-11-22.json"}
    assert len([k for k in out if k.startswith("feed/new/20")]) == A.NEW_DAYS
    import xml.dom.minidom
    doc = xml.dom.minidom.parseString(out["feed/atom.xml"])
    assert doc.getElementsByTagName("entry")[0].getElementsByTagName("published")[0].firstChild.data == "2026-11-22T10:23:05+09:00"
    json.dumps(out["feed/schema.json"])


def test_schema_validates_output():
    try:
        import jsonschema
    except ImportError:
        return
    out = A.build([ev(), ev(id="t-z", steps=["팔로우"])], {}, {}, SITE, "2026-11-22", "2026-11-22T11:00:00+09:00")
    jsonschema.validate(out["feed/events.json"], A.SCHEMA)

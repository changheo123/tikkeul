# DB(data/db.json) → 사이트(_site/). 검증에 실패하면 0이 아닌 코드로 끝나 배포가 멈추고 기존 사이트가 유지된다
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from collector.common import now_kst
from collector.enrich import enrich

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_site"

# 출처 현황 표: (키, 이름, DB의 src 코드들, 방식 설명)
SOURCE_ROWS = [
    ("ledger", "사람 확인 장부", ["ledger"], "1단계에서 사람이 공식 원문과 대조해 승인한 이벤트"),
    ("korissue", "경품 이벤트 모음(korissue)", ["ki"], "오늘·내일 마감 이벤트와 주최 원문 응모 링크(인스타그램·블로그·구글폼 등)"),
    ("eventhouse", "경품 이벤트 모음(이벤트하우스)", ["eh"], "플랫폼별 진행 이벤트 목록. 응모 링크는 로그인 전용이라 쓰지 않고, 원문은 다른 출처와 합쳐 찾음"),
    ("korissue_orgs", "korissue 주최사별 진행 이벤트", ["ki"], "목록에 나온 주최사의 진행 이벤트와 원본 응모 링크(인스타그램·폼 등)를 일찍 확보"),
    ("eventhouse_search", "이벤트하우스 상세 검색(전자기기·X·카페)", ["eh"], "경품 분류·키워드 검색으로 진행 중 이벤트 탐색. 위 이벤트하우스와 같은 글은 하나로 합침"),
    ("events_kr", "경품 이벤트 모음(events.kr)", ["ek"], "목록·상세 공개 페이지(robots.txt 허용)"),
    ("contestkorea", "콘테스트코리아 기업주최 이벤트", ["ck"], "진행 중 이벤트 게시판. 주최사 공고와 참여 링크를 구분해 읽음"),
    ("wevity", "위비티 이벤트", ["wv"], "이벤트 게시판(글 수가 적음)"),
    (None, "올콘(all-con.co.kr)", [], "약관이 사전 승낙 없는 정보의 복제·유통을 금지해 수집 안 함"),
    (None, "씽굿(thinkcontest.com)", [], "이벤트 게시판이 없는 공모전 전용 사이트라 수집 안 함"),
    ("danawa", "다나와 이벤트", ["dn"], "다나와 자체 무료 이벤트 목록(퀴즈·체험단 등, 다나와 로그인 필요)"),
    ("sbs", "SBS 방송 이벤트", ["sb"], "SBS 공식 이벤트 목록(투표·방청·시청 이벤트)"),
    ("reviewnote", "무료 체험단(리뷰노트)", ["rn"], "체험단 모집 목록. 구매·페이백형은 유료로 보고 제외"),
    ("mrblog", "무료 체험단(미블)", ["mb"], "체험단 모집 목록. 선결제·페이백형은 유료로 보고 제외"),
    (None, "무료 체험단(강남맛집)", [], "수집기는 있으나 해외 서버(GitHub) 접속이 막혀 시간 초과 → 우회하지 않고 끔"),
    ("busan", "부산시 이벤트 통합안내", ["bs"], "공식 이벤트 게시판 목록"),
    ("mohw", "보건복지부 국민참여이벤트", ["mo"], "산하기관 이벤트 모음 게시판"),
    ("touredu", "한국관광공사 관광e배움터", ["te"], "진행 중 이벤트 게시판"),
    ("youtube", "유튜브 영상 설명란", ["yt"], "YouTube Data API(무료 키 필요)"),
    ("naver", "네이버 블로그·카페", ["nv"], "네이버 검색 API(무료 키 필요). 블로그 RSS는 robots.txt가 막아 쓰지 않음"),
    ("telegram", "텔레그램 공개 채널", ["tg"], "공개 채널 웹 미리보기(t.me/s). 수집할 채널 목록 필요"),
    (None, "슈퍼투데이(suto.co.kr)", [], "이용규칙이 외부 서비스로의 반복 전재와 자동 수집을 금지하고 robots.txt가 데이터 수집 봇을 막아 수집 안 함(허락을 받으면 연동 가능)"),
    (None, "인스타그램", [], "공개 게시물 검색을 허용하는 공식 API가 없어 수집 안 함. 다른 출처에서 찾은 인스타 이벤트는 표시"),
    (None, "X(트위터)", [], "검색 API가 유료라 무료 운영 원칙상 수집 안 함"),
    (None, "쿠팡·쇼핑몰", [], "공개 이벤트 목록 API가 없고 약관상 자동 수집이 제한돼 수집 안 함"),
    (None, "라디오·방송사", [], "방송사 이벤트 게시판이 로그인·스크립트 방식이라 아직 수집기 없음"),
]


def site_url():
    """사이트 대표 주소. 저장소 이름이 바뀌어도 Actions가 알려 주는 저장소 이름을 따라간다."""
    owner, name = os.environ.get("GITHUB_REPOSITORY", "changheo123/1234").split("/", 1)
    return f"https://{owner}.github.io/{name}/"


def source_meta(state, runs):
    last_run = runs[-1]["sources"] if runs else {}
    out = []
    for key, label, match, how in SOURCE_ROWS:
        if key is None:
            st = "no"
        elif key == "ledger":
            st = "ledger"
        else:
            r = last_run.get(key, {}).get("status")
            if r in (None, "wait"):  # 이번 실행 차례가 아니었으면 지난 결과로 판단
                r = "ok" if state.get("lastOk", {}).get(key) and not state.get("fails", {}).get(key) else ("unconfigured" if key in ("youtube", "naver", "telegram") and not state.get("lastOk", {}).get(key) else "error")
            st = {"ok": "on", "unconfigured": "off", "off": "off"}.get(r, "err")
        out.append({"key": key, "label": label, "match": match, "how": how, "st": st,
                    "last": state.get("lastOk", {}).get(key) if key else None})
    return out


def main():
    db = json.loads((ROOT / "data" / "db.json").read_text(encoding="utf-8"))
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8"))
    runs = json.loads((ROOT / "data" / "runs.json").read_text(encoding="utf-8"))
    via = {}
    for aid, oid in state.get("alias", {}).items():
        via.setdefault(oid, set()).add(aid.split("-")[0])

    events = []
    for e in db["events"].values():
        if e.get("status") not in ("active", "ended"):
            continue  # 반려(rejected)·링크 끊김(removed)은 싣지 않는다
        x = enrich(e)
        x["via"] = sorted(via.get(e["id"], []))
        for k in ("srcUrls", "linkFail", "platHint", "rawText", "formLinks", "pageFlags", "formPending", "formChecked", "formScanV", "prizeChecked", "truncChecked", "prizeFrom", "linkChecked", "lastSeen", "linkStatus"):
            x.pop(k, None)
        x["isAuto"] = bool(e.get("cand"))
        events.append(x)
    # 마감 가까운 순(없으면 맨 뒤) → 제목
    events.sort(key=lambda x: (x.get("end") or "9999", x["title"]))

    now = now_kst()
    today = now.date().isoformat()
    # 종료는 status 기준(마감일 미상이라도 이미 종료된 건 진행 중으로 세지 않는다)
    live = [x for x in events if x.get("status") != "ended" and (not x.get("end") or x["end"] >= today)]
    meta = {
        "built": now.strftime("%Y-%m-%d %H:%M"), "date": today,
        # 상단 안내의 건수는 기본 화면 기준(참여 제한 이벤트 제외)
        "approved": sum(1 for x in live if not x.get("cand") and x.get("is_restricted") is not True),
        "candidates": sum(1 for x in live if x.get("cand") and x.get("is_restricted") is not True),
        "shown": len(live), "ended": len(events) - len(live),
        "withForm": sum(1 for x in live if x.get("form_url")),
        "restricted": sum(1 for x in live if x.get("is_restricted") is True),
        "restrictReview": sum(1 for x in live if x.get("restriction_note")),
        "elec": sum(1 for x in live if x.get("elec")),
        "needsProof": sum(1 for x in live if x.get("needs_proof")),
        "sources": source_meta(state, runs),
        "lastRun": runs[-1]["at"] if runs else None,
    }
    # 출처별 진행 중 건수(화면에 일부만 싣더라도 정확하도록 여기서 계산)
    for s in meta["sources"]:
        s["count"] = sum(1 for x in live if x["src"] in s["match"] or set(x["via"]) & set(s["match"]))
    payload = {"meta": meta, "events": events}
    # 첫 화면에는 기본 노출 이벤트만 싣고, 나머지는 필요할 때 불러온다
    live_ids = {x["id"] for x in live}
    slim = lambda x: {k: v for k, v in x.items() if k not in ("listUrl", "isAuto", "excluded")}
    main = [slim(x) for x in live if x.get("is_restricted") is not True and x.get("grade") != "X"]
    main_ids = {x["id"] for x in main}
    extra = [slim(x) for x in live if x["id"] not in main_ids]
    # 종료 목록은 카테고리 구분과 목록 표시에 쓰는 값만
    ENDED_KEYS = ("id", "title", "org", "end", "url", "status", "grade", "kind", "plats", "pub", "elec", "card", "pcats", "pmain", "prize", "form_url")
    ended = [{k: x.get(k) for k in ENDED_KEYS} for x in events if x["id"] not in live_ids]
    assert len(main) + len(extra) + len(ended) == len(events), "분리 파일 건수 불일치"
    meta["mainCount"], meta["extraCount"] = len(main), len(extra)

    # 검증 1: DB의 진행 중·종료 건수와 사이트 데이터 건수가 같아야 한다
    n_db = sum(1 for e in db["events"].values() if e.get("status") in ("active", "ended"))
    assert len(events) == n_db, f"건수 불일치 {len(events)} != {n_db}"
    # 검증 2: 원문 링크가 없는 이벤트는 싣지 않는다
    bad = [x["id"] for x in events if not str(x.get("url", "")).startswith("http")]
    assert not bad, f"원문 링크 없음: {bad[:5]}"

    tpl = (ROOT / "web" / "template.html").read_text(encoding="utf-8")
    assert tpl.count("/*__DATA__*/") == 1
    data_js = json.dumps({"meta": meta, "events": main}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = tpl.replace("__SITE_URL__", site_url())  # 공유 정보·구조화 데이터의 주소(데이터 삽입 전에 바꾼다)
    page = tpl.replace("/*__DATA__*/", data_js)

    tmp = ROOT / "_site.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "data").mkdir(parents=True)
    # GitHub Pages는 문서 뼈대가 필요하다(Artifact는 자동으로 감싸 주지만 일반 호스팅은 아님)
    html = ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f'<link rel="canonical" href="{site_url()}">\n'
            '<link rel="icon" type="image/png" href="assets/icon-192.png">\n'
            '<link rel="apple-touch-icon" href="assets/apple-touch-icon.png">\n'
            '<link rel="manifest" href="assets/site.webmanifest">\n'
            f'{page.split("<header", 1)[0]}</head>\n<body>\n<header{page.split("<header", 1)[1]}\n</body></html>\n')
    (tmp / "index.html").write_text(html, encoding="utf-8")
    (tmp / "data" / "events.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, part in (("extra", extra), ("ended", ended)):
        (tmp / "data" / f"{name}.json").write_text(json.dumps({"events": part}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    status = [{k: v for k, v in r.items() if k != "sources"} | {"sources": {s: {a: b for a, b in v.items() if a != "trace"} for s, v in r["sources"].items()}} for r in runs[-100:]]
    (tmp / "data" / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    (tmp / ".nojekyll").write_text("")
    shutil.copytree(ROOT / "web" / "assets", tmp / "assets")  # 파비콘·앱 아이콘·공유 이미지

    # 검증 3: 페이지 스크립트 문법(node가 있으면)
    script = html.rsplit("<script>", 1)[1].rsplit("</script>", 1)[0]
    (tmp / "check.js").write_text(script, encoding="utf-8")
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", str(tmp / "check.js")], capture_output=True, text=True)
        assert r.returncode == 0, "스크립트 문법 오류: " + r.stderr[-500:]
    (tmp / "check.js").unlink()

    # 검증을 모두 통과한 뒤에만 결과 폴더에 덮어쓴다(폴더가 사용 중이어도 되도록 내용 복사)
    shutil.copytree(tmp, OUT, dirs_exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({k: v for k, v in meta.items() if k != "sources"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

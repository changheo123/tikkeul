# 이미 끝난 행사의 연도 밀림을 되돌리고, 같은 응모 폼은 하나로 합친다. 오래된 종료 이벤트는 보관 파일로 옮긴다
import datetime as dt
import json
import os
import re
from pathlib import Path

from .common import instagram_posted_in, sub_years
from . import forms
from .verify import title_is_stale

# 게시일보다 이 만큼 뒤면 연도 없는 일정이 한 해(또는 두 해) 밀린 것이다
_SHIFT = ((300, 430, 1), (660, 800, 2))
_EXPLICIT = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_SHORT_YEAR = re.compile(r"(?:기간|마감|까지|발표)[^0-9]{0,8}(?<!\d)(\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")


def _iso(d):
    return d.isoformat() if isinstance(d, dt.date) else d


def _years_late(anchor, pub):
    delta = (anchor - pub).days
    for lo, hi, years in _SHIFT:
        if lo <= delta <= hi:
            return years
    return 0


def _shift_fields(e, years):
    for k in ("start", "end", "announce"):
        if e.get(k):
            e[k] = sub_years(dt.date.fromisoformat(e[k]), years).isoformat()


def explicit_deadline_year(e):
    """본문·제목에 마감과 같은 월·일이 더 이른 연도로 적혀 있으면 그 연도."""
    end = e.get("end")
    if not end:
        return None
    ed = dt.date.fromisoformat(end)
    blob = f"{e.get('title') or ''}\n{e.get('rawText') or ''}"
    found = None
    for m in _EXPLICIT.finditer(blob):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if (mo, d) == (ed.month, ed.day) and y < ed.year:
            found = y
    for m in _SHORT_YEAR.finditer(blob):
        y = 2000 + int(m.group(1))
        mo, d = int(m.group(2)), int(m.group(3))
        if 2020 <= y < ed.year and (mo, d) == (ed.month, ed.day):
            found = y
    return found


def close_shifted(db, today):
    """게시일이나 본문 연도로 보아 이미 끝난 행사를 바로 종료한다.
    연도 없는 '11/13~11/20'이 올해·내년으로 들어간 경우 해를 게시일에 맞춘다."""
    res = {"realigned": 0, "ended": 0}
    for e in db["events"].values():
        if e.get("status") not in ("active", "ended") or not e.get("end"):
            continue
        before = (e.get("start"), e.get("end"), e.get("announce"))
        pub = instagram_posted_in(e.get("url"), *(e.get("srcUrls") or []))
        if pub:
            anchor_s = e.get("start") or e.get("end")
            years = _years_late(dt.date.fromisoformat(anchor_s), pub)
            if years:
                _shift_fields(e, years)
        y = explicit_deadline_year(e)
        if y and e.get("end") and int(e["end"][:4]) > y:
            _shift_fields(e, int(e["end"][:4]) - y)
        realigned = (e.get("start"), e.get("end"), e.get("announce")) != before
        res["realigned"] += realigned
        # 마감일이 그냥 지난 경우는 update_status가 종료한다. 여기서는 연도가 밀린 경우만 바로 종료한다
        if e.get("status") == "active" and ((realigned and e.get("end") and e["end"] < today) or title_is_stale(e)):
            e["status"] = "ended"
            e["ended_reason"] = "게시일 또는 본문 연도가 지난 행사를 가리킴"
            res["ended"] += 1
    return res


def form_key(url):
    """같은 응모 폼이면 같은 키. 단축 주소·편집 화면은 키가 없다."""
    u = forms.is_form(url or "")
    if not u:
        return None
    patterns = (
        ("naver", r"form\.naver\.com/response/([\w\-]+)"),
        ("google", r"docs\.google\.com/forms(?:/u/\d+)?/d/(?:e/)?([\w\-]+)"),
        ("moa", r"moaform\.com/q/([\w\-]+)"),
        ("smore", r"smore\.im/form/([\w\-]+)"),
    )
    for name, pat in patterns:
        m = re.search(pat, u, re.I)
        if m:
            return name + ":" + m.group(1)
    return None


def dedupe_forms(db):
    """최종 폼 아이디가 같은 진행·종료 이벤트는 하나만 남긴다."""
    groups = {}
    for e in db["events"].values():
        if e.get("status") not in ("active", "ended"):
            continue
        key = form_key(e.get("form_url"))
        if key:
            groups.setdefault(key, []).append(e)
    merged = 0
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda e: (
            e.get("status") != "active",
            bool(e.get("cand", True)),
            not e.get("entry_url"),
            e.get("firstSeen") or "9999",
            e["id"],
        ))
        keep, rest = rows[0], rows[1:]
        urls = keep.setdefault("srcUrls", [])
        links = keep.setdefault("formLinks", [])
        for e in rest:
            for u in [e.get("url"), *(e.get("srcUrls") or [])]:
                if u and u not in urls:
                    urls.append(u)
            for u in e.get("formLinks") or []:
                if u not in links:
                    links.append(u)
            if not keep.get("form_url") and e.get("form_url"):
                keep["form_url"] = e["form_url"]
            e["status"], e["excluded"] = "merged", keep["id"]
            merged += 1
    return merged


ARCHIVE_DAYS = 7       # 마감 후 이 기간이 지난 종료 이벤트는 보관 파일로 옮긴다
NO_END_DAYS = 30       # 마감일 없는 종료 이벤트는 마지막으로 본 날 기준
SHORTLINK_DAYS = 30    # 단축 링크 확인 결과 캐시 보존 기간


def _archive_month(e):
    return (e.get("end") or e.get("lastSeen") or e.get("firstSeen") or "0000-00")[:7]


def _write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def archive_ended(db, state, vstore, today, archive_dir):
    """마감 후 오래 지난 종료 이벤트를 월별 보관 파일(archive_dir/ended-YYYY-MM.json)로 옮긴다.
    삭제가 아니라 이동이며, 보관 파일에 먼저 쓴 뒤에만 DB에서 뺀다. 진행 중 이벤트는 건드리지 않는다.
    함께 정리: 사라진 이벤트의 검증 결과 캐시, 오래된 단축 링크 캐시, 대상이 사라진 별칭."""
    t = dt.date.fromisoformat(today)
    cut, cut_no_end = (t - dt.timedelta(days=ARCHIVE_DAYS)).isoformat(), (t - dt.timedelta(days=NO_END_DAYS)).isoformat()
    res = {"archived": 0, "files": 0, "verify_pruned": 0, "shortlinks_pruned": 0, "alias_pruned": 0}
    move = {}
    for k, e in db["events"].items():
        if e.get("status") != "ended":
            continue
        old = (e["end"] < cut) if e.get("end") else ((e.get("lastSeen") or e.get("firstSeen") or "9999") < cut_no_end)
        if old:
            move.setdefault(_archive_month(e), {})[k] = e
    if move:
        archive_dir = Path(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        for month, rows in sorted(move.items()):
            path = archive_dir / f"ended-{month}.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"events": {}}
            data["events"].update(rows)
            _write_json(path, data)
            res["files"] += 1
        for rows in move.values():
            for k in rows:
                del db["events"][k]
                res["archived"] += 1
    keep = set(db["events"])
    for k in [k for k in vstore.get("events", {}) if k not in keep or db["events"][k].get("status") != "active"]:
        del vstore["events"][k]  # 검증 결과는 진행 중 이벤트에만 쓰는 캐시(변경 이력은 history에 남김)
        res["verify_pruned"] += 1
    short = state.get("shortlinks", {})
    scut = (t - dt.timedelta(days=SHORTLINK_DAYS)).isoformat()
    for u in [u for u, c in short.items() if (c.get("at") or "0000") < scut]:
        del short[u]
        res["shortlinks_pruned"] += 1
    alias = state.get("alias", {})
    for a in [a for a, oid in alias.items() if oid not in keep]:
        del alias[a]
        res["alias_pruned"] += 1
    return res


def load_archive(archive_dir, since):
    """보관 파일 중 마감(또는 마지막 확인)이 since 이후인 이벤트. 사이트의 종료 목록·저장 목록에 쓴다."""
    out = []
    for path in sorted(Path(archive_dir).glob("ended-*.json")):
        if path.stem[6:] < since[:7]:
            continue
        for e in json.loads(path.read_text(encoding="utf-8"))["events"].values():
            if (e.get("end") or e.get("lastSeen") or "") >= since:
                out.append(e)
    return out

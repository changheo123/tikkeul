# 화면용 파생 필드 계산(1단계 enrich.py를 모듈로 옮김). 장부 값에서만 도출하고 근거가 없으면 None
import re

PRIVATE = ["BNK투자증권", "현대차증권", "한독 모터스", "씨게이트", "파리바게뜨", "신한라이프", "행복얼라이언스",
           "지냄", "옥시젠슈티컬스", "필로텍", "동서식품", "십세들", "주최 미표기", "스낵인터랙티브", "쉐어잇", "카페게이트", "(주)"]
PUBLIC_ORG = re.compile(r"(?:부|청|처|원|공단|공사|재단|센터|위원회|박물관|과학관|도서관|교육청|의회|대학교|정부|시|군|구|도)(?:\s|$|\(|·|,)|정부|시청|도청|구청|군청|광역시|특별시|특별자치")
PUBLIC_HOST = re.compile(r"\.go\.kr|\.re\.kr|\.ac\.kr|\.mil\.kr|\.or\.kr")


def platforms(e):
    u = (e.get("url") or "").lower()
    p = []
    hint = e.get("platHint")
    if "instagram.com" in u or "인스타 참여" in e["tags"] or hint == "insta": p.append("insta")
    if "youtube.com" in u or "youtu.be" in u or "유튜브 참여" in e["tags"] or hint == "yt": p.append("yt")
    if "blog.naver.com" in u or "cafe.naver.com" in u or hint == "naver": p.append("naver")
    if "x.com/" in u or "twitter.com" in u or hint == "x": p.append("x")
    if "t.me/" in u or hint == "tg": p.append("tg")
    if "coupang.com" in u or "smartstore.naver.com" in u or hint == "shop": p.append("shop")
    if not p: p.append("web")
    return p


def via_list(e):
    """응모 링크가 주최 원문이 아니라 모음 사이트·공유 채널인 경우."""
    return bool(re.search(r"events\.kr/|t\.me/|eventhouse\.kr/|contestkorea\.com/|wevity\.com/", e.get("url") or ""))


def is_public(e):
    org = e.get("org") or ""
    if any(k in org for k in PRIVATE):
        return False
    if e.get("src") in (None, "ledger"):
        return "주최 미표기" not in org  # 1단계와 같은 판정(장부는 기업 목록 외에는 공공)
    hosts = " ".join(x or "" for x in (e.get("url"), e.get("listUrl") if e.get("src") != "ek" else None))
    return bool(PUBLIC_ORG.search(org + " ") or (PUBLIC_HOST.search(hosts) and not re.search(r"events\.kr", hosts)))


PRIZE_TYPES = [
    ("coffee", ["커피", "아메리카노", "라떼", "스타벅스", "투썸", "메가", "컴포즈", "공차", "프라페", "밀크티", "카페"]),
    ("gift", ["상품권", "네이버페이", "포인트", "금액권", "해피콘", "배달의민족", "배민", "온누리", "튼튼머니", "땡겨요", "배달쿠폰", "기프티콘", "쿠폰"]),
    ("food", ["치킨", "피자", "케이크", "디저트", "한우", "브리또", "햄버거", "분식"]),
    ("goods", ["워치", "에어팟", "고프로", "헤드셋", "카메라", "골드바", "숙박권", "퓨리케어", "크림", "키보드", "독서등",
               "캐리어", "테이블웨어", "초대권", "버즈", "굿즈", "레디백", "인스탁스", "아이패드", "갤럭시", "교통카드"]),
]


def prize_types(e):
    p = e.get("prize") or ""
    return [k for k, words in PRIZE_TYPES if any(w in p for w in words)]


def prize_value(e):
    # 공백을 지우지 않는다: 'GS25 5천원권'을 붙이면 '255천원'으로 잘못 읽힌다
    p = e.get("prize") or ""
    b = r"(?<![\dA-Za-z])"  # 영문·숫자 바로 뒤의 숫자(GS25, CU2 등)는 금액이 아니다
    vals = []
    for m in re.finditer(b + r"(\d+)\s*만\s*(?:(\d)\s*천\s*)?원", p):
        vals.append(int(m.group(1)) * 10000 + (int(m.group(2)) * 1000 if m.group(2) else 0))
    for m in re.finditer(b + r"(?<!만)(?<!만\s)(\d+)\s*천\s*원", p):
        vals.append(int(m.group(1)) * 1000)
    for m in re.finditer(b + r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원", p):
        vals.append(int(m.group(1).replace(",", "")))
    return max(vals) if vals else None


TAX_WORDS = ["골드바", "워치", "에어팟", "고프로", "숙박권", "퓨리케어", "헤드셋", "카메라", "버즈", "한우", "캐리어", "아이패드"]


def tax_flag(e, v):
    p = e.get("prize") or ""
    return any(w in p for w in TAX_WORDS) or (v is not None and v > 50000)


def time_bucket(e):
    """0=10초, 1=1분, 2=5분, 3=5분 이상, None=확인 필요. est=True면 절차·태그로 어림한 값."""
    if e.get("max"):
        m = e["max"]
        return (1 if m <= 1 else 2 if m <= 5 else 3), False
    tags = e["tags"]
    if "10분 넘게 걸림" in tags or "아이디어 제출" in tags or "공모전" in e["reasons"]: return 3, False
    if "원문 미확인" in tags or "세부 미확인" in tags: return None, True
    if e["kind"] == "설문": return 3 if "10분" in " ".join(e["needs"]) else 2, True
    if "캡처 인증" in tags or "로그인 필요" in tags: return 2, True
    if e["kind"] in ("퀴즈", "투표"): return 2, True
    if e["kind"] == "댓글": return 1, True
    return None, True


def need_account(e):
    txt = " ".join(e["needs"] + e["steps"] + e["reasons"] + e["tags"])
    return bool(re.search(r"회원가입|로그인", txt))


def need_sns(e, plats):
    if any(p in plats for p in ("insta", "yt", "naver", "x", "tg")): return True
    txt = " ".join(e["needs"] + e["tags"] + e["reasons"] + e["steps"])
    return bool(re.search(r"팔로우|구독|SNS|공유·게시형|인스타|유튜브", txt))


def enrich(e):
    plats = platforms(e)
    v = prize_value(e)
    tb, est = time_bucket(e)
    out = dict(e)
    out.update({
        "plats": plats, "pub": is_public(e), "viaList": via_list(e), "ptypes": prize_types(e),
        "value": v, "tax": tax_flag(e, v), "tb": tb, "est": est,
        "acct": need_account(e), "sns": need_sns(e, plats),
        # ok=사람이 확인한 장부에 유료 조건 없음, check=자동 수집·원문 미확인이라 원문 확인 필요
        "free": "check" if (e.get("cand") or e.get("issues") or "원문 미확인" in e["tags"] or via_list(e)) else "ok",
        "freeNote": e.get("freeNote"),
        "form_url": e.get("form_url"),
        "is_restricted": e.get("is_restricted") is True,
        "restriction_note": e.get("restriction_note"),
        "elec": e.get("elec") or [],
        "elec_models": e.get("elec_models") or [],
        "restriction_reason": e.get("restriction_reason"),
    })
    return out

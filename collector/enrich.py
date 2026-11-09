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


# 경품 카테고리: 경품명에 실제로 적힌 말로만 나눈다(복수 허용). 전자기기는 기존 elec 분류를 따른다
PRIZE_CATS = [
    ("coffee", r"커피|아메리카노|(?:카페\s*)?라떼|스타벅스|투썸|메가\s*(?:MGC|엠지씨)|컴포즈|이디야|빽다방|할리스|폴바셋|공차|프라페|밀크티|바리스타룰스"),
    ("food", r"치킨|피자|케이크|디저트|한우|브리또|햄버거|분식|베이글|도넛|마카롱|떡|빵|쌀|과일|식품|간식|아이스크림|배스킨|배라|파리바게|뚜레쥬르|교촌|굽네|BBQ|bhc|외식|식사권|식단|밀키트|고기|삼겹|라면|초콜릿|쿠키|과자|스낵|즙|차\s*세트|티박스|스초생|특산물|김치|버거|맥도날드|롯데리아|맘스터치|KFC|한솥|베이커리|약과|빼빼로|사과|벌꿀|밤꿀|어묵|곰탕|갈비|시리얼|후레이크|소스|반찬|누룽지|우유|음료|콜라|이온워터|수분워터|생수|유산균|낙산균|마그네슘|영양제|건강기능|콜라겐|비오틴"),
    ("beauty", r"화장품|올리브영|립스틱|립밤|틴트|향수|마스크팩|세럼|에센스|선크림|뷰티|바디로션|핸드크림|네일|(?<![이스생])크림(?!빵)|퍼프|미스트|헤나|립\s*시럽|톤업"),
    ("travel", r"숙박|호텔|리조트|펜션|항공권|(?<!여행용\s)여행(?!용)|캠핑|템플스테이|투어"),
    ("gift", r"상품권|금액권|네이버\s*페이|네이버\s*(?:모바일\s*)?포인트|포인트|\d\s*(?:만|천)\s*원권|온누리|컬쳐|배민|배달의민족|땡겨요|요기요|쿠팡이츠|서울페이|지역화폐|기프트\s*카드|현금|캐시|머니|올리브영|교통카드|주유권|적립금|11pay|(?:편의점|배달앱)\s*이용권"),
    ("gifticon", r"기프티콘|기프트콘|해피콘|쿠폰|교환권|모바일\s*상품권"),
    ("life", r"텀블러|에코백|수건|타월|타올|아이깨끗해|수면안대|다리미|지갑|마사지기|세제|휴지|물티슈|담요|우산|캐리어|레디백|파우치|식기|테이블웨어|머그|방향제|치약|칫솔|청소|주방|샴푸|비누|핸드워시|양말|슬리퍼|수면조끼|독서등|다이어리|달력|모자|가방|이불|베개"),
]
PRIZE_CATS = [(k, re.compile(p, re.I)) for k, p in PRIZE_CATS]
# 체험단 원고료·적립 포인트는 경품이 아니다
REWARD_ONLY = re.compile(r"원고료\s*[\d,]*\s*(?:포인트|원|p)?|\+?\s*[\d,만]+\s*(?:포인트|p)(?![가-힣])", re.I)
TRAIL_COUNT = re.compile(r"(?:\s*(?:최대|총|회차당|매주)?\s*[\d,]+\s*(?:명|분|팀)|\s+[xX]\s*\d+|\s*×\s*\d+)+\s*$")
COMPLETE = re.compile(r"(?:아메리카노|커피|라떼|상품권|금액권|교환권|쿠폰|기프티콘|포인트|케이크|세트|파인트|프라페|치약)$")
GENERIC = re.compile(r"^[가-힣\d\s]{0,8}(?:선물|상품|경품(?:\s*미공개)?)$")  # 실제 경품명이 아닌 말


def _clean_item(s):
    """카드에 쓸 경품명 한 개: 등수·당첨 인원·해시태그·출처 표시 등을 뺀다(없는 말을 만들지 않음)."""
    if "▶" in s:
        s = s.rsplit("▶", 1)[1]
    s = re.sub(r"[#＃]\S+", "", s).split("(")[0]
    s = re.sub(r"\s*\+\s*[\d,.만]+\s*(?:포인트|p)\s*$", "", s.strip(), flags=re.I)  # 체험단 적립 포인트
    s = re.sub(r"\s*(?:\.{2,}|…)\s*", "…", s)  # 출처가 줄여 놓은 말은 줄임표로 남긴다
    s = re.sub(r"^[\s◇◆■□●○·\-]+", "", s)
    s = re.sub(r"^(?:(?:이벤트\s*)?경품|[가-힣]{1,6}상|\d+등)\s*[:：]\s*", "", s)
    s = re.sub(r"^\d+등\s+", "", s)
    s = re.sub(r"^[^:：]{2,14}[:：]\s+", "", s)  # '스탬프 2개 이상: 경품' 같은 앞말
    s = re.sub(r"^(?:매주|매일|매월|회차별)\s*", "", s)
    s = re.sub(r"^추첨을?\s*통해\s*", "", s)
    s = re.sub(r"^[\d,]+\s*명(?:을|에게)?\s*(?:무작위로|추첨(?:을\s*통해|으로|하여)?)?\s*(?:선정해|선정하여)?\s*", "", s)
    s = re.sub(r"(?:을|를)?\s*(?:드립니다|드려요|증정합니다|받아보세요)[.!”\"]*$", "", s)
    s = re.sub(r"도$", "", s)
    s = TRAIL_COUNT.sub("", s.strip())
    s = re.sub(r"\s*증정$", "", s).strip(" ,·/+-")
    s = re.sub(r"\s+", " ", s)
    # 잘렸더라도 끝말이 온전한 낱말이면 줄임표 없이 보여 준다(그 뒤의 말은 지어내지 않는다)
    if s.endswith("…") and COMPLETE.search(s[:-1].strip()):
        s = s[:-1].strip()
    return "" if GENERIC.match(s) else s


def prize_items(e):
    """경품 목록. 여러 경품이 한 칸에 적힌 경우 나눈다."""
    many = e.get("prizeAll") or []
    if len(many) > 1:
        raw = many
    else:
        p = re.sub(r"\([^()]*\)", " ", e.get("prize") or "")
        raw = re.split(r"\s+·\s+|,\s+", p)
    out = []
    for x in raw:
        c = _clean_item(x)
        if c and c not in out:
            out.append(c)
    return out


def card_name(e):
    """카드의 큰 글씨. 경품명이 없으면 간결한 제목으로 대체한다."""
    items = prize_items(e)
    if items:
        return items[0] if len(items) == 1 else f"{items[0]} 외 {len(items) - 1}종"
    t = (e.get("title") or "").strip()
    return re.sub(r"\s*(?:경품\s*)?이벤트\s*[!~.]*\s*$", "", t) or t


def prize_cats(e):
    """(카테고리 목록, 대표 카테고리). 하나의 이벤트가 여러 카테고리에 속할 수 있다."""
    text = REWARD_ONLY.sub(" ", " ".join([e.get("prize") or "", *(e.get("prizeAll") or [])]))
    cats = [k for k, rx in PRIZE_CATS if rx.search(text)]
    if e.get("elec"):
        cats.insert(0, "elec")
    items = prize_items(e)
    first = REWARD_ONLY.sub(" ", items[0]) if items else ""
    main = next((k for k, rx in PRIZE_CATS if rx.search(first)), None) or ("elec" if e.get("elec") else "etc")
    return cats or ["etc"], main


def enrich(e):
    plats = platforms(e)
    pcats, pmain = prize_cats(e)
    v = prize_value(e)
    tb, est = time_bucket(e)
    out = dict(e)
    out.update({
        "plats": plats, "pub": is_public(e), "viaList": via_list(e), "ptypes": prize_types(e),
        "card": card_name(e), "pcats": pcats, "pmain": pmain,
        "value": v, "tax": tax_flag(e, v), "tb": tb, "est": est,
        "acct": need_account(e), "sns": need_sns(e, plats),
        # ok=사람이 확인한 장부에 유료 조건 없음, check=자동 수집·원문 미확인이라 원문 확인 필요
        "free": "check" if (e.get("cand") or e.get("issues") or "원문 미확인" in e["tags"] or via_list(e)) else "ok",
        "freeNote": e.get("freeNote"),
        "form_url": e.get("form_url"),
        "is_restricted": e.get("is_restricted") is True,
        "restriction_note": e.get("restriction_note"),
        "needs_proof": e.get("needs_proof") is True,
        "proof_reason": e.get("proof_reason"),
        "elec": e.get("elec") or [],
        "elec_models": e.get("elec_models") or [],
        "restriction_reason": e.get("restriction_reason"),
    })
    return out

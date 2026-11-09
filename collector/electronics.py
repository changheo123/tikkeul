# 전자기기 경품 분류. 실제 경품 문구에서만 판별하고, 모델명은 원문에 적힌 표기만 저장한다(추측 금지)
import re

TYPES = {  # 키: (화면 이름, 판별 정규식)
    "audio": ("이어폰·헤드폰", r"에어팟|AirPods|갤럭시\s*버즈|버즈\s*\d|Galaxy\s*Buds|이어폰|헤드폰|헤드셋"),
    "pc": ("노트북·PC", r"맥북|MacBook|갤럭시\s*북|(?:LG|엘지)\s*그램|그램\s*노트북|노트북|데스크\s*[톱탑]|게이밍\s*PC|컴퓨터\s*본체"),
    "phone": ("스마트폰", r"아이폰|iPhone|갤럭시\s*[SZ]\s*\d*|갤럭시\s*[SZ]\s*(?:플립|폴드)|갤럭시(?!\s*(?:버즈|워치|탭|북|핏|링|스토어|코인|아카데미|GALAX|S\d|Z))(?=\s*(?:\d|$|[-,(/·+]|폰))|스마트폰"),
    "tablet": ("태블릿", r"아이패드|iPad|갤럭시\s*탭|태블릿"),
    "watch": ("스마트워치", r"애플\s*워치|Apple\s*Watch|갤럭시\s*워치|스마트\s*워치"),
    "console": ("게임기", r"플레이스테이션|PS\s*5|(?<![가-힣])플스\s*5?|닌텐도|Nintendo|스위치\s*2|Xbox|엑스박스"),
    "other": ("기타", r"모니터(?!링|요원|단)|카메라|고프로|액션캠|인스타\s*360|빔\s*프로젝터|프로젝터|로봇\s*청소기|(?<![A-Za-z가-힣])TV(?![A-Za-z])|(?<![가-힣])티비"),
}
_RX = {k: re.compile(v[1], re.I) for k, v in TYPES.items()}
# 전자기기 본체가 아닌 것: 키워드 바로 뒤에 붙는 액세서리·이용권
NOT_DEVICE = re.compile(r"^.{0,10}?(?:케이스|파우치|커버|거치대|충전기|보조\s*배터리|필름|스트랩|가방|스킨|키링|스티커|그립|삼각대|쿠폰|할인|구독|시청권|이용권|프로그램|필터)", re.I)
GENERIC_PRIZE = re.compile(r"^(?:상품|선물|경품|소정|푸짐|다양|기프티콘|추첨 ?선물)")
TITLE_GIVE = r".{0,12}?(?:받자|노리자|챙기자|증정|추첨|경품|드려요|드립니다)"
PRIZE_LINE = re.compile(r"경품|상품|선물|증정|추첨|\d\s*등|\d+\s*명")

MODELS = re.compile(
    r"에어팟\s*(?:프로|Pro|맥스|Max)?\s*\d?(?:세대)?|갤럭시\s*버즈\s*\d*\s*(?:프로|FE|라이브)?|갤럭시\s*워치\s*\d*\s*(?:울트라|클래식)?"
    r"|갤럭시\s*탭\s*[A-Z]?\d*\s*(?:울트라|\+)?|갤럭시\s*북\s*\d*\s*(?:프로|울트라)?|갤럭시\s*[SZ]\s*(?:플립|폴드)?\s*\d+\s*(?:울트라|\+|FE)?"
    r"|아이폰\s*\d+\s*(?:프로\s*맥스|프로|플러스|에어|미니|e)?|아이패드\s*(?:프로|에어|미니)?\s*\d*(?:세대)?|맥북\s*(?:에어|프로|네오)?\s*\d*"
    r"|애플\s*워치\s*(?:SE\s*\d?|울트라\s*\d?|시리즈\s*\d+|\d+)?|(?:LG|엘지)\s*그램\s*\d*|PS\s*5\s*(?:프로|슬림)?|플레이스테이션\s*\d*|닌텐도\s*스위치\s*\d*"
    r"|Xbox\s*[A-Za-z ]{0,8}|소니\s*[A-Z]{2,}-?\d{3,}[A-Z0-9]*|[A-Z]{2,}-\d{3,}[A-Z0-9]*", re.I)


def _types_in(text):
    found = []
    for k, rx in _RX.items():
        for m in rx.finditer(text or ""):
            if not NOT_DEVICE.match(text[m.end():m.end() + 16]):
                found.append(k)
                break
    return found


def prize_texts(e):
    """전자기기를 찾아볼 경품 문구들. 경품이 불명확할 때만 제목을, 본문은 경품 안내 줄만 쓴다."""
    prize = (e.get("prize") or "").strip()
    texts = [prize] if prize else []
    texts += list(e.get("prizeAll") or [])  # 1등 외 전체 경품
    for ln in (e.get("rawText") or "").splitlines():
        if PRIZE_LINE.search(ln) and len(ln) <= 120:
            texts.append(ln)
    return texts


def classify(e):
    """(종류 목록, 모델명 목록). 체험단(리뷰 조건 제공)은 추첨 경품이 아니므로 대상에서 뺀다."""
    if e.get("kind") == "체험단":
        return [], []
    title = e.get("title") or ""
    texts = prize_texts(e)
    prize = (e.get("prize") or "").strip()
    found = []
    for t in texts:
        found += _types_in(t)
    used = list(texts)
    if not e.get("prizeAll") and (not prize or ".." in prize or GENERIC_PRIZE.match(prize)):
        # 경품 칸이 비었거나 잘린 경우: 제목에서 기기 이름 바로 뒤에 증정 표현이 있을 때만 인정
        for k, rx in _RX.items():
            if re.search(f"(?:{rx.pattern})" + TITLE_GIVE, title, re.I) and not any(NOT_DEVICE.match(title[m.end():m.end() + 16]) for m in rx.finditer(title)):
                found.append(k)
                used.append(title)
    kinds = [k for k in TYPES if k in found]
    models = []
    if kinds:
        for t in used:
            for m in MODELS.finditer(t):
                s = m.group(0)
                # '에어팟 2명'의 2는 세대가 아니라 당첨 인원이다
                if re.match(r"\d*\s*(?:명|개|인|대|장|세트)", t[m.end():]) and re.search(r"\d$", s):
                    s = re.sub(r"\s*\d+$", "", s)
                s = re.sub(r"\s+", " ", s).strip()
                if len(s) > 2 and s not in models:
                    models.append(s)
    # 더 자세한 표기가 있으면 짧은 표기는 뺀다(갤럭시워치 9 ⊃ 갤럭시워치)
    key = lambda x: x.replace(" ", "").lower()
    models = [m for m in models if not any(o != m and key(o).startswith(key(m)) for o in models)]
    return kinds, models[:6]

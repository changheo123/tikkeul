# 응모 조건 구조화: 참여 절차를 행동 단위로 나누고 조건을 값으로 만든다.
# 원칙: 근거가 있을 때만 true/false. 근거가 없으면 null(확인 안 됨)이며 '없음'을 추측하지 않는다.
import re

# 행동 종류(한 절차 문장에 여러 개가 있을 수 있다)
ACTIONS = [
    ("follow", r"팔로우|팔로잉|구독|이웃\s*추가|채널\s*추가|친구\s*추가|단골"),
    ("like", r"좋아요|하트|공감"),
    ("comment", r"댓글|답글|기대평|응원글|한\s*줄|행시"),
    ("share", r"공유|리그램|리포스트|소문|스토리\s*(?:게시|업로드)|태그\s*업로드|게시물\s*업로드"),
    ("tag_friend", r"친구\s*태그|태그\s*친구|@친구|지인\s*태그"),
    ("quiz", r"퀴즈|정답|맞히|맞추|초성|빈칸|OX"),
    ("survey", r"설문|만족도|투표|의견\s*조사"),
    ("form", r"폼|신청서|양식|정보\s*입력|응모\s*하기|신청"),
    ("proof", r"캡처|캡쳐|인증샷|인증\s*사진|인증|영수증|스크린샷"),
    ("photo", r"사진|촬영|업로드|이미지"),
    ("watch", r"시청|영상\s*(?:보기|확인)|감상|청취"),
    ("visit", r"방문|누리집|홈페이지|접속|사이트"),
    ("dm", r"(?<![A-Za-z])DM(?![A-Za-z])|메시지\s*(?:보내|전송)"),
    ("signup", r"회원\s*가입|신규\s*가입"),
    ("login", r"로그인"),
    ("review", r"후기|리뷰|사용기"),
]
ACTIONS = [(k, re.compile(p, re.I)) for k, p in ACTIONS]

PERSONAL = [
    ("name", r"이름|성명|성함"),
    ("phone", r"연락처|휴대\s*폰|핸드폰|전화\s*번호|휴대전화"),
    ("address", r"주소|배송지"),
    ("email", r"이메일|e-?mail"),
    ("birth", r"생년월일|출생\s*연도"),
    ("sns_id", r"(?:인스타|유튜브|블로그|SNS)\s*(?:아이디|계정|닉네임)|아이디|닉네임"),
]
PERSONAL = [(k, re.compile(p, re.I)) for k, p in PERSONAL]
ONE_PER = re.compile(r"1인\s*1회|1인당\s*1회|ID\s*당\s*1회|계정\s*당\s*1회|중복\s*(?:응모|참여|당첨)\s*(?:불가|불가능|제한|안\s*됨|안됩니다|시\s*제외)")
MULTI_OK = re.compile(r"중복\s*(?:응모|참여)\s*(?:가능|허용)|매일\s*(?:응모|참여)\s*가능|여러\s*번\s*(?:응모|참여)")
AGE = re.compile(r"만\s*\d{1,2}\s*세\s*(?:이상|이하|미만)|\d{1,2}\s*세\s*이상|성인|미성년자|청소년|초등학생|중학생|고등학생|대학생|재학생|졸업생")
SNS_PLATS = ("insta", "yt", "naver", "x", "tg")


def _text(e):
    return " ".join([e.get("title") or "", *(e.get("steps") or []), *(e.get("needs") or []), *(e.get("tags") or []), e.get("rawText") or ""])


def actions_of(text):
    return [k for k, rx in ACTIONS if rx.search(text or "")]


def steps(e):
    """참여 절차를 순서·원문·행동 종류로. 절차를 모르면 빈 목록."""
    return [{"order": i, "text": s, "actions": actions_of(s)} for i, s in enumerate(e.get("steps") or [], 1)]


def _has(actions, key, known):
    """절차가 있으면 그 행동이 있는지 true/false, 절차를 모르면 null."""
    if key in actions:
        return True
    return False if known else None


def conditions(e):
    """응모 조건. e는 enrich를 거친 이벤트(plats·acct·tax·free·tb 등 포함)."""
    st = steps(e)
    known = bool(st)
    acts = {a for s in st for a in s["actions"]}
    text = _text(e)
    needs = " ".join(e.get("needs") or [])
    plats = [p for p in e.get("plats") or [] if p in SNS_PLATS]
    login = True if (re.search(r"로그인|회원가입", needs + " ".join(e.get("steps") or [])) or "로그인 필요" in (e.get("tags") or [])) else None
    signup = True if re.search(r"신규\s*(?:회원\s*)?가입|회원\s*가입\s*(?:후|필요|필수)", text) else None
    found_personal = [k for k, rx in PERSONAL if rx.search(text)]
    one = True if ONE_PER.search(text) else (False if MULTI_OK.search(text) else None)
    age = sorted(set(m.group(0).replace(" ", "") for m in AGE.finditer(text))) or None
    restricted = e.get("is_restricted") is True
    visit = True if ("방문 필요" in (e.get("tags") or []) or re.search(r"방문", e.get("restriction_reason") or "")) else None
    minutes = None
    if e.get("max"):
        minutes = {"min": e.get("min") or e["max"], "max": e["max"], "estimated": False}
    elif e.get("tb") is not None:
        minutes = {"min": None, "max": [0.2, 1, 5, None][e["tb"]], "estimated": bool(e.get("est"))}
    return {
        "steps_known": known,
        "login_required": login,
        "signup_required": signup,
        "sns_account_required": True if (plats and (acts & {"follow", "like", "comment", "share", "dm"} or not known)) else (False if known and not plats else None),
        "sns_platforms": plats,
        "follow_required": _has(acts, "follow", known),
        "like_required": _has(acts, "like", known),
        "comment_required": _has(acts, "comment", known),
        "share_required": _has(acts, "share", known),
        "tag_friend_required": _has(acts, "tag_friend", known),
        "quiz": _has(acts, "quiz", known),
        "survey": _has(acts, "survey", known),
        "form_required": True if ("form" in acts or e.get("entry_type") == "form") else (False if known else None),
        "photo_required": _has(acts, "photo", known),
        "proof_required": True if e.get("needs_proof") else (_has(acts, "proof", known)),
        "proof_detail": e.get("proof_reason"),
        "watch_required": _has(acts, "watch", known),
        "review_required": True if re.search(r"후기|리뷰", needs) else _has(acts, "review", known),
        "visit_required": visit,
        "personal_info": found_personal or None,
        "one_entry_per_person": one,
        "eligibility": {
            "restricted": restricted,
            "reason": e.get("restriction_reason") if restricted else e.get("restriction_note"),
            "age": age,
            "who": e.get("who"),
        },
        "selection": e.get("mode"),
        "winners": e.get("count"),
        "free_confirmed": True if e.get("free") == "ok" else None,
        "tax_possible": True if e.get("tax") else None,
        "time_minutes": minutes,
    }

# 티끌모아 티끌

작은 경품도 모으면 즐거움이 된다. 무료 경품 이벤트를 종류별로 찾아보고 빠르게 응모하는 플랫폼입니다.

무료로 참여할 수 있는 경품·댓글·퀴즈·설문 이벤트를 매시간 자동으로 모아 보여주는 사이트입니다.
GitHub Actions(수집·빌드)와 GitHub Pages(호스팅)만 써서 월 운영비 0원으로 돌아갑니다.

## 구조

| 경로 | 역할 |
|---|---|
| `collector/sources/` | 출처별 수집 모듈. 키 없이 동작: korissue(원문 링크), 이벤트하우스, events.kr, 다나와, SBS, 부산, 복지부, 관광e배움터, 유벤트, 공공이벤트 블로그. 키·설정 필요: 유튜브, 네이버(블로그·카페·웹문서), 텔레그램 |
| `collector/extract.py` | 규칙 기반 추출: 기간·발표일·경품·참여 방법, 유료 조건·공연초대 제외(AI 미사용) |
| `collector/pipeline.py` | 수집 → 중복 병합 → 종료·링크 처리 → 급감 안전장치 → `data/` 저장 |
| `build.py` | `data/db.json` → `_site/`(건수·링크·스크립트 검증) |
| `web/template.html` | 사이트 화면(검색·정렬·필터·상세 창·저장) |
| `data/db.json` | 이벤트 DB(git 이력이 곧 백업) |
| `data/runs.json` | 실행 로그(최근 300회), 사이트의 `data/status.json`으로도 공개 |
| `data/overrides.json` | 관리자 승인·반려·수정 |
| `config/sources.json` | 출처 켜기/끄기, 수집 주기, 검색어, 텔레그램 채널 |
| `collector/agentfeed.py` | AI 에이전트용 피드(`feed/`): 진행 중 전체·일자별 신규·Atom·JSON Schema·경량 피드. 문서는 `web/feed/README.md` |
| `collector/notices.py` | 원문 공지 읽기: 새 이벤트·공지 주소가 바뀐 이벤트의 공지를 한 번 읽어 응모 조건 문장만 `data/notices.json`에 저장(실행당 80건·120초·동시 4호스트·호스트당 2초 간격 상한) |

## 에이전트·프로그램용 피드

화면은 JS로 그려지므로 프로그램은 HTML 대신 아래 정적 JSON을 쓴다(매시간 갱신, 한국 시간 `+09:00`).

- `https://changheo123.github.io/tikkeul/feed/events.json` — 진행 중 전체
- `…/feed/new/YYYY-MM-DD.json` — 그날 처음 등록된 이벤트(최근 30일), 목록은 `…/feed/new/index.json`
- `…/feed/atom.xml` — 새 이벤트 Atom, `…/feed/schema.json` — JSON Schema, `…/feed/README.md` — 필드 설명
- `…/feed/auto.json` — 진행 중이고 `auto_ok=true`인 것만, `…/feed/active-lite.json` — 진행 중 전체의 최소 필드(점수·순위 없음)
- 사이트 루트 `llms.txt`와 `index.html`의 `<link rel="alternate">`로도 찾을 수 있다
- 기존 `data/feed.json`(schema 1.0)은 그대로 유지한다

## 관리자 작업(GitHub 웹에서 파일 편집)

- **자동 수집 이벤트 승인**: `data/overrides.json`의 `approve`에 이벤트 id 추가 → '승인 전' 표시가 사라짐
- **잘못된 이벤트 내리기**: `reject`에 id 추가
- **내용 수정**: `"edit": {"id": {"end": "2026-11-30", "prize": "…"}}`
- **출처 끄기·주기 변경**: `config/sources.json`의 `enabled`, `interval_min`
- **텔레그램 채널 추가**: `config/sources.json` → `telegram.channels`에 공개 채널 이름(예: `"channelname"`)
- 이벤트 id는 사이트 `data/events.json`(진행 중), `data/archive.json`(종료), `data/db.json`에서 확인

저장하면 다음 정시 실행(매시 17분)에 반영됩니다. 바로 반영하려면 Actions → collect-and-deploy → Run workflow.

## 무료 API 키(선택)

저장소 Settings → Secrets and variables → Actions → New repository secret

| 이름 | 발급처 | 무료 한도 | 사용량 |
|---|---|---|---|
| `YT_API_KEY` | Google Cloud 콘솔 → YouTube Data API v3 사용 설정 → API 키 | 하루 10,000단위 | 3시간마다 검색 3회(300단위) → 하루 약 2,400단위 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | developers.naver.com → 애플리케이션 등록 → 검색 API | 하루 25,000회 | 2시간마다 6회 → 하루 72회 |

키가 없으면 해당 출처는 '연동 필요'로 표시되고 요청을 보내지 않습니다. 결제 수단 등록이 필요한 요금제는 쓰지 않습니다.

## 응모 폼·종료·참여 제한

- **`form_url`**: 네이버폼, 구글폼, 모아폼, 스모어를 글·HTML 링크에서 추출(`collector/forms.py`). `naver.me`·`forms.gle` 단축 링크는
  이동 주소를 확인해 폼일 때만 저장하고, 확인되지 않으면 `null`로 둔다. 이벤트하우스는 로그인 없이 `fgo.php?fnum=…&go=4`가
  가리키는 폼만 저장한다. 같은 폼 아이디는 한 행으로 합친다. `url`(원문 공지)은 그대로 유지
- **`status`**: 한국 날짜 기준 `end < 오늘`이면 `ended`. 연도 없는 일정이 올해·내년으로 밀렸어도, 게시일(인스타그램 게시물)이나
  본문에 적힌 지난 연도로 이미 끝난 행사는 바로 `ended`다. `end: null`은 유지하고, 한 번 종료된 이벤트는 다시 수집돼도 되살리지 않는다.
  종료 목록은 사이트 `data/archive.json`에만 있고, 메인 피드 `data/events.json`에는 진행 중만 담긴다
- **`is_restricted`**: 지역 거주·현장 방문 제한이면 `true`, 표현은 있지만 불확실하면 `null`(검토 대상), 아니면 `false`
  (`collector/restrict.py`, 사유는 `restriction_reason`). 잘못 판별된 건은 `data/overrides.json`의 `edit`로 고친다

- **`elec`·`elec_models`**: 전자기기 경품 종류와 원문에 적힌 모델명(`collector/electronics.py`). 체험단·액세서리는 제외하고, 모델명은 추측하지 않는다.
  `is_restricted`는 true/false만 쓰고, 불확실한 건은 `restriction_note`에 메모만 남긴다

## 자동 실행 방식

- **예약(cron)**: 매시 23분. GitHub 사정으로 늦거나 빠질 수 있음(이 저장소는 만든 직후 예약이 돌지 않았음)
- **이어달리기(next-run)**: push·수동·이어달리기 실행이 끝나면 50분 뒤 다음 실행을 `workflow_dispatch`로 요청.
  최근 2시간 안에 예약 실행이 있었으면 스스로 멈춘다. 멈추려면 Actions에서 진행 중인 next-run 작업을 취소
- **품질 점검(audit)**: 매 실행마다 유료·공연초대 재검사(제외), 마감일·원문 링크 이상은 '확인 필요' 표시, 완전 중복 병합

## 장애 대응

- **수집 실패**: 출처별로 격리되어 다른 출처·기존 데이터는 유지, 다음 정시에 재시도. 기록은 `data/runs.json`
- **진행 중 이벤트가 절반 아래로 급감**: 저장하지 않고 실패 처리(기존 DB·사이트 유지)
- **빌드·브라우저 점검 실패**: 배포하지 않아 이전 사이트 유지
- **잘못 반영된 데이터 되돌리기**: `git revert <수집 커밋>` 후 push

## 로컬 실행

```
pip install -r requirements.txt
python -m pytest tests -q
python -m collector.pipeline --force
python build.py
python -m http.server 8765 --directory _site
node tests/browser_test.mjs http://127.0.0.1:8765/ .
```

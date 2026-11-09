// 실제 Chrome으로 사이트를 열어 화면·기능을 점검한다(Chrome DevTools 프로토콜, 외부 패키지 없음)
// 사용: node tests/browser_test.mjs <사이트 URL> <스크린샷 폴더>
import { spawn } from "node:child_process";
import { writeFileSync, existsSync } from "node:fs";

const URL_ = process.argv[2] || "http://127.0.0.1:8765/";
const OUT = process.argv[3] || ".";
const CHROME = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium",
].find(p => p && existsSync(p));
const PORT = 9333;
const proc = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, "--no-first-run",
  "--no-sandbox", `--user-data-dir=${OUT}/chrome-prof`, "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let ws, seq = 0;
const pending = new Map();
function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((res, rej) => pending.set(id, { res, rej }));
}
async function ev(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description || ""));
  return r.result.value;
}
// 페이지가 다 그려질 때까지(카드가 생기고 문서 로드 완료) 최대 20초 기다린다
async function ready() {
  for (let i = 0; i < 80; i++) {
    await sleep(250);
    try { if (await ev(`document.readyState === 'complete' && document.querySelectorAll('.tk').length > 0`)) { await sleep(300); return; } } catch {}
  }
}
// 조건이 참이 될 때까지 최대 15초 기다린다(지연 로딩 확인용)
async function until(expr) {
  for (let i = 0; i < 60; i++) { if (await ev(expr).catch(() => false)) return true; await sleep(250); }
  return false;
}
async function shot(name) {
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, "base64"));
}

const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok: !!ok, detail }); };

try {
  let target;
  for (let i = 0; i < 120 && !target; i++) {  // Chrome 시작을 최대 30초 기다린다(러너가 느릴 때)
    await sleep(250);
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === "page"); } catch {}
  }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  const errors = [];
  ws.addEventListener("message", m => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { const p = pending.get(d.id); pending.delete(d.id); d.error ? p.rej(new Error(d.error.message)) : p.res(d.result); }
    if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.text);
    if (d.method === "Log.entryAdded" && d.params.entry.level === "error") errors.push(d.params.entry.text);
  });
  await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");

  // 화면 폭별 점검: 가로 넘침, 터치 영역, 카드 구성
  const WIDTHS = [["w360", 360, 780, true], ["w390", 390, 844, true], ["w430", 430, 932, true], ["w1280", 1280, 900, false], ["w1920", 1920, 1080, false]];
  for (const [label, w, h, mobile] of WIDTHS) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile });
    await send("Page.navigate", { url: URL_ });
    await ready();
    const m = await ev(`(() => {
      const box = e => e.getBoundingClientRect();
      const small = [...document.querySelectorAll('.cat, .go, .sort, .fbtn, .nav a')].filter(e => { const r = box(e); return r.width > 0 && (r.height < 44 || r.width < 44); }).map(e => e.textContent.trim());
      const over = [...document.querySelectorAll('.tk')].filter(t => box(t).right > innerWidth + 1 || t.scrollWidth > t.clientWidth + 1).length;
      const cols = new Set([...document.querySelectorAll('.tk')].slice(0, 12).map(t => Math.round(box(t).left))).size;
      return { sw: document.documentElement.scrollWidth, iw: innerWidth, cards: document.querySelectorAll('.tk').length, small: small.slice(0, 5), n: small.length, over, cols, nav: getComputedStyle(document.getElementById('nav')).position };
    })()`);
    check(`${label}: 가로 넘침·카드 깨짐 없음`, m.sw <= m.iw && m.over === 0 && m.cards > 0, `scrollWidth ${m.sw}/${m.iw}, 카드 ${m.cards}, 넘친 카드 ${m.over}`);
    const clip = await ev(`[...document.querySelectorAll('.pz')].filter(b => getComputedStyle(b).webkitLineClamp !== 'none' || b.scrollHeight > b.clientHeight + 1 || b.getBoundingClientRect().right > b.closest('.tk').getBoundingClientRect().right).length`);
    check(`${label}: 경품명 잘림 없음`, clip === 0, `잘린 카드 ${clip}`);
    check(`${label}: 터치 영역 44px 이상`, m.n === 0, m.small.join(" | "));
    const colsOk = mobile ? m.cols === 1 && m.nav === "fixed" : m.cols >= 2 && m.cols <= 3 && m.nav !== "fixed";
    check(`${label}: 열 수·메뉴 위치`, colsOk, `${m.cols}열, 메뉴 ${m.nav}`);
    await shot(`${label}-top`);
  }

  // 아래 기능 점검은 휴대폰 화면 기준
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Page.navigate", { url: URL_ }); await ready();
  await ev(`localStorage.clear()`);  // 지난 실행의 저장·참여 기록을 지우고 시작
  await send("Page.reload"); await ready();

  // 브랜드와 운영 정보
  const brand = await ev(`({ title: document.title, desc: document.querySelector('meta[name=description]').content, og: document.querySelector('meta[property="og:site_name"]').content,
    ld: JSON.parse(document.querySelector('script[type="application/ld+json"]').textContent).name, logo: document.querySelector('h1').textContent.trim().replace(/^티/, ''),
    img: document.querySelector('meta[property="og:image"]').content, chrome: document.querySelector('header').textContent + document.getElementById('tools').textContent + document.querySelector('.searchbar').textContent + document.getElementById('infoView').textContent })`);
  check("사이트명 변경(제목·설명·OG·구조화 데이터·로고)", brand.title.includes("티끌모아 티끌") && brand.desc.includes("티끌모아 티끌") && brand.og === "티끌모아 티끌" && brand.ld === "티끌모아 티끌" && brand.logo === "티끌모아 티끌" && /^https:.*assets\/og\.png$/.test(brand.img), JSON.stringify({ t: brand.title, l: brand.logo, i: brand.img }));
  check("옛 이름이 화면에 없음", !/틈틈|줍줍/.test(brand.title + brand.desc + brand.og + brand.chrome));
  for (const f of ["assets/icon-192.png", "assets/apple-touch-icon.png", "assets/og.png", "assets/site.webmanifest"]) {
    const st = await ev(`fetch(${JSON.stringify(f)}).then(r => r.status)`);
    check(`브랜드 파일 ${f}`, st === 200, String(st));
  }
  const homeOps = await ev(`(() => { const r = e => e.getClientRects().length > 0; return { src: r(document.getElementById('srcBox')), stats: r(document.querySelector('.stats')), order: ['h1', '#q', '#chips', '.sortbar', '#list'].map(q => Math.round(document.querySelector(q).getBoundingClientRect().top)) }; })()`);
  check("메인에 운영 정보 없음", !homeOps.src && !homeOps.stats);
  check("메인 순서: 로고·검색·카테고리·정렬·목록", homeOps.order.every((v, i, a) => i === 0 || a[i - 1] < v), homeOps.order.join(" < "));

  const total = await ev(`DATA.events.filter(e => e.status !== 'ended' && (!e.end || e.end >= kstToday()) && e.grade !== 'X' && e.is_restricted !== true).length`);
  check("기본 목록 건수 = 기본 노출 데이터", total === await ev(`cur.length`), `데이터 ${total} / 목록 ${await ev(`cur.length`)}`);
  const first = await ev(`({ onlyMain: DATA.events.every(e => e.is_restricted !== true && e.grade !== 'X' && e.status !== 'ended'), lazy: performance.getEntriesByType('resource').filter(r => /data\\/(extra|ended)\\.json/.test(r.name)).length, proof: DATA.events.every(e => e.needs_proof === true || e.needs_proof === false), dom: document.querySelectorAll('.tk').length })`);
  check("첫 화면은 기본 데이터·일부 카드만", first.onlyMain && first.lazy === 0 && first.dom === 40, JSON.stringify(first));
  check("needs_proof는 true/false만", first.proof);
  // 아래로 내리면 다음 카드가 이어서 그려진다
  await ev(`window.scrollTo(0, document.body.scrollHeight)`); await until(`document.querySelectorAll('.tk').length > 40`);
  check("스크롤로 이어서 표시", (await ev(`document.querySelectorAll('.tk').length`)) > 40);
  await ev(`window.scrollTo(0, 0)`);

  const catNames = await ev(`[...document.querySelectorAll('.cat')].map(c => c.textContent.trim().replace(/^\\S+?(?=[가-힣])/u, ''))`);
  check("카테고리 10개·아이콘", catNames.join() === "전체,기프티콘,커피,전자기기,음식,뷰티,상품권,여행·숙박,생활용품,기타" && await ev(`[...document.querySelectorAll('.cat i')].every(i => i.textContent.trim())`), catNames.join(" | "));

  // 카드: 날짜·경품명·응모 폼·이벤트 공지만
  const cards = await ev(`(() => { const bad = []; let form = 0, plain = 0, noDate = 0;
    const dateRx = /^(\\d{2}\\.\\d{2} 마감|마감일 미확인)$/;
    for (const e of cur) {
      const li = document.createElement('ol'); li.innerHTML = card(e); const t = li.firstElementChild;
      const kids = [...t.children].map(k => k.tagName).join();
      const as = [...t.querySelectorAll('a')].map(a => a.textContent.trim());
      const words = t.textContent.replace(/\\s+/g, ' ').trim();
      const exp = e.form_url ? ['응모 폼', '이벤트 공지'] : ['이벤트 공지'];
      const due = t.querySelector('.due').textContent;
      const name = t.querySelector('.pz').textContent.trim();
      if (kids !== 'P,H3,DIV' || as.join() !== exp.join() || !dateRx.test(due) || !name.includes(e.card) || words.replace(/\\s/g, '') !== (due + name + as.join('')).replace(/\\s/g, '')) bad.push(e.id);
      for (const a of t.querySelectorAll('a')) {
        const want = a.textContent.includes('응모 폼') ? e.form_url : e.url;
        if (a.target !== '_blank' || !/noopener/.test(a.rel) || a.href !== new URL(want).href) bad.push(e.id);
      }
      if (e.form_url) form++; else plain++;
      if (!e.end) noDate++;
    }
    return { n: bad.length, bad: bad.slice(0, 3), form, plain, noDate, total: cur.length }; })()`);
  check("카드 전체: 날짜·경품명·응모 폼·이벤트 공지만, 링크 정확", cards.n === 0 && cards.form > 0, JSON.stringify(cards));
  const noCardFake = await ev(`live.every(e => e.form_url ? true : !/응모 폼/.test((() => { const d = document.createElement('div'); d.innerHTML = links(e); return d.textContent; })()))`);
  check("폼이 확인되지 않으면 응모 폼 버튼 없음", noCardFake);

  // 카테고리 분류: 선택하면 해당 경품만, 같은 이벤트 중복 없음
  for (const k of ["gifticon", "coffee", "elec", "food", "beauty", "gift", "travel", "life", "etc"]) {
    await ev(`document.querySelector('.cat[data-k="${k}"]').click()`); await sleep(150);
    const r = await ev(`({ ok: cur.every(e => e.pcats.includes('${k}')), uniq: new Set(cur.map(e => e.id)).size === cur.length, n: cur.length, expect: live.filter(e => passBase(e) && e.pcats.includes('${k}')).length, pressed: document.querySelector('.cat[data-k="${k}"]').getAttribute('aria-pressed') })`);
    check(`카테고리 ${k}: 분류·중복 없음`, r.ok && r.uniq && r.n === r.expect && r.pressed === "true", `${r.n}건`);
  }
  await ev(`document.querySelector('.cat[data-k="coffee"]').click()`); await sleep(150);
  check("커피 쿠폰은 커피 카테고리에서 찾기", await ev(`cur.some(e => /커피\\s*쿠폰/.test(e.prize || ''))`));
  // 전자기기 세부 필터
  await ev(`document.querySelector('.cat[data-k="elec"]').click()`); await sleep(200);
  const subs = await ev(`[...document.querySelectorAll('#subchips .chip')].map(c => c.textContent)`);
  const key = await ev(`Object.keys(ELEC).find(k => cur.some(e => e.elec.includes(k))) || ''`);
  let subOk = !!key, subN = 0;
  if (key) { await ev(`document.querySelector('#subchips .chip[data-e="${key}"]').click()`); await sleep(200); subOk = await ev(`cur.length > 0 && cur.every(e => e.elec.includes('${key}'))`); subN = await ev(`cur.length`); }
  check("전자기기 세부 필터", subs.length === 8 && subOk, `${subs.join(" | ")}, ${key} ${subN}건`);
  await ev(`document.querySelector('.cat[data-k="all"]').click()`); await sleep(200);
  check("세부 필터는 전자기기 선택 시만 표시", await ev(`document.getElementById('subchips').hidden`));

  // 검색
  await ev(`(() => { const q = document.getElementById('q'); q.value = '스타벅스'; q.dispatchEvent(new Event('input')); })()`); await sleep(350);
  const sr = await ev(`({ n: cur.length, ok: cur.every(e => e.hay.includes('스타벅스')) })`);
  check("검색: '스타벅스' 결과가 모두 일치", sr.n > 0 && sr.ok, `${sr.n}건`);
  await ev(`(() => { const q = document.getElementById('q'); q.value = ''; q.dispatchEvent(new Event('input')); })()`); await sleep(350);

  // 정렬
  await ev(`document.querySelector('.sort[data-sort="new"]').click()`); await sleep(200);
  const seen = await ev(`cur.map(e => e.firstSeen || '')`);
  check("정렬: 최신 등록순", seen.every((v, i) => i === 0 || seen[i - 1] >= v), `${seen[0]} … ${seen.at(-1)}`);
  await ev(`document.querySelector('.sort[data-sort="due"]').click()`); await sleep(200);
  const dues = await ev(`cur.map(e => e.end).filter(Boolean)`);
  check("정렬: 마감 임박순", dues.every((v, i) => i === 0 || dues[i - 1] <= v), `${dues[0]} … ${dues.at(-1)}`);

  // 상세 필터(접이식)
  check("필터 메뉴는 기본 접힘", await ev(`document.getElementById('panel').hidden`));
  await ev(`document.getElementById('fBtn').click()`); await sleep(100);
  check("필터 메뉴 펼침", !(await ev(`document.getElementById('panel').hidden`)));
  await ev(`(() => { const s = document.getElementById('fType'); s.value = 'quiz'; s.dispatchEvent(new Event('change')); })()`);
  await ev(`(() => { const s = document.getElementById('fPlat'); s.value = 'yt'; s.dispatchEvent(new Event('change')); })()`); await sleep(200);
  const yt = await ev(`({ n: cur.length, ok: cur.every(e => e.plats.includes('yt') && ['댓글','퀴즈','투표'].includes(e.kind)) })`);
  check("필터: 유튜브 + 댓글·퀴즈", yt.n > 0 && yt.ok, `${yt.n}건`);
  await ev(`document.getElementById('fReset').click()`); await sleep(200);
  await ev(`(() => { const c = document.getElementById('fNoCost'); c.checked = true; c.dispatchEvent(new Event('change')); })()`); await sleep(200);
  check("필터: 제세공과금 있는 이벤트 제외", !(await ev(`cur.some(e => e.tax)`)));
  await ev(`document.getElementById('fReset').click()`); await sleep(200);
  await shot("mobile-filter");
  await ev(`document.getElementById('fBtn').click()`);

  // 상세 창
  await ev(`document.querySelector('#list .tk .pz').click()`); await sleep(300);
  const dlg = await ev(`({open: document.getElementById('dlg').open, link: document.querySelector('#dFoot a.note')?.href, title: document.getElementById('dTitle')?.textContent, fav: !!document.querySelector('#dFoot [data-act="fav"]')})`);
  check("상세 창 열림·공지 링크·저장 버튼", dlg.open && /^https?:/.test(dlg.link || "") && dlg.fav, `${dlg.title} → ${dlg.link}`);
  await shot("mobile-detail");

  // 저장·참여: 새로고침 후 유지, 기존 키 그대로
  const fid = await ev(`document.querySelector('#dFoot [data-act="fav"]').dataset.id`);
  await ev(`document.querySelector('#dFoot [data-act="fav"]').click()`); await sleep(150);
  await ev(`document.querySelector('#dFoot [data-act="done"]').click()`); await sleep(150);
  await ev(`document.getElementById('dClose').click()`);
  const keys = await ev(`({ fav: JSON.parse(localStorage.getItem('tt-fav')), done: JSON.parse(localStorage.getItem('tt-done')) })`);
  check("저장·참여가 기존 키(tt-fav·tt-done)에 기록", keys.fav.includes(fid) && keys.done.includes(fid));
  await send("Page.reload"); await ready();
  await ev(`location.hash = '#saved'`); await sleep(300);
  check("저장 화면: 새로고침 후 유지", await ev(`cur.length === 1 && cur[0].id === ${JSON.stringify(fid)} && document.querySelector('[data-v="fav"]').getAttribute('aria-current') === 'page' && document.getElementById('tools').hidden`));
  await shot("mobile-saved");
  await ev(`location.hash = '#joined'`); await sleep(300);
  check("참여 화면: 유지·흐리게 표시", await ev(`cur.length === 1 && cur[0].id === ${JSON.stringify(fid)} && !!document.querySelector('.tk.done')`));
  await ev(`location.hash = ''`); await sleep(300);
  await ev(`(() => { const c = document.getElementById('fHideDone'); c.checked = true; c.dispatchEvent(new Event('change')); })()`); await sleep(200);
  check("필터: 참여한 것 숨기기", !(await ev(`cur.some(e => e.id === ${JSON.stringify(fid)})`)));
  await ev(`document.getElementById('fReset').click()`); await sleep(200);
  // 이전 버전에서 저장한 기록(제한·종료 이벤트 포함)도 보여야 한다
  const legacy = await ev(`fetch('data/ended.json').then(r => r.json()).then(d => d.events[0].id)`);
  await ev(`localStorage.setItem('tt-fav', JSON.stringify([${JSON.stringify(fid)}, ${JSON.stringify(legacy)}]))`);
  await send("Page.reload"); await ready();
  await ev(`location.hash = '#saved'`);
  await until(`loaded.ended && cur.length === 2`); await sleep(300);
  const old = await ev(`({ n: cur.length, ended: document.querySelector('#list .tk:last-child .due').textContent })`);
  check("예전 저장 기록 호환(종료 이벤트 포함)", old.n === 2 && old.ended.includes("종료"), JSON.stringify(old));
  await ev(`document.querySelector('#list .tk:last-child .pz').click()`); await sleep(250);
  check("종료 이벤트 상세 창", await ev(`document.getElementById('dlg').open && document.getElementById('dBody').textContent.includes('이벤트 공지')`));
  await ev(`document.getElementById('dClose').click()`);
  await ev(`localStorage.clear()`);

  // 안내 화면: 운영 정보·종료 이벤트
  await ev(`location.hash = '#info'`); await sleep(300);
  await shot("mobile-info");
  const goneN = await ev(`m.ended`);
  await ev(`document.getElementById('endedBox').open = true`);
  await until(`loaded.ended && document.querySelectorAll('#ended li').length > 0`); await sleep(300);
  const ended = await ev(`({ended: document.querySelectorAll('#ended li').length, src: document.querySelectorAll('#src tr').length, stamp: document.getElementById('stamp').textContent})`);
  check("안내 화면: 종료 목록·출처 현황·수집 상태", ended.src >= 8 && ended.ended === goneN && ended.stamp.includes("자동 수집"), `종료 ${ended.ended}/${goneN}건, 출처 행 ${ended.src}개`);
  await ev(`location.hash = ''`); await sleep(300);

  // 제한 이벤트: 기본 제외, 필터로 포함, 종료 이벤트는 피드에 없음
  const r0 = await ev(`cur.length`);
  const leak = await ev(`cur.filter(e => e.is_restricted === true || e.status === 'ended').length`);
  await ev(`(() => { const c = document.getElementById('fRestricted'); c.checked = true; c.dispatchEvent(new Event('change')); })()`);
  await until(`loaded.extra`); await sleep(500);
  const r1 = await ev(`cur.length`);
  const nRes = await ev(`live.filter(e => e.grade !== 'X' && e.is_restricted === true).length`);
  check("제한 이벤트 기본 제외·필터로 포함", leak === 0 && r1 - r0 === nRes, `기본 ${r0}, 포함 ${r1}, 제한 ${nRes}, 피드에 섞인 제한·종료 ${leak}`);
  const opened = await ev(`(() => { const e = cur.find(e => e.is_restricted === true && e.restriction_reason); if (!e) return false; openDetail(e.id); return true; })()`); await sleep(250);
  if (opened) {
    check("상세 창에 제한 사유 표시", await ev(`document.getElementById('dBody').textContent.includes('참여 제한 사유')`));
    await ev(`document.getElementById('dClose').click()`);
  }

  // 전체 데이터(제한·조건 많음 포함)에서 검색 속도
  await ev(`(() => { const c = document.getElementById('showX'); c.checked = true; c.dispatchEvent(new Event('change')); })()`); await sleep(500);
  const speed = await ev(`(() => { state.q = '커피'; const t = performance.now(); render(); const ms = performance.now() - t; const n = cur.length, base = live.length; state.q = ''; render(); return { ms: Math.round(ms), n, base, ids: all.length }; })()`);
  check("전체 데이터 검색 100ms 이내", speed.ms < 100 && speed.base >= 2500, JSON.stringify(speed));
  await ev(`document.getElementById('fReset').click()`); await sleep(300);
  check("is_restricted는 true/false만", await ev(`all.every(e => e.status === 'ended' || e.is_restricted === true || e.is_restricted === false)`));
  check("모든 링크가 http(s)", (await ev(`[...document.querySelectorAll('a.go')].filter(a => !/^https?:\\/\\//.test(a.href)).length`)) === 0);

  check("스크립트 오류 없음", errors.length === 0, errors.slice(0, 3).join(" / "));
} catch (e) {
  check("테스트 실행", false, String(e));
} finally {
  writeFileSync(`${OUT}/browser_results.json`, JSON.stringify(results, null, 1));
  for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}  ${r.detail}`);
  // GitHub Actions에서는 실패 항목을 주석(annotation)으로 남겨 로그인 없이도 원인을 볼 수 있게 한다
  if (process.env.GITHUB_ACTIONS) for (const r of results.filter(x => !x.ok)) console.log(`::error::브라우저 점검 실패: ${r.name} ${String(r.detail).replace(/\n/g, " ").slice(0, 300)}`);
  try { ws?.close(); } catch {}
  proc.kill();
  process.exit(results.every(r => r.ok) ? 0 : 1);
}

# 벤더링 기록 — @tanstack/virtual-core 3.17.8 (MIT)

이 디렉토리의 `*.js` 는 npm 배포본 `@tanstack/virtual-core@3.17.8` 의 `dist/esm/*` 이고,
`LICENSE` 는 같은 배포본의 것이다. CDN 을 쓰지 않는 이유는 README 참조 — 이 앱은 내
컴퓨터에서 도는 로컬 앱이고 오프라인에서도 떠야 한다.

## 상류에서 바꾼 것 (딱 하나)

`index.js`(6곳)·`utils.js`(1곳)의 토큰 `process` + `.env.NODE_ENV` 를 문자열 리터럴
`"production"` 으로 치환했다. `lazy-measurements.js` 는 해당 참조가 없어 그대로다.

**왜 바꿔야 했나.** 상류 ESM 배포본은 *번들러가 있다는 전제*로 그 환경변수 참조를
그대로 둔다 — webpack/rollup/vite 의 `define` 이 빌드 시점에 리터럴로 치환해 주기
때문이다. 이 앱은 **빌드 단계가 없고** 브라우저가 이 파일을 그대로 받는데, 브라우저에는
그 Node 전역이 없다. 그래서 그 참조는 런타임 `ReferenceError` 가 된다.

**이게 왜 그냥 죽는 것보다 나빴나.** 그 참조들은 모듈 최상단이 아니라 `Virtualizer`
*생성자 안*에 있다. 그래서 모듈 평가는 성공하고 `window.TanStackVirtual` 도 정상으로
보인다 — "라이브러리가 없으면 알린다"는 방어를 **통과한 뒤** `new Virtualizer(...)`
에서 터졌다. 결과는 `boot()` 이 통째로 중단되고 `wire()` 에 도달하지 못하는 것,
즉 **모든 버튼이 죽고 안내 한 줄도 안 나오는** 조용한 전면 마비였다.

**우리가 한 것은 로직 수정이 아니다.** 번들러가 `NODE_ENV=production` 으로 했을 치환을
미리 해 둔 것이고, 결과는 상류의 프로덕션 빌드와 같다(개발용 디버그 계측이 꺼진다).
상류가 브라우저 직행용 배포본을 따로 내지 않으므로 이 치환이 정석 경로다.

## 상류 버전을 올릴 때 (기계적으로 — 이것 말고 손대지 말 것)

1. `npm pack @tanstack/virtual-core@<ver>` 로 받은 `dist/esm/*.js` 와 `LICENSE` 로 덮어쓴다.
2. 그 토큰을 전부 `"production"` 으로 치환한다.
3. 각 파일 맨 위에 출처·개변 여부 주석을 다시 붙이고 이 문서의 버전을 갱신한다.
4. `python -m pytest tests/test_vendor_assets.py tests/test_browser_smoke.py -q` 를 돌린다.

## 이 디렉토리의 규칙

`tests/test_vendor_assets.py` 는 **원문 전체를 그대로** 훑어 Node 전용 전역을 찾는다
(주석을 벗겨내면 검사에 구멍이 생기므로 벗기지 않는다). 그러므로 **주석에도 금지 토큰을
문자 그대로 적지 않는다** — 이 문서가 그 설명을 대신 맡는 이유다.

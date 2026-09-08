/*
 * ⭐ **화면이 쓰는 그 공식**을 파이썬 테스트에서 부를 수 있게 하는 한 줄짜리 창구.
 *
 * 왜 이런 것이 필요한가: 카운트는 **화면에서 계산되는 파생값**이고, 그 공식의
 * 유일한 원천은 `static/js/reads.js` 다. 2-인스턴스 실측(실제 git 왕복으로
 * "카운트 1 → 0")을 파이썬에서 하면서 공식을 파이썬으로 한 번 더 적으면, 그
 * 순간 원천이 둘이 되어 둘이 어긋나도 테스트가 통과한다.
 *
 * 그래서 흉내내지 않고 **진짜 모듈을 그대로 부른다.** 입력은 stdin 의 JSON:
 *
 *     {"participants": [...], "messages": [{"id": "...", "sender": "..."}, ...]}
 *
 * 출력은 메시지마다의 카운트 배열 (stdout, JSON 한 줄).
 */

import path from 'node:path';
import url from 'node:url';

const here = path.dirname(url.fileURLToPath(import.meta.url));
const reads = await import(
  url.pathToFileURL(
    path.resolve(here, '..', '..', 'src', 'gitwire_chat', 'static', 'js', 'reads.js')
  ).href
);

let raw = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) { raw += chunk; }
const input = JSON.parse(raw || '{}');
const list = input.participants || [];
const messages = input.messages || [];
process.stdout.write(JSON.stringify(
  messages.map((m) => reads.countUnread(list, m))
) + '\n');

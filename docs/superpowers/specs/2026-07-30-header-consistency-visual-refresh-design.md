# 헤더 일관성 버그 수정 · citec-wiki-qa 시각 언어 차용 — 설계

## 배경

2026-07-29에 `apps/web/public/js/nav.js` 공용 내비를 도입해 12개 앱 페이지의
상단 메뉴를 통일했다고 판단했으나, 사용자가 실사용 중 다음을 재현했다:

> "상단 메뉴가 화면마다 모두 다르게 표시된다. Admin도 우측에 있다가 Insight
> 메뉴 누르면 왼쪽으로 왔다가 문서 클릭하면 없어졌다가... 일관성이 전혀 없다."

원인 조사 결과 세 가지 실제 결함을 확인했다:

1. **`nav.js`의 `<details open>` 버그** — `apps/web/public/js/nav.js`가
   검색 관련 6개 페이지(search/chat/si/tickets/analytics/capacity)에서
   `<details class="nav-dd" open>`로 렌더링해, 해당 페이지에서는 드롭다운
   패널이 항상 펼쳐진 채로 로드된다. 나머지 6개 페이지(home/bundles/insights/
   admin/doc/login)에서는 접혀 있다. 페이지군에 따라 메뉴 모양 자체가 달라
   보이는 직접적 원인.
2. **`.top`/`.top a` CSS가 12개 페이지에 각각 복제**되어 있고 이미 서로
   달라져 있다 (`align-items:center`가 admin/chat/search/doc 4개 페이지에는
   있고 나머지 8개에는 없음). `nav.js`는 구조(HTML)만 공용화했을 뿐 스타일은
   페이지별 로컬 `<style>`에 계속 의존했기 때문에, 앞으로도 언제든 다시
   달라질 수 있는 구조였다.
3. **`/docs/*.html`은애초에 공용 내비 대상이 아니었다** — 문서 허브
   12개 페이지(스크립트 생성 9개 + 수기 작성 `docs/index.html`,
   `docs/design.html`)는 자체 헤더(`홈`, `문서 목록` 2개 링크만) 또는
   헤더 자체가 없어(`design.html`) "문서 클릭하면 없어진다"는 현상의
   원인이다.

추가로 사용자는 `~/tmp/citec-wiki-qa`(동일 팀의 자매 프로젝트)의 다크
헤더·Samsung SDS 브랜드 컬러·Pretendard/JetBrains Mono 타이포그래피를
citec-kb에도 차용하길 원한다. 좌측 rail·터미널 검색창 등 대시보드형
레이아웃 전체를 이식하는 것은 범위에서 제외하고, **헤더·색상·타이포**만
차용하기로 확정했다 (기존 12개 페이지의 카드형 단일 컬럼 레이아웃은 유지).

## 목표

1. `nav.js`의 `open` 버그를 제거해 6개/6개로 나뉘던 렌더링 차이를 없앤다.
2. 헤더 CSS(`.top`, `.top a`, 드롭다운, Admin 구분선)를 페이지별 로컬
   `<style>`에서 완전히 제거하고, 새 공용 스타일시트
   `apps/web/public/css/theme.css` 하나로 단일화해 향후 재발(drift)을
   구조적으로 막는다.
3. `/docs/` 허브(11개 페이지, `design.html` 제외 — 아래 비목표 참고)에도
   동일한 공용 헤더를 적용한다.
4. Pretendard·JetBrains Mono 폰트 파일을 레포에 내장(vendor)하고 `theme.css`
   에서 `@font-face`로 로드해, CDN 없이 폐쇄망 운영서버에서도 동일하게
   렌더링되게 한다.
5. 헤더를 wiki-qa 스타일의 다크 네이비 바(`#0C111F`)로, 브랜드 색상을
   Samsung SDS 블루(`#1428A0`)로 교체한다.

## 비목표

- 좌측 rail·터미널 스타일 검색창 등 wiki-qa의 대시보드형 페이지 레이아웃
  이식 (기존 카드형 단일 컬럼 레이아웃 유지)
- 다크모드 토글 추가 (헤더만 항상 다크, 페이지 본문은 기존 라이트 유지 —
  wiki-qa도 헤더는 테마 무관하게 항상 다크였으므로 동일한 패턴)
- 검색/Fast QA 등 각 페이지의 기능 변경
- `docs/design.html`의 헤더 구조 변경 — 이 페이지는 자체 hero 헤더 +
  좌측 TOC(`<nav class="nav">`)를 가진 독립형 리포트 문서이며 `.top` 공용
  헤더가 애초에 없다. 이번 작업에서는 `theme.css`를 링크해 폰트만
  적용하고(이미 `docs.css`가 `var(--font)`/`var(--mono)`로 Pretendard/
  JetBrains Mono를 참조하고 있어 자동 적용됨), 헤더 구조 자체는 건드리지
  않는다. 구조 통일은 별도 작업으로 분리.
- 네비 라벨 영문화 (wiki-qa는 SEARCH/WIKI/ADMIN처럼 영문 대문자 mono
  탭이지만, citec-kb는 기존 한글 라벨(검색/Fast QA/유사장애 등)을 유지한다.
  한글은 대소문자 개념이 없고 작은 크기의 mono 폰트로는 가독성이 떨어지므로
  내비 라벨은 Pretendard sans 유지, JetBrains Mono는 기존에 이미
  monospace를 쓰던 요소(코드/미리보기 블록)에만 적용한다.)

## 설계

### 1. `nav.js` 버그 수정

`render()` 함수에서 `<details>`에 `open` 속성을 붙이는 로직을 제거한다.
활성 상태 표시는 `<summary>`의 `nav-current` 클래스만으로 한다.

```js
// Before
html += '<details class="nav-dd"' + (searchCurrent ? " open" : "") + ">";
// After
html += '<details class="nav-dd">';
```

`summary`에 `nav-current`/`aria-current`를 붙이는 로직은 그대로 유지한다
(펼쳐지진 않지만 활성 표시는 계속 됨).

`nav.js`의 `injectStyle()` 함수(및 그것이 만들던 `<style id="nav-style">`
인라인 주입)는 **완전히 삭제**한다. 이후 `nav.js`는 순수하게 DOM 구조만
만들고, 모든 시각 스타일은 `theme.css`가 담당한다 — 스타일 소스가 JS 문자열과
정적 CSS 파일 두 곳으로 나뉘어 있던 것을 하나로 합친다.

### 2. 신규 공용 스타일시트 `apps/web/public/css/theme.css`

새 파일. 두 부분으로 구성:

**(a) `@font-face` — 로컬 폰트 파일 참조 (CDN 없음)**

```css
@font-face {
  font-family: "Pretendard";
  src: url("/fonts/Pretendard-Regular.woff2") format("woff2");
  font-weight: 400; font-display: swap;
}
/* + Medium(500), SemiBold(600), Bold(700) 동일 패턴 */
@font-face {
  font-family: "JetBrains Mono";
  src: url("/fonts/JetBrainsMono-Regular.woff2") format("woff2");
  font-weight: 400; font-display: swap;
}
/* + Medium(500), SemiBold(600), Bold(700) 동일 패턴 */
```

**(b) 헤더/내비 크롬 스타일 — 모든 페이지의 로컬 `.top` 규칙을 대체**

```css
.top {
  background: #0C111F;
  border-bottom: 1px solid #1A2238;
  padding: 12px 20px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  align-items: center;
}
.top a {
  color: rgba(255,255,255,.68);
  text-decoration: none;
  font-weight: 600;
  font-size: 14px;
  transition: color .15s;
}
.top a:hover { color: #fff; }
.top a.nav-current,
.top summary.nav-current { color: #fff; }
.top .nav-dd summary { cursor: pointer; list-style: none; }
.top .nav-dd summary::-webkit-details-marker { display: none; }
.top .nav-dd-menu {
  position: absolute; top: 100%; left: 0; margin-top: 6px;
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  box-shadow: 0 10px 28px rgba(15,23,42,.18);
  padding: 6px; display: flex; flex-direction: column; min-width: 140px; z-index: 20;
}
.top .nav-dd-menu a { color: #1428A0; padding: 6px 10px; border-radius: 6px; white-space: nowrap; }
.top .nav-dd-menu a:hover { background: #f1f5f9; }
.top .nav-admin { margin-left: auto; padding-left: 14px; border-left: 1px solid rgba(255,255,255,.18); }
```

(`.nav-dd { position: relative; }`도 함께 포함 — 기존 `nav.js`
`injectStyle()`에 있던 규칙을 그대로 이관.)

### 3. 12개 앱 페이지 수정 (index/search/chat/si/tickets/analytics/capacity/
bundles/insights/admin/doc/login)

각 페이지에서:

- `<head>`에 `<link rel="stylesheet" href="/css/theme.css">` 추가
  (다른 `<link rel="stylesheet">`와 같은 위치, `markdown.css`/`doclink.css`
  보다 먼저 — 헤더가 가장 먼저 스타일 적용되어야 FOUC 없음).
- 로컬 `<style>` 블록에서 `.top { ... }` / `.top a { ... }` 규칙 **삭제**
  (이제 `theme.css`가 전담).
- `:root`의 `--primary` 값을 `#1d4ed8` → `#1428A0`으로 변경.
- `body`의 `font-family` 선언 맨 앞에 `'Pretendard'`를 추가
  (예: `font-family: system-ui, "Noto Sans KR", sans-serif;` →
  `font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif;`).

`nav.js`가 그대로 각 페이지의 `#topNav`에 구조를 렌더링하는 것은 변경 없음
(2026-07-29 작업의 `<script src="/js/nav.js">` 포함 위치·`data-page` 값은
유지).

### 4. `/docs/` 허브 — 9개 스크립트 생성 페이지

`scripts/render_docs_html.py`의 `TOP_NAV` 템플릿을 앱 페이지와 동일한
패턴으로 교체:

```python
TOP_NAV = """<div class="top" id="topNav" data-page="docs"></div>"""
```

생성되는 각 HTML의 `<head>`에 `<link rel="stylesheet" href="/css/theme.css">`
와 `<script src="/js/nav.js"></script>`를 추가하도록 스크립트의 HTML 템플릿
부분을 수정한 뒤, `.venv/bin/python scripts/render_docs_html.py`를 재실행해
9개 출력 파일(`ai-agent-guide.html`, `deploy.html`, `external-api.html`,
`implementation-plan.html`, `mcp.html`, `oidc-idp-setup.html`,
`packet-analysis-mcp-guide.html`, `phase2-pilot-checklist.html`,
`query-catalog-analysis.html`)을 재생성한다. 이 9개 파일은 손으로 편집하지
않는다 (스크립트 헤더의 기존 경고 그대로 준수).

`docs.css`는 이미 `--font`/`--mono` 토큰으로 `"Pretendard"`/`"JetBrains
Mono"`를 이름으로 참조하고 있으므로(`apps/web/public/css/docs.css:28-29`),
`theme.css`가 로드되어 두 폰트가 실제로 사용 가능해지는 순간 별도
`docs.css` 수정 없이 자동 적용된다.

### 5. `docs/index.html` (수기 작성)

9개 생성 페이지와 동일한 패턴을 수기로 적용: `.top` 하드코딩 링크 2개
(`홈`, `문서 목록`)를 `<div class="top" id="topNav" data-page="docs"></div>`
로 교체하고 `theme.css` + `nav.js`를 링크한다.

### 6. `docs/design.html`

**변경 없음** (비목표 참고) — `<link rel="stylesheet" href="/css/theme.css">`
한 줄만 추가해 폰트가 로드되게 한다. 자체 hero 헤더/좌측 TOC 구조는 그대로
둔다.

### 7. 폰트 파일 벤더링

`apps/web/public/fonts/`에 8개 `.woff2` 파일을 커밋:

- `Pretendard-Regular.woff2` (400), `Pretendard-Medium.woff2` (500),
  `Pretendard-SemiBold.woff2` (600), `Pretendard-Bold.woff2` (700)
- `JetBrainsMono-Regular.woff2` (400), `JetBrainsMono-Medium.woff2` (500),
  `JetBrainsMono-SemiBold.woff2` (600), `JetBrainsMono-Bold.woff2` (700)

출처: Pretendard는 SIL Open Font License 1.1, JetBrains Mono는 Apache
License 2.0 — 둘 다 재배포 가능. 구현 시 공식 배포처(Pretendard GitHub
releases의 `static/woff2` 서브셋, JetBrains Mono GitHub releases)에서
직접 다운로드해 커밋한다 (이 개발 환경은 인터넷 접근이 가능하나, 폐쇄망
운영서버는 레포에 커밋된 파일만 배포 번들로 받으므로 CDN 요청이 발생하지
않는다 — `docs/DEPLOY.md`의 code 번들 패턴과 일치).

## 리스크 / 참고

- 헤더가 흰색(`#fff`)에서 다크 네이비(`#0C111F`)로 바뀌므로, 각 페이지가
  헤더 바로 아래 배치한 요소(`doc.html`의 `navTitle` sibling div 등)의
  주변 여백이 시각적으로 어색해지지 않는지 확인 필요 — 다만 `navTitle`
  div는 `.top` 바깥의 별도 블록이라 색상 변경의 영향은 받지 않는다.
- `nav.js`의 `injectStyle()` 삭제로 `<style id="nav-style">` 동적 주입이
  없어지므로, `theme.css` `<link>` 태그를 빠뜨린 페이지가 있으면 헤더가
  완전히 스타일 없이(unstyled) 보인다 — 구현 계획의 검증 단계에서 12개 앱
  페이지 + `docs/index.html` + 9개 생성 페이지 전부에 대해 `theme.css`
  링크 존재를 grep으로 확인한다.
- 폰트 파일 4×2=8개, 각 서브셋 용량에 따라 총 수백 KB~1MB 내외 예상 —
  `scripts/out.sh`는 `for d in apps config mcp-server scripts deploy
  packages; do ... done`(`scripts/out.sh:369`)로 `apps/` 디렉터리 전체를
  통째로 복사하므로 `apps/web/public/fonts/`·`css/theme.css`는 별도 스크립트
  수정 없이 자동으로 `--code` 번들에 포함된다.

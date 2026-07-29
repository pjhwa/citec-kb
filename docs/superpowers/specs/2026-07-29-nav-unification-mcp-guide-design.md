# 상단 내비게이션 통합 · MCP 연결 안내 추가 — 설계

## 배경

`apps/web/public/*.html` 12개 정적 페이지(index, search, chat, si, tickets, analytics,
capacity, admin, bundles, insights, doc, login)는 각자 `<div class="top">...</div>`에
상단 메뉴 링크를 하드코딩하고 있다. 페이지마다 노출되는 링크 개수·순서가 달라
일관성이 없다(예: `admin.html`은 검색/Insight/번들/Admin/Login/문서만 있고,
`si.html`은 검색/Fast QA/유사장애/기간/집계/공수/번들/Admin/Login/문서를 모두 노출).

또한 홈 화면(index.html)에 Claude MCP 서버 연결 방법에 대한 안내가 없다.

## 목표

1. 12개 페이지의 상단 메뉴를 하나의 공용 컴포넌트로 통일한다.
2. 검색 관련 6개 항목(검색/Fast QA/유사장애/기간 지원건/집계/공수·대수)을
   드롭다운 메뉴 하나로 묶는다.
3. Admin 메뉴를 우측으로 이동하고 구분선으로 시각적으로 분리한다.
4. Login 메뉴 링크를 제거한다(로그인 기능 자체는 유지, 직접 URL 접근만 가능).
5. 홈 화면 첫 화면에 Claude MCP 연결 안내(Claude Code CLI 명령 / Claude Desktop
   JSON)를 추가하고, 운영 서버 IP는 브라우저가 현재 접속 중인 호스트명을 읽어
   자동으로 채운다.

## 비목표

- 로그인/인증 기능 자체의 구현/삭제 (파일·API는 그대로 유지)
- 검색/Fast QA 등 각 전문 페이지의 기능 변경
- 서버측 API 신규 엔드포인트 추가 (IP는 클라이언트에서 `location.hostname`으로 처리)

## 설계

### 1. 공용 내비 컴포넌트 (`apps/web/public/js/nav.js`)

신규 파일. 페이지 로드 시 `#topNav` 요소(속성 `data-page="<page-id>"`)를 찾아
그 안에 통일된 메뉴를 렌더링한다.

메뉴 구성 (좌→우):

- `홈` (로고 역할, `/`)
- `검색 ▾` — `<details class="nav-dd"><summary>검색 ▾</summary><div class="nav-dd-menu">…</div></details>`
  네이티브 패턴 드롭다운. 하위 항목: 검색(`/search.html`) · Fast QA(`/chat.html`) ·
  유사장애(`/si.html`) · 기간 지원건(`/tickets.html`) · 집계(`/analytics.html`) ·
  공수·대수(`/capacity.html`)
- `번들` (`/bundles.html`)
- `Insight` (`/insights.html`)
- `문서` (`/docs/`)
- `API` (`/api/docs`, `target="_blank"`)
- (우측, `margin-left:auto` + `border-left:1px solid var(--border)` 로 구분) `Admin` (`/admin.html`)

`Login` 링크는 메뉴에 포함하지 않는다.

현재 페이지는 `data-page` 값과 일치하는 링크에 `aria-current="page"` +
약한 강조 스타일(밑줄/굵기)을 부여한다.

색상/폰트는 각 페이지에 이미 정의된 CSS 변수(`--primary`, `--border`, `--muted`)를
그대로 참조하므로 페이지별 별도 스타일 재정의가 필요 없다. `nav.js`는 최초 실행 시
`<style id="nav-style">`를 한 번 주입해 `.nav-dd`, `.nav-dd-menu`, `.top .admin-link`
등 드롭다운/구분선 스타일을 정의한다.

### 2. 12개 페이지 마크업 변경

각 페이지에서:

- 기존 `<div class="top"> ... 하드코딩된 <a> 목록 ... </div>` 를
  `<div class="top" id="topNav" data-page="<id>"></div>` 로 교체한다.
  - `<id>` 매핑: index→`home`, search→`search`, chat→`chat`, si→`si`,
    tickets→`tickets`, analytics→`analytics`, capacity→`capacity`,
    admin→`admin`, bundles→`bundles`, insights→`insights`, doc→`doc`,
    login→`login`
- `</body>` 직전(다른 스크립트들과 함께)에 `<script src="/js/nav.js"></script>`
  를 추가한다. 페이지별 기존 스크립트 로드 순서는 유지하되, DOM 조작이 필요하므로
  다른 인라인 스크립트보다 먼저 `.top`을 채우도록 가장 먼저 로드한다.
- `search.html`, `chat.html`, `admin.html`에 있는
  `CitecAuth.mountChip(".top")` 호출을 제거한다(로그인 칩이 다시 붙어
  Login 링크가 재노출되는 것을 방지).
- `doc.html`의 `<span id="navTitle">...</span>`는 `.top` 내부가 아니라
  `#topNav` 바로 다음 형제 요소로 이동한다(문서 제목 표시 기능은 유지).

### 3. 홈 화면 MCP 연결 안내 (`index.html`)

`.ask-card` 바로 아래(첫 화면 스크롤 없이 보이는 위치)에 새 카드 섹션을 추가한다.

- 안내 문구: "Claude에서 이 서버를 MCP로 연결하기"
- 서버 주소는 `location.hostname`으로 읽고(값이 비어 있으면 `localhost`로
  대체), 포트는 고정값 `8577`을 사용해 `http://<host>:8577/mcp` 형태로 조합한다.
- **Claude Code** 섹션: 아래 명령을 `<pre><code>`로 표시, 복사 버튼 포함
  ```
  claude mcp add --scope user --transport http wiki-mcp http://<host>:8577/mcp
  ```
- **Claude Desktop** 섹션: 아래 JSON을 `<pre><code>`로 표시, 복사 버튼 포함
  ```json
  {
    "mcpServers": {
      "wiki-mcp": {
        "url": "http://<host>:8577/mcp",
        "transport": "streamable-http"
      }
    }
  }
  ```
- 복사 버튼은 `navigator.clipboard.writeText`를 사용하고, 클릭 시 짧게
  "복사됨" 피드백을 보여준다(기존 페이지의 `.ghost` 버튼 스타일 재사용).

## 리스크 / 참고

- `nav.js`가 모든 페이지에서 `.top`을 완전히 대체하므로, 페이지 로드 시
  `nav.js` 실패(404 등) 시 상단 메뉴가 비어버릴 수 있다. 파일이 정적 자산으로
  함께 배포되므로 별도 fallback은 두지 않는다(다른 정적 JS 파일들도 동일 전제).
- `location.hostname`은 사용자가 실제 접속한 주소를 그대로 반영하므로 사설
  IP/도메인 어떤 경우에도 별도 서버 호출 없이 동작한다.

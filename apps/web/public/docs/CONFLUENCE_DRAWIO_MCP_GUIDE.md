# Confluence draw.io 다이어그램 MCP 가이드 (Claude 전용)

**대상:** Confluence 페이지에 있는 draw.io 다이어그램을 읽거나 새로/수정해서 써야 하는 Claude 세션.
**목적:** `citec-kb` MCP를 통해 Confluence REST API를 간접 호출해, (1) 아직 page_id를 모르는
상태에서 페이지를 찾고, (2) 페이지 위의 다이어그램 존재 여부와 원본 XML을 정확히 파악하고,
(3) mxGraph XML을 안전하게 편집해 다시 써 넣는 전체 흐름을 안내한다.

관련 문서: [MCP.md](./MCP.md) · 설계 스펙 `docs/superpowers/specs/2026-08-03-confluence-drawio-integration-design.md`

경로: `Claude ↔ mcp-server (kb_confluence_* 4개 도구) ↔ apps/api /v1/confluence/* ↔ Confluence REST API`

---

## 0. 핵심 원칙

1. **page_id를 모르면 반드시 `kb_confluence_find_pages`부터 호출한다.** space_key(공간명)는
   조직 개편·이관 등으로 바뀔 수 있으므로, 이전 세션에서 봤던 값이나 추측값을 그대로
   재사용하지 않는다. 매번 사용자가 알려준(또는 확인 가능한) space_key를 인자로 넘긴다.
2. **쓰기 전에 반드시 먼저 읽는다 (get-before-put).** 이미 존재하는 다이어그램을 수정할
   때는 `kb_confluence_get_diagram`으로 현재 XML을 가져와 파싱한 뒤, 필요한 부분만 바꾼
   XML을 다시 써 넣는다. 원본을 보지 않고 새로 만든 XML로 덮어쓰면 기존 도형·연결·ID가
   전부 소실된다.
3. **mxGraph XML의 최소 구조(§4)를 이해하고, 최소 변경 원칙을 지킨다.** 관계없는 셀의
   `id`를 바꾸거나 삭제하지 않는다 — 다른 셀의 `parent`/`source`/`target`이 그 id를
   참조하고 있을 수 있다.
4. **모든 `.drawio` 파일은 `<mxfile><diagram id=...>...</diagram></mxfile>`로 감싸야 한다
   (§4.2).** `<mxGraphModel>`만 최상위로 올리면 Confluence는 업로드는 받아주지만(200 OK)
   뷰어가 렌더링에 실패한다 — 반드시 `<mxfile>` 래퍼와 원본 `<diagram id>`를 유지한다.
   받은 XML이 base64로 압축된 형식(§4.4)이면 절대 새로 지어내지 말고 사용자에게 보고한다.
5. **오류 코드별로 다르게 대응한다(§5).** 503(연동 미설정)은 사용자에게 즉시 보고하고
   재시도하지 않는다. 404(페이지/다이어그램 없음)는 오탈자·잘못된 diagram_name일 가능성을
   먼저 의심한다. 502(인증 실패)는 API 서버 측 `CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD`
   설정 문제이므로 Claude가 재시도로 해결할 수 없다 — 사용자에게 보고한다.
6. **매크로 삽입은 이 도구의 책임 범위 밖이다.** `kb_confluence_put_diagram`은 첨부파일만
   업로드/갱신한다. 페이지 본문에 해당 다이어그램을 보여주는 `drawio` 매크로가 아직 없다면,
   업로드에 성공해도 페이지에는 보이지 않는다 — 매크로가 이미 있는 diagram_name에
   업데이트를 걸 때(가장 흔한 경우)만 즉시 반영된다. 매크로가 없는 페이지에 새 다이어그램을
   "보이게" 추가해야 한다면, 업로드 완료 후 사용자에게 페이지에서 직접 draw.io 매크로를
   삽입해 해당 diagram_name을 선택하도록 안내한다.
7. **인증 정보는 API 서버(`apps/api`)에 설정되어 있다.** Claude나 MCP 쪽에서 Confluence
   계정/비밀번호를 직접 다루거나 요청 본문에 넣지 않는다 — 4개 도구 모두 page_id/space_key/
   diagram_name/xml_content만 인자로 받는다.

---

## 1. 사용 가능한 MCP 도구

| 도구 | 용도 | 백엔드 |
|------|------|--------|
| `kb_confluence_find_pages(space_key=, title="", limit=25)` | space 안에서 페이지 검색 (CQL) → page_id 확보 | `GET /v1/confluence/spaces/{space_key}/pages` |
| `kb_confluence_list_diagrams(page_id=)` | 페이지의 draw.io 다이어그램 목록 (본문 매크로 + 첨부파일 기준) | `GET /v1/confluence/pages/{id}/diagrams` |
| `kb_confluence_get_diagram(page_id=, diagram_name=)` | 다이어그램 원본 `.drawio` XML 조회 | `GET /v1/confluence/pages/{id}/diagrams/{name}` |
| `kb_confluence_put_diagram(page_id=, diagram_name=, xml_content=)` | XML 업로드/갱신 (기존 첨부 있으면 새 버전, 없으면 신규 생성) | `PUT /v1/confluence/pages/{id}/diagrams/{name}` |

각 도구는 REST 프록시일 뿐이며, `xml_content`는 항상 **원본 그대로의 mxGraph XML 문자열**이다
(이미지 변환·렌더링 없음 — Claude는 텍스트로만 읽고 쓴다).

`kb_confluence_list_diagrams`의 각 항목에는 다음 진단용 필드도 포함된다 — 매칭 실패나
렌더링 문제를 조사할 때 바로 이 값들부터 확인한다:
- `attachment_id`가 `None`이면 `candidate_attachment_titles`(페이지의 실제 첨부파일명 전체
  목록, 빈 배열이면 첨부파일이 아예 없다는 뜻)를 함께 반환한다.
- `media_type`은 Confluence에 실제 저장된 `metadata.mediaType` 값이다 (§4.5 참고).

---

## 2. 워크플로 — 읽기 (page_id를 모를 때)

```
1) kb_confluence_find_pages(space_key="LOOKIN", title="네트워크")
   → 여러 건이 나올 수 있다. title/web_url로 사용자가 말한 페이지가 맞는지 확인한다.
   → 결과가 1건으로 좁혀지지 않으면, 후보 목록을 사용자에게 보여주고 확인받는다
     (임의로 첫 번째 결과를 선택하지 않는다).

2) kb_confluence_list_diagrams(page_id="<확정된 page_id>")
   → 이 페이지에 어떤 diagram_name들이 있는지, 본문 매크로에 연결된 것(inline=true)인지
     첨부파일로만 존재하는 것(inline=false)인지 확인한다.

3) kb_confluence_get_diagram(page_id="...", diagram_name="<목록에서 확인한 이름>")
   → 원본 XML을 받는다. 이 시점에서 Claude는 XML을 "읽고 이해"만 하면 되는 요청(예:
     "이 다이어그램에 어떤 노드가 있어?")이면 여기서 끝난다.
```

이미 page_id를 알고 있다면 (사용자가 URL/ID를 직접 알려준 경우) 1단계는 건너뛴다.

---

## 3. 워크플로 — 쓰기 (신규 작성 / 기존 수정)

### 3-1. 기존 다이어그램 수정

```
1) kb_confluence_list_diagrams(page_id=...) 로 diagram_name이 실제 존재하는지 확인
2) kb_confluence_get_diagram(page_id=..., diagram_name=...) 로 현재 XML을 가져온다
3) XML을 파싱해 요청받은 변경만 적용한다 (§4의 mxCell 구조 참고).
   - 기존 mxCell id는 그대로 유지한다.
   - 새 도형/연결을 추가할 때는 기존에 쓰이지 않은 새 id를 부여한다
     (기존 XML에서 가장 큰 id 다음 숫자를 쓰는 것이 안전하다).
4) kb_confluence_put_diagram(page_id=..., diagram_name=..., xml_content="<수정된 전체 XML>")
   → 반환된 version 번호가 이전보다 1 증가했는지 확인해 업로드 성공을 검증한다.
```

### 3-2. 완전히 새로운 다이어그램 작성

```
1) kb_confluence_list_diagrams(page_id=...) 로 같은 이름이 이미 있는지 먼저 확인
   (있다면 3-1 절차를 따른다 — 실수로 다른 다이어그램을 덮어쓰지 않기 위함)
2) §4.2의 최소 골격을 기반으로 요청받은 도형/연결을 구성한 mxGraph XML을 작성한다
3) kb_confluence_put_diagram(page_id=..., diagram_name="<새 이름>", xml_content="...")
   → 신규 생성이므로 반환 version은 보통 1이다.
4) 페이지 본문에 이 diagram_name을 보여주는 매크로가 없다면(§0.6), 업로드는 성공했지만
   화면에는 아직 안 보인다는 것을 사용자에게 알린다.
```

---

## 4. mxGraph XML 구조 기초

**중요 (2026-08-04 정정):** `.drawio` 첨부파일의 최상위 요소는 `<mxGraphModel>`이 **아니라
`<mxfile>`이다.** `<mxGraphModel>`은 그 안의 `<diagram>` 요소가 감싸는 내부 콘텐츠일 뿐이다.
`<mxfile>` 래퍼 없이 `<mxGraphModel>`만 올리면 Confluence는 업로드 자체는 받아주지만(200
OK, 버전 증가) draw.io 뷰어가 구조를 인식하지 못해 **"cannot display diagram" 오류로
렌더링에 실패한다** — 실제로 이 문제로 테스트 페이지의 다이어그램이 깨진 사례가 있었다.

```
<mxfile host="..." modified="..." agent="..." version="...">
  <diagram id="..." name="Page-1">
    <mxGraphModel ...>
      <root> ... </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

### 4.1 핵심 요소

- `<mxfile>`: **최상위 루트.** `host`/`modified`/`agent`/`version` 속성을 가지며, 하나 이상의
  `<diagram>`을 자식으로 가진다.
- `<diagram id="..." name="...">`: 다이어그램 한 페이지. `id`는 유일해야 하며, **기존
  다이어그램을 수정할 때는 원본의 id를 그대로 유지한다** — 바꾸면 Confluence 매크로가
  참조를 잃는다. 내용은 §4.4의 압축 여부에 따라 두 가지 형태 중 하나다.
- `<mxGraphModel>`: `<diagram>` 내부의 실제 그래프 데이터. 캔버스 설정(격자, 페이지 크기 등)을
  속성으로 가진다.
- `<root>`: 셀(cell) 컨테이너. 관례상 `id="0"`(루트 레이어), `id="1"`(기본 레이어, parent="0")
  두 개가 항상 먼저 온다 — 이 둘은 지우거나 바꾸지 않는다.
- `<mxCell>` (vertex, 즉 도형/노드): `vertex="1"`, `parent`는 보통 `"1"`, `value`는 표시
  텍스트, `style`은 세미콜론으로 구분된 `키=값` 목록(도형 종류·색상·테두리 등), 자식으로
  `<mxGeometry x= y= width= height= as="geometry"/>`를 가진다.
- `<mxCell>` (edge, 즉 화살표/연결선): `edge="1"`, `source`/`target`에 연결할 두 vertex의
  `id`를 넣는다. 자식 `<mxGeometry relative="1" as="geometry"/>`는 보통 빈 채로 둔다.
- 모든 `mxCell id`는 **문서 전체에서 유일**해야 한다. 겹치면 draw.io가 셀을 잘못 렌더링하거나
  경고 없이 하나를 덮어쓴다.

### 4.2 최소 유효 골격 (신규 작성 시 기준)

```xml
<mxfile host="Confluence" modified="2026-08-04T00:00:00.000Z" agent="citec-kb-mcp" version="24.0.0">
  <diagram id="new-diagram-1" name="Page-1">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1"
                  connect="1" arrows="1" fold="1" page="1" pageScale="1"
                  pageWidth="850" pageHeight="1100" math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="2" value="Node A" style="rounded=0;whiteSpace=wrap;html=1;"
                vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="120" height="60" as="geometry" />
        </mxCell>
        <mxCell id="3" value="Node B" style="rounded=0;whiteSpace=wrap;html=1;"
                vertex="1" parent="1">
          <mxGeometry x="240" y="40" width="120" height="60" as="geometry" />
        </mxCell>
        <mxCell id="4" style="edgeStyle=orthogonalEdgeStyle;html=1;"
                edge="1" parent="1" source="2" target="3">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

이 골격에서 `value`(텍스트), `style`(모양), 좌표/크기, 연결 관계만 요청에 맞게 바꾸면
대부분의 신규 다이어그램 요청을 처리할 수 있다. **기존 다이어그램을 수정하는 경우, `<mxfile>`과
`<diagram id=...>`는 원본 값을 그대로 유지**하고 `<mxGraphModel>` 내부만 편집한다.

### 4.3 자주 쓰는 style 예시

| 목적 | style 값 |
|------|----------|
| 기본 사각형 | `rounded=0;whiteSpace=wrap;html=1;` |
| 둥근 사각형 | `rounded=1;whiteSpace=wrap;html=1;` |
| 원/타원 | `ellipse;whiteSpace=wrap;html=1;` |
| 마름모(결정) | `rhombus;whiteSpace=wrap;html=1;` |
| 직교(계단형) 화살표 | `edgeStyle=orthogonalEdgeStyle;html=1;` |
| 점선 화살표 | `dashed=1;html=1;` |

### 4.4 압축된(compressed) 다이어그램 — 현재 지원 범위 밖

draw.io는 `<diagram>` 내부 콘텐츠를 압축(base64 + raw deflate)해서 저장하는 옵션을 지원한다.
이 경우 `<diagram id="..." name="...">` 태그 바로 다음에 `<mxGraphModel>`이 보이지 않고,
대신 알아볼 수 없는 base64 문자열이 온다:

```xml
<diagram id="abc123" name="Page-1">
  7Vddc6M2FP01mflop9jhSl2xn4ejxNll2n1z9tqIQTYyMhKDN...(이하 알아볼 수 없는 문자열)
</diagram>
```

**`kb_confluence_get_diagram`으로 받은 텍스트가 이 형태(‘diagram 태그 다음이
`<mxGraphModel`로 시작하지 않고 임의의 문자/숫자 뭉치)라면, 그 다이어그램은 압축
저장되어 있는 것이다.** 현재 citec-kb는 이 압축을 해제/재압축하는 기능이 없다 —

- ❌ **압축된 내용을 읽지 못했다고 새 `<mxGraphModel>`을 지어내서 `kb_confluence_put_diagram`으로
  덮어쓰지 않는다.** 원본을 완전히 파괴한다 (id도, 실제 내용도 모두 소실).
- ✅ 대신 사용자에게 "이 다이어그램은 압축 저장 형식이라 현재 도구로는 읽거나 편집할 수
  없다"고 명확히 보고하고, 필요하면 압축 해제 지원을 별도로 요청하도록 안내한다.
- ✅ 이미 실수로 덮어썼다면, Confluence 페이지의 첨부파일 버전 기록(첨부파일 → 이력)에서
  이전 버전으로 복원하도록 안내한다 — citec-kb API는 최신 버전만 조회하므로 과거 버전
  복구는 Confluence UI에서 직접 해야 한다.

### 4.5 첨부파일 저장 규칙 — media type과 파일명 (citec-kb가 자동 처리)

**2026-08-04 실사고 해결 내역.** `kb_confluence_put_diagram`으로 업로드는 성공(200 OK,
버전 증가, macro revision 일치)했는데도 브라우저에서 **"Diagram attachment access error:
cannot display diagram"**이 뜨는 사고가 있었다. 근본 원인 두 가지, 둘 다 citec-kb 쪽에서
고쳐 지금은 자동으로 처리된다 — Claude가 직접 신경 쓸 필요는 없지만, **같은 증상이 다시
보이면 이 표부터 확인한다**:

| 원인 | 증상 | 조치 (citec-kb가 자동 수행) |
|------|------|------------------------------|
| Content-Type이 `application/xml`로 고정 전송됨 | `list_diagrams`의 `media_type`이 정상 다이어그램(`application/vnd.jgraph.mxfile`)과 다름 | 업로드 시 `application/vnd.jgraph.mxfile`로 전송 |
| 신규 첨부파일명에 `.drawio` 확장자를 강제로 붙임 | Confluence가 Content-Type을 무시하고 **파일 확장자로 media type을 재판단**해 위 문제가 재발함 | 신규 생성 시 확장자 없이 `diagram_name` 그대로 저장 (이 조직의 실제 첨부파일 명명 규칙과 동일) |

이 조직의 draw.io Confluence 앱은 다이어그램 원본을 확장자 없이 저장한다 — 예:
`MAZ 가용성 테스트 구성도` (확장자 없음), `MAZ 가용성 테스트 구성도.png`(자동 생성 미리보기),
`~MAZ 가용성 테스트 구성도.tmp`(자동 생성 락파일)가 함께 존재한다. `match_attachment_for_diagram`은
이 순서로 매칭한다: ① `<diagram_name>.drawio` 정확매칭 → ② 확장자 없는 정확매칭 → ③
`.drawio`/`.xml` 확장자 한정 stem fallback.

---

## 5. 오류 처리

| 상황 | 도구가 반환하는 메시지 | Claude의 대응 |
|------|----------------------|----------------|
| API 서버에 `CONFLUENCE_BASE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_PASSWORD` 미설정 | `오류: Confluence 연동이 설정되지 않았습니다 (...)` | 재시도하지 않는다. 사용자에게 API 서버 설정이 필요하다고 보고한다. |
| space에 해당 조건의 페이지 없음 | `공간 {space_key}에서 조건에 맞는 페이지를 찾을 수 없습니다.` | space_key 철자, title 필터를 다시 확인하거나 title 없이 재검색한다. |
| 페이지에 다이어그램 없음 | `페이지 {page_id}에서 draw.io 다이어그램을 찾을 수 없습니다.` | diagram_name을 짐작하지 말고, 사용자에게 정확한 이름을 확인하거나 첨부파일 목록 자체가 비어있음을 보고한다. |
| diagram_name이 실제로 없음 (get) | `오류: 다이어그램을 찾을 수 없습니다: <name>` | 오탈자 가능성 우선 의심 → `kb_confluence_list_diagrams`로 실제 존재하는 이름 재확인. |
| Confluence 인증 실패 (502) | 에러 문자열에 `confluence auth failed` 포함 | Claude가 해결할 수 없는 서버 설정 문제 — 재시도 금지, 사용자/운영자에게 보고. |
| Confluence 쪽 리소스 자체가 없음 (502, 404 매핑) | `confluence resource not found` | page_id 자체가 잘못됐을 가능성 — `kb_confluence_find_pages`로 재확인. |
| Confluence 연결 자체가 실패 (502) | 에러 문자열에 `confluence request failed` 포함 | DNS/TCP 연결 단계에서만 발생 — citec-kb가 자동으로 최대 2회 재시도한 뒤에도 실패한 것이므로, 일시적 블립이 아니라 지속적인 네트워크 문제일 가능성이 높다. 재시도하지 않고 보고한다. |
| 업로드는 성공(200)했는데 브라우저에서 "Diagram attachment access error: cannot display diagram" | API 오류 아님 — citec-kb 도구는 정상 응답을 반환함 | citec-kb API/MCP 오류가 아니라 뷰어 렌더링 문제다. `kb_confluence_list_diagrams`로 `media_type`이 `application/vnd.jgraph.mxfile`인지 확인한다(§4.5) — 2026-08-04에 이 원인으로 발생한 사고가 있었고 이미 수정되었으므로, 최신 code 번들이 배포됐는지부터 확인한다. |

---

## 6. 안티패턴

- ❌ space_key나 page_id를 이전 대화·기억에서 그대로 재사용 (공간명은 바뀔 수 있음 — §0.1)
- ❌ 기존 다이어그램을 `kb_confluence_get_diagram` 없이 바로 `kb_confluence_put_diagram`으로
  덮어쓰기 (기존 도형·연결 소실 위험 — §0.2)
- ❌ `kb_confluence_find_pages` 결과가 여러 건인데 첫 번째를 임의로 선택 (§2)
- ❌ 업로드 후 매크로가 자동으로 페이지에 나타난다고 가정 (§0.6 — 매크로가 이미 있는
  diagram_name을 갱신하는 경우만 즉시 반영됨)
- ❌ 기존 XML의 `mxCell id`를 임의로 바꾸거나 재사용해 다른 셀과 충돌시키는 것 (§0.3, §4.1)
- ❌ `<mxfile>` 래퍼 없이 `<mxGraphModel>`만 최상위로 업로드하는 것 — 업로드는 성공(200)하지만
  뷰어에서 "cannot display diagram"으로 렌더링 실패 (§0.4, §4.2)
- ❌ `kb_confluence_get_diagram` 결과가 압축된 base64 형식(§4.4)인데 이를 무시하고 새
  `<mxGraphModel>`을 지어내서 덮어쓰는 것 — 원본이 영구 소실된다
- ❌ 502/503 오류를 재시도로 해결하려 하는 것 (서버 설정 문제이므로 Claude가 고칠 수 없음)
- ❌ 사용자가 확인하지 않은 diagram_name/page_id를 추측해서 쓰기 작업을 실행하는 것
- ❌ `list_diagrams` 결과에 `attachment_id=None` + `candidate_attachment_titles`가 있는데
  이를 무시하고 diagram_name으로 바로 get/put을 시도하는 것 — 먼저 실제 첨부파일명을
  사용자에게 확인한다

---

## 7. 최소 체크리스트

- [ ] page_id를 모르면 `kb_confluence_find_pages`로 먼저 확정했는가 (space_key 하드코딩 아님)
- [ ] 검색 결과가 여러 건이면 사용자 확인을 받았는가
- [ ] 쓰기 작업 전에 `kb_confluence_get_diagram`으로 기존 내용을 확인했는가 (신규 생성이
      아닌 경우)
- [ ] 편집 시 기존 `<mxfile>`/`<diagram id>`와 `mxCell id`를 보존했는가, 새 id는 충돌
      없이 부여했는가
- [ ] 받은 XML이 `<mxGraphModel`로 시작하지 않는 base64 압축 형식인지 확인했는가 —
      맞다면 편집을 시도하지 않고 사용자에게 보고했는가
- [ ] `kb_confluence_put_diagram` 응답의 `version`이 예상대로 증가했는지 확인했는가
- [ ] 매크로가 없는 페이지에 신규 업로드한 경우, 사용자에게 매크로 삽입이 별도로
      필요하다고 안내했는가
- [ ] 503/502 오류를 재시도가 아니라 보고로 처리했는가

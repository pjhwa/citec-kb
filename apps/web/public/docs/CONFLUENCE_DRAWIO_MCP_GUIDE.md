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
4. **새 다이어그램을 처음부터 만들 때도 유효한 `mxGraphModel` 최소 골격(§4.2)을 지킨다.**
   빈 문자열이나 불완전한 XML을 올리면 Confluence 쪽 draw.io 뷰어가 열리지 않는다.
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

draw.io(diagrams.net)가 사용하는 파일 형식은 `mxGraphModel`이다. Confluence에 저장되는
`.drawio` 첨부파일도 동일한 형식이다.

### 4.1 핵심 요소

- `<mxGraphModel>`: 최상위 루트. 캔버스 설정(격자, 페이지 크기 등)을 속성으로 가진다.
- `<root>`: 셀(cell) 컨테이너. 관례상 `id="0"`(루트 레이어), `id="1"`(기본 레이어, parent="0")
  두 개가 항상 먼저 온다 — 이 둘은 지우거나 바꾸지 않는다.
- `<mxCell>` (vertex, 즉 도형/노드): `vertex="1"`, `parent`는 보통 `"1"`, `value`는 표시
  텍스트, `style`은 세미콜론으로 구분된 `키=값` 목록(도형 종류·색상·테두리 등), 자식으로
  `<mxGeometry x= y= width= height= as="geometry"/>`를 가진다.
- `<mxCell>` (edge, 즉 화살표/연결선): `edge="1"`, `source`/`target`에 연결할 두 vertex의
  `id`를 넣는다. 자식 `<mxGeometry relative="1" as="geometry"/>`는 보통 빈 채로 둔다.
- 모든 `id`는 **문서 전체에서 유일**해야 한다. 겹치면 draw.io가 셀을 잘못 렌더링하거나
  경고 없이 하나를 덮어쓴다.

### 4.2 최소 유효 골격 (신규 작성 시 기준)

```xml
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
```

이 골격에서 `value`(텍스트), `style`(모양), 좌표/크기, 연결 관계만 요청에 맞게 바꾸면
대부분의 신규 다이어그램 요청을 처리할 수 있다.

### 4.3 자주 쓰는 style 예시

| 목적 | style 값 |
|------|----------|
| 기본 사각형 | `rounded=0;whiteSpace=wrap;html=1;` |
| 둥근 사각형 | `rounded=1;whiteSpace=wrap;html=1;` |
| 원/타원 | `ellipse;whiteSpace=wrap;html=1;` |
| 마름모(결정) | `rhombus;whiteSpace=wrap;html=1;` |
| 직교(계단형) 화살표 | `edgeStyle=orthogonalEdgeStyle;html=1;` |
| 점선 화살표 | `dashed=1;html=1;` |

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

---

## 6. 안티패턴

- ❌ space_key나 page_id를 이전 대화·기억에서 그대로 재사용 (공간명은 바뀔 수 있음 — §0.1)
- ❌ 기존 다이어그램을 `kb_confluence_get_diagram` 없이 바로 `kb_confluence_put_diagram`으로
  덮어쓰기 (기존 도형·연결 소실 위험 — §0.2)
- ❌ `kb_confluence_find_pages` 결과가 여러 건인데 첫 번째를 임의로 선택 (§2)
- ❌ 업로드 후 매크로가 자동으로 페이지에 나타난다고 가정 (§0.6 — 매크로가 이미 있는
  diagram_name을 갱신하는 경우만 즉시 반영됨)
- ❌ 기존 XML의 `mxCell id`를 임의로 바꾸거나 재사용해 다른 셀과 충돌시키는 것 (§0.3, §4.1)
- ❌ 502/503 오류를 재시도로 해결하려 하는 것 (서버 설정 문제이므로 Claude가 고칠 수 없음)
- ❌ 사용자가 확인하지 않은 diagram_name/page_id를 추측해서 쓰기 작업을 실행하는 것

---

## 7. 최소 체크리스트

- [ ] page_id를 모르면 `kb_confluence_find_pages`로 먼저 확정했는가 (space_key 하드코딩 아님)
- [ ] 검색 결과가 여러 건이면 사용자 확인을 받았는가
- [ ] 쓰기 작업 전에 `kb_confluence_get_diagram`으로 기존 내용을 확인했는가 (신규 생성이
      아닌 경우)
- [ ] 편집 시 기존 `mxCell id`를 보존했는가, 새 id는 충돌 없이 부여했는가
- [ ] `kb_confluence_put_diagram` 응답의 `version`이 예상대로 증가했는지 확인했는가
- [ ] 매크로가 없는 페이지에 신규 업로드한 경우, 사용자에게 매크로 삽입이 별도로
      필요하다고 안내했는가
- [ ] 503/502 오류를 재시도가 아니라 보고로 처리했는가

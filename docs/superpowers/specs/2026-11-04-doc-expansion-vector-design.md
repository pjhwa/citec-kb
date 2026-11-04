# 문서 확장 임베딩(벡터 경로) 설계

- 작성일: 2026-11-04
- 상태: 승인됨(사용자 구두 승인)
- 관련: `docs/superpowers/specs/2026-11-03-doc-expansion-design.md`, `docs/DOC_EXPANSION_RUNBOOK_20261103.md` §9

## 1. 배경과 목표

문서 확장(요약·키워드)을 렉시컬 FTS 경로에 넣었을 때(`grounded`/`all`, AND/OR 매칭) v2_pointer/v2_vocab recall@10이 오르지 않았다. 질의가 `plainto_tsquery("simple")`로 AND 매칭되고 형태소 분석이 없어 단어 하나가 어긋나면 끊기기 때문이다. 의미가 가까우면 단어가 달라도 매칭되는 벡터 경로에 확장문을 넣어 같은 평가셋으로 효과를 본다.

## 2. 결정 사항

| 항목 | 결정 |
|---|---|
| 저장 | 새 테이블 `doc_expansion_embedding`(document_id PK/FK cascade, model, vector, source_content_hash, created_at) + HNSW 코사인 인덱스. `embeddings`·청크 임베딩은 불변 |
| 입력 | `passage: ` + summary + 키워드(공백 연결), 현재 임베딩 모델 `embed_passages` 그대로. 문서당 벡터 1개 |
| 생성 | `docexp embed` CLI(재개 가능, 기존 행 건너뜀, `--force`, dry-run 기본, `--commit`) |
| 검색 | `SEARCH_DOC_EXPANSION_VEC=off|on`(기본 off), `SEARCH_DOC_EXPANSION_VEC_WEIGHT`(기본 0.5). 질의 벡터로 확장 벡터 top-N을 구하고 문서의 대표 청크(최소 ordinal 활성 청크)를 후보에 합친다. 해시가 다른 행은 무시 |
| 노출 | 확장문은 랭킹 신호일 뿐 응답에 나가지 않는다 |
| 범위 | 개발 서버 한정. 운영 적용은 측정 후 별도 결정 |

## 3. 평가와 통과 기준 (측정 전 고정)

- 평가셋 `artifacts/citec-eval-v2/evalset-v2-all.json`(46건), 같은 파일로 `off`/`on` 비교.
- 성공: v2_pointer 또는 v2_vocab에서 recall@10이 +0.10 이상 오르거나 정답이 새로 top-10에 들어오는 케이스가 있고, v2_real recall@10 악화 없음, v2_real MRR 하락이 구간 안, p50 지연 악화 없음.
- 그렇지 않으면 실패로 기록하고 플래그를 off로 둔다. 코드를 유지할지는 결과에 따라 정한다.

## 4. 비범위

청크 임베딩 재생성, 질의 재작성, 운영 쓰기, 확장 대상 규칙 변경.

## 5. 위험

- 의미가 비슷한 일반 요약이 많아 일반 문서가 올라올 수 있다 → `v2_real` 회귀로 확인.
- 개발 서버의 확장 데이터는 15천 건뿐이다(운영과 5천 건 차이).
- HNSW 필터: 기존 `vector_search`와 같이 `hnsw.iterative_scan`을 쓴다.

## 6. 테스트(네트워크 없음)

- `embed` 입력 문자열 구성, 재개/`--force`, 해시 불일치 행 무시
- 검색: 플래그 off 동일성, on일 때 확장 벡터로 대표 청크가 후보에 들어옴, 필터 적용, 오래된 행 무시(스크래치 DB, 가짜 임베딩)

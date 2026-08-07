# `fb_domain` 어휘 목록 — 실패 버킷 도메인 파셋

> `failure_bucket`(§`docs/superpowers/specs/2026-08-06-failure-bucket-multi-plugin-design.md`)의
> `fb_domain` 컬럼이 쓰는 값 목록이다. 서비스일류화팀의 각 진단 플러그인(Claude Code 스킬)은 등록/조회
> 시 이 표에 있는 값만 쓴다. **DB에 hard enum 제약은 없다** — 오타/난립을 막기 위한 소프트
> 컨벤션이며, 이 표가 유일한 정본(source of truth)이다.
>
> `fb_domain`은 코퍼스 전역 `Document.domain`(`kb_search(area=)` 필터가 쓰는 7개 고정 어휘:
> `os`/`dbms`/`storage`/`network`/`virtualization`/`middleware`/`cloud`)과 **다른 축**이다.
> `apps/api/app/taxonomy.py`의 `_FB_DOMAIN_TO_CORPUS_DOMAIN` 매핑 테이블이 아래 값을 그 7개
> 어휘 중 하나로 변환한다 — 새 행을 추가할 때 그 매핑 테이블도 함께 갱신해야 한다(아래 "새 도메인
> 추가 절차" 참고).

## 현재 등록된 값

| fb_domain | 설명 | 소유 플러그인 | 코퍼스 domain 매핑 | 대표 신호 예시 | 추가일 |
|---|---|---|---|---|---|
| `network` | 네트워크 패킷/전송계층(TCP/TLS/HTTP) 장애. 하위 분류는 기존 `protocol` 컬럼(TCP/TLS/HTTP)으로 별도 표현 | packet-capture-rca | `network` | "RST 직전 idle ≥ 60초", "TLS record 경계 불일치" | 2026-07-29 |
| `cluster` | Pacemaker/Corosync 리눅스 HA 클러스터 — 펜싱/쿼럼 손실/split-brain/DRBD/iSCSI 공유스토리지 | pacemaker-tools | `os` | "token timeout 이내 펜싱 발생", "qdevice 응답 없음 후 쿼럼 손실" | (신설 시 기입) |
| `windows` | Windows 이벤트로그, WSFC 페일오버 클러스터, AD DS 복제, SQL Always On AG | windows-tools | `os` | "Event ID 1135 발생 후 5초 이내 1177 발생", "repadmin 오류 8606 연속 3회" | (신설 시 기입) |

> `cluster`/`windows`가 둘 다 코퍼스 `domain="os"`로 매핑되는 것은 의도된 설계다 — Pacemaker/WSFC/AD
> 모두 OS/커널 계층 HA 기능이라 `kb_search(area="os")`로 전역 검색될 때 함께 잡히길 원한다. `fb_domain`
> 자체는 서로 다른 값으로 유지해 `kb_list_failure_buckets(fb_domain=)`/`kb_match_failure_bucket`에서는
> 계속 분리해서 필터링한다.

## 새 도메인 추가 절차

1. 정말 새 `fb_domain`이 필요한지 먼저 확인한다 — 기존 값 안에서 신호(`discriminating_signals`)로
   충분히 구분되는 경우라면 새 값을 만들지 않는다(예: "AD 복제 실패"는 `windows` 안에서 신호로 구분,
   별도 도메인 불필요).
2. 이 표에 행을 추가하는 PR을 낸다: `fb_domain`, 설명, 소유 플러그인, 코퍼스 domain 매핑 후보(7개
   고정 어휘 중 가장 가까운 것 — 애매하면 citec-kb 개발팀과 상의), 대표 신호 예시, 추가일.
3. 같은 PR에서 `apps/api/app/taxonomy.py`의 `_FB_DOMAIN_TO_CORPUS_DOMAIN` 딕셔너리에 매핑을
   추가한다.
4. 매핑 표에 없는 값으로 먼저 등록해버린 경우 citec-kb가 등록 응답에 경고를 붙인다(설계 문서 §5) —
   경고를 보면 오타인지 신설인지 확인 후 이 표를 갱신한다.

## 참고

- 값 형식: 소문자, 언더스코어 구분 없이 단일 단어 권장(`network`, `cluster`, `windows`). 복합 개념이
  필요하면 하이픈보다 신호 쪽에서 구분하는 것을 먼저 검토한다(위 1번).
- **`environment`(csp/msp/onprem/hybrid)는 `fb_domain`과 다른 축이다** — `fb_domain`이 "어느
  진단 영역인가"라면 `environment`는 "그 패턴이 어느 배포 환경에서 성립하는가"다. 이 표에 새 값을
  추가하는 절차와 무관하며, 모든 `fb_domain`이 공통으로 쓰는 선택 필드다(`docs/FAILURE_BUCKET_PLUGIN_GUIDE.md`
  §4-1 참고). `environment`에는 이 표와 같은 별도 어휘 표가 없다 — `references/corpus-taxonomy.md`가
  이미 코퍼스 전역에서 쓰는 4개 값을 그대로 재사용한다.
- 이 표는 `docs/FAILURE_BUCKET_PLUGIN_GUIDE.md`(플러그인 개발자용 지침)와
  `docs/superpowers/specs/2026-08-06-failure-bucket-multi-plugin-design.md`(citec-kb 설계 지침)
  양쪽에서 동일하게 참조한다 — 세 문서가 서로 다른 도메인 목록을 갖지 않도록 이 파일만 갱신한다.

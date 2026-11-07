# 검증 기록 — 2026-11-07

검증 대상은 업로드된 ax26-label-workspace-main 원본을 수정한 Python/HTML/JavaScript 프로젝트입니다. 실제 서버에 올라간 코드나 모델 응답의 검증 결과와 구분합니다.

## 로컬 자동 검사

- Python 3.12.14: `python3 -m unittest discover -s tests -p 'test_*.py' -v` — **26개 통과**.
- Node 24.22.1: `node --test tests/frontend.test.cjs` — **12개 통과**.
- `python3 -m py_compile server.py session_store.py agentstream_client.py scripts/live_smoke.py` 통과.
- `node --check app.js`, `node --check stream.js` 통과.

주요 범위: 정확한 API 주소·본문·키 헤더, 세션 UUID, health/첫 질문/후속 질문, JSON/NDJSON 콘텐츠 유형, 분할 UTF-8·JSON, text 블록 필터·중복 이벤트, 제공된 근거 보존, 잘못된 JSON·완료 전 종료·실패·취소·타임아웃, 오류 상태·재시도 정보, 리다이렉트 차단, 정적 파일 허용 목록, CSRF·Origin·방문자별 세션 격리, 동시 대화 잠금·늦은 이전 요청 종료의 경합 방지, 요청 재전송 차단, 표 입력 제한. 실제 로컬 HTTP 서버를 통해 첫/후속 질문과 취소 요청을 검사했습니다.

`live_smoke.py` 실행 경로도 주입된 **대역 서비스**로 검사했습니다. 실제 AgentStream 호출 결과가 아닙니다.

## 브라우저 검사

기존 AX26 화면을 Python 서버와 대역 업스트림으로 열어 다음을 직접 확인했습니다.

- 문안제작/문안검토/보관함 및 기존 레이아웃 유지.
- 문안검토 요청 → 답변 수신 완료 → 같은 결과 후속 질문 → 기존 답변과 추가 답변 보존.
- 응답에 제공된 근거의 펼침 영역.
- API 키 입력란 제거, 상태 확인 화면에서 status/message/checked_at/embedding_models 표시.

대역 답변에는 “연동 검증용 대역 답변입니다”가 명시되어 있었으며 이 대역 서버는 배포 ZIP에 포함하지 않았습니다. 브라우저 시험에서 발견한 HTTP 환경의 crypto.randomUUID 미지원 문제는 암호학적 getRandomValues 대체 구현으로 수정하고 회귀 테스트를 추가했습니다.

## 이번에 수행하지 않은 검증

- 실제 API 키를 사용하는 AgentStream 모델 호출 및 RAG 문서 검색.
- 공개 해커톤 서버에 수정 ZIP 배포, 해당 서버의 DNS/TLS·프록시 버퍼링·전체 응답 완료.
- 실제 제품 이미지의 OCR 정확도와 법령 적용 판단 정확도.

API 키를 이 작업 환경에 주입하지 않았고 사용자 서버에 SSH 접속하지 않았습니다. 배포 후 README의 첫 질문·후속 질문·취소·브라우저별 검증 또는 `scripts/live_smoke.py`를 운영자가 실행해야 합니다. health 성공은 실제 답변 완료를 대신하지 않습니다.

## 유지한 범위와 제한

이미지 OCR 및 텍스트 검토 경로는 기존 구조를 유지합니다. 자동으로 AI 판단과 이미지 좌표를 연결하거나 취소선을 생성하는 기능은 추가하지 않았으며, 영역별 수정 표시와 근거 입력은 직접 등록 방식입니다. 원본의 회사정보·보관함 localStorage 키를 유지했습니다. 이 앱은 동일 상태 저장소에 서버 프로세스 하나를 실행하는 구조입니다. 실패나 취소 후에는 새 대화로 시작합니다.

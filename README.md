# AX26 표시 문안 제작·검토 — 기존 화면 AgentStream 연동

업로드된 원본의 Python 서버와 HTML/CSS/JavaScript 화면을 유지한 수정본입니다. 제품정보·엑셀 붙여넣기·회사정보·브라우저 OCR·문안 제작/검토·보관함을 사용합니다. 별도의 Node 앱으로 갈아탈 필요가 없습니다.

## 이번 변경

- API 키를 **서버 환경변수 `AGENTSTREAM_API_KEY`에서만** 읽습니다. 브라우저의 키 입력·저장·해제 기능은 제거했습니다.
- 방문자 쿠키와 CSRF 검증, 방문자별 자체 대화 ID, 고정 워크스페이스·챗봇 매핑을 사용합니다. 다른 방문자의 대화는 조회/재사용할 수 없습니다.
- 최초 요청은 health → sessions 생성 → messages 전송 순서입니다. 후속 질문은 같은 세션을 순서대로 사용합니다. 실패한 POST는 자동 재실행하지 않습니다.
- 한글과 JSON 줄이 나뉘어 도착하는 NDJSON, 중복 이벤트, 부분 답변, 완료 전 종료, 오류, 취소, 제한 시간을 처리합니다. `turn.completed`를 받은 경우만 완료입니다.
- 실제 assistant 답변과 제공된 출처 자료를 표시합니다. 서버 키 설정 여부와 챗봇 health, 실제 답변 완료는 서로 다른 상태입니다.

**이미지 검토 범위:** Tesseract가 브라우저에서 한국어·영어 텍스트를 추출하고, 사용자가 확인/수정한 문구를 챗봇에 전달합니다. 이미지 파일 자체는 AgentStream API에 보내지 않습니다. 이미지에 영역을 그리고 수정 문구·근거를 붙이는 기능은 원본과 같은 **직접 등록 방식**입니다. AI 답변을 이미지 좌표와 자동 연결하는 기능은 아직 포함되지 않습니다.

## 서버에서 바로 실행

Python **3.10 이상**이 필요합니다. 서버는 표준 라이브러리만 사용하므로 `pip install`이나 Node 설치가 필요하지 않습니다. 기존 암호화 키 파일을 읽거나 API 키를 파일에 새로 저장하지 않습니다.

키가 현재 Ubuntu 셸에 없다면, 화면에 표시되지 않게 입력합니다.

```bash
if [ -z "${AGENTSTREAM_API_KEY:-}" ]; then
  read -rsp 'AgentStream API 키: ' AGENTSTREAM_API_KEY
  printf '\n'
fi
export AGENTSTREAM_API_KEY
```

기존 공개 URL로 실행하는 설정입니다.

```bash
export APP_HOST='0.0.0.0'
export PORT='8501'
export APP_PUBLIC_URL='https://devopen.jntp-ax.or.kr/i/Tfo3vbS3FbL3oe9y/ax26/'
export APP_BASE_PATH='/i/Tfo3vbS3FbL3oe9y/ax26'
python3 server.py
```

실행 후 다음 주소를 새로고침합니다.

https://devopen.jntp-ax.or.kr/i/Tfo3vbS3FbL3oe9y/ax26/

프록시가 공개 경로를 그대로 전달하거나 `/ax26/` 또는 `/`로 바꾸어 전달하는 경우를 지원합니다. 원본 배포 설명의 `/ax26/` 전달 방식도 유지합니다. 플랫폼의 사이트 공개 여부와 AgentStream API 키 인증은 별개입니다. 사이트 로그인 HTML이 오면 해당 사이트의 접근 설정을 확인하세요.

이 실행은 포그라운드 방식입니다. 실행 중인 SSH 창을 닫거나 `Ctrl+C`를 누르면 서버가 종료됩니다.

## 현재 실행 중인 Node 앱에서 전환하기

기존 `agentstream-integration` 폴더를 덮어쓰지 않고 새 폴더로 배포합니다. 같은 서버의 다른 팀원 프로세스는 종료하지 마세요.

### 1. Windows PowerShell에서 ZIP 전송

수정 ZIP을 다운로드 폴더에 `ax26-label-workspace-main.zip` 이름으로 저장합니다. 브라우저가 `(1)` 등을 붙였다면 실제 파일명에 맞춰 명령을 수정하세요. 이 서버는 SFTP 방식이 실패했으므로 `scp -O`를 사용합니다.

```powershell
& "$env:WINDIR\System32\OpenSSH\scp.exe" -O "$env:USERPROFILE\Downloads\ax26-label-workspace-main.zip" "ff9ebb72-6ae84c10.hackathon:/home/ubuntu/hackathon/kyunghun/"
```

### 2. Ubuntu SSH에서 새 폴더에 풀기

기존 Node 앱 실행 창은 이 단계까지 그대로 두어도 됩니다. 별도 SSH 창에서 압축을 풀 수 있습니다.

```bash
release_dir="$HOME/hackathon/kyunghun/ax26-source-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$release_dir"
python3 -m zipfile -e "$HOME/hackathon/kyunghun/ax26-label-workspace-main.zip" "$release_dir"
cd "$release_dir/ax26-label-workspace-main"
pwd
python3 --version
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

`pwd`가 출력한 새 폴더 경로를 보관합니다. 테스트는 대역 응답을 사용하며 실제 API 키나 모델 호출이 필요 없습니다.

### 3. 기존 앱 실행 창에서 전환

`npm start`가 실행 중인 **그 창에서만** `Ctrl+C`를 누릅니다. 그 셸에서 위 `pwd`로 확인한 새 폴더로 이동합니다. 예시 경로의 날짜를 그대로 복사하지 말고 실제 출력 경로를 사용하세요.

```bash
# 새 압축 해제 폴더의 ax26-label-workspace-main 안으로 cd한 뒤 실행합니다.
if [ -z "${AGENTSTREAM_API_KEY:-}" ]; then
  read -rsp 'AgentStream API 키: ' AGENTSTREAM_API_KEY
  printf '\n'
fi
export AGENTSTREAM_API_KEY
export APP_HOST='0.0.0.0'
export PORT='8501'
export APP_PUBLIC_URL='https://devopen.jntp-ax.or.kr/i/Tfo3vbS3FbL3oe9y/ax26/'
export APP_BASE_PATH='/i/Tfo3vbS3FbL3oe9y/ax26'
python3 server.py
```

주소 사용 중 오류가 나면 기존 앱이 아직 실행 중인지 확인하세요. 포트 기준으로 다른 프로세스를 강제 종료하지 마세요.

### 4. 브라우저에서 실제 검증

1. 공개 URL을 `Ctrl+F5`로 새로고침합니다. 기존 문안제작·문안검토·보관함 화면이 보여야 합니다.
2. 설정 → API 연결에서 서버 키 설정과 health의 `status`, `message`, `checked_at`, `embedding_models`를 확인합니다. healthy만으로 실제 문서 검색 성공을 단정하지 않습니다.
3. 문안검토에 짧은 검토 대상 문구와 제품 정보를 넣고 요청합니다. 부분 텍스트가 보이는 데서 끝나지 않고 **답변 수신 완료**까지 확인합니다.
4. 후속 질문으로 “방금 답변의 근거와 확인하지 못한 내용을 구분해 주세요”라고 묻습니다. 같은 대화가 이어지는지 확인합니다.
5. 라벨 이미지를 첨부하여 OCR 원문을 확인하고 오인식 문자를 수정한 다음 검토합니다. 클릭 가능한 출처는 실제 제공된 자료만 표시해야 합니다.
6. 생성 중 중지 버튼을 눌러 일부 답변과 중지 상태가 남는지 확인합니다. 새 대화로 다시 시작합니다.
7. 시크릿 창에서 별도로 접속하여 다른 방문자의 대화가 공유되지 않는지 확인합니다.

실제 서버를 통한 첫 질문·후속 질문 전체 완료를 명령으로 검사할 수도 있습니다. **실제 모델 사용량이 발생하고 메시지가 AgentStream에 저장될 수 있습니다.** 이 스크립트는 API 키를 받지 않고 자체 서버를 경유하며, 답변 원문 대신 상태와 글자 수만 출력합니다.

```bash
python3 scripts/live_smoke.py 'https://devopen.jntp-ax.or.kr/i/Tfo3vbS3FbL3oe9y/ax26/'
```

사이트 로그인으로 보호된 URL은 이 명령에서 로그인 HTML/리다이렉트로 실패할 수 있습니다. 해당 경우 로그인한 브라우저에서 위 절차로 검증하세요. 법령 근거의 정확성·최신성·제품 적용 여부는 별도로 확인해야 합니다.

### 이전 앱으로 되돌리기

새 Python 실행 창에서 `Ctrl+C`를 누른 다음, 같은 셸에서 실행합니다. 기존 키와 공개 URL 환경변수가 유지되어 있어야 합니다.

```bash
cd "$HOME/hackathon/kyunghun/agentstream-integration"
npm start
```

## 로컬 개발과 설정

로컬 기본 주소는 `http://localhost:8501/`입니다. 공개 배포 변수들이 설정된 셸에서 로컬 시험을 한다면 원하는 값으로 명시적으로 바꾸세요.

```bash
export APP_HOST='127.0.0.1'
export PORT='8501'
export APP_PUBLIC_URL='http://localhost:8501/'
export APP_BASE_PATH=''
python3 server.py
```

| 환경변수 | 용도 |
| --- | --- |
| `AGENTSTREAM_API_KEY` | 서버 전용 API 키. 없으면 모델 요청 차단 |
| `AGENTSTREAM_BASE_URL` | 빈 값이면 고정 주소 `https://api-gateway.jntp-ax.or.kr/agentstream`; 다른 값은 기동 거부 |
| `AGENTSTREAM_CHATBOT_ID` | 빈 값이면 고정 챗봇 `5a09d748-53e8-4260-9d24-5a64e5e52960`; 다른 값은 기동 거부 |
| `APP_HOST`, `PORT` | 기본 `127.0.0.1`, `8501` |
| `APP_PUBLIC_URL` | 브라우저에서 사용하는 전체 URL. Origin 및 Secure 쿠키 판단 |
| `APP_BASE_PATH` | 공개 경로 접두사. 로컬 기본 빈 값 |
| `APP_STATE_DIR` | 방문자·대화 매핑 DB 위치. 기본 `~/.ax26-private` |
| `APP_REQUEST_TIMEOUT` | 요청 전체 제한 시간(초). 기본 180 |

`.env.example`은 빈 값으로 된 설정 목록입니다. 서버는 `.env` 파일을 자동 로드하지 않습니다. 실행 셸이나 운영 환경의 비밀 저장소에서 환경변수를 주입하세요. 실제 키를 명령 기록·README·프런트엔드·Git·대화에 적지 마세요.

워크스페이스는 `a35a47e3-876f-43c2-ac7f-7a96bdff83e2`로 고정합니다. workspace UUID, chatbot UUID, 대화 session UUID는 서로 다른 값입니다. 저장된 시스템 프롬프트는 AgentStream이 적용하며 앱은 이를 복사하지 않습니다. RAG만 연결된 것으로 취급하고 SQL/Plotly 호출이나 차트 성공을 가정하지 않습니다.

## 데이터와 동작 범위

- 회사정보와 보관함은 원본과 같은 localStorage 키를 유지합니다. 같은 공개 주소·브라우저에서는 기존 데이터가 유지됩니다. 다른 브라우저로 자동 동기화되지 않습니다.
- 원본 이미지는 브라우저에서 OCR 처리합니다. OCR 실행에는 외부 CDN의 Tesseract 코드와 언어 모델 다운로드가 필요합니다. 다운로드 실패 시 직접 텍스트를 입력할 수 있습니다.
- 서버 DB에는 방문자 인증과 대화 연결 메타데이터가 저장됩니다. 새 구현은 원문·답변·API 키를 DB에 저장하지 않습니다. 기존 배포에서 남은 암호화 키 파일이나 구형 DB는 자동 삭제하지 않습니다.
- 검토·제작 요청에는 입력 제품정보, 회사정보, OCR/수정 텍스트 등이 포함됩니다. 이 메시지와 답변은 AgentStream의 이력 정책에 따라 저장될 수 있습니다.
- 배합표는 기존 8열 범위만 지원합니다. 추가 열이나 크기 제한 초과는 잘라내지 않고 오류로 알립니다. 엑셀 파일 자체 업로드 기능은 없습니다.
- 입력정보 기반 정적 문안, 샘플, 실제 모델 답변은 구분합니다. 문서 근거가 없는 항목은 적합 판정으로 대체하지 않습니다.
- 브라우저는 150초 후 서버 취소를 요청하며 서버의 기본 전체 제한 시간은 180초입니다. DNS·TLS 연결 설정 중에는 연결 시도가 반환된 뒤 취소가 확인될 수 있습니다. 취소 요청이 서버에 도달하지 않아도 자동 재전송하지 않습니다.
- 상태 확인 실패를 포함하여 완료하지 못한 요청은 보수적으로 새 대화가 필요하도록 처리합니다. 이전 답변은 화면에 남습니다.
- 서버는 단일 프로세스 실행을 전제로 합니다. 동일 저장소에 여러 서버 프로세스를 겹쳐 실행하지 마세요.

## 테스트와 파일

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/frontend.test.cjs
```

Python 테스트는 서버·업스트림 계약·세션 격리를 검사합니다. Node는 브라우저 스트림 파서 테스트에만 필요하며 서비스 실행에는 필요하지 않습니다. 실제 API 및 배포 검증 여부는 `docs/verification.md`를 참고하세요.

| 파일 | 역할 |
| --- | --- |
| `server.py` | 정적 파일, 방문자/CSRF, 자체 API, 실행 설정 |
| `agentstream_client.py` | 외부 API 호출, NDJSON, 오류/취소 처리 |
| `session_store.py` | 방문자별 자체 대화와 AgentStream 세션 매핑 |
| `app.js`, `stream.js` | 기존 화면 동작, OCR, 후속 질문, 스트림 처리 |
| `index.html`, `style.css` | 기존 AX26 화면 |
| `scripts/live_smoke.py` | 운영자가 실행하는 실제 첫 질문·후속 질문 검사 |
| `tests/` | 로컬 대역 및 계약 검증 |

# AX26 표시 문안 제작·검토

건강기능식품 제품정보와 원료 배합표를 입력하여 AgentStream 지식베이스 기반의 문안 제작·검토를 수행하는 웹 애플리케이션입니다.

## 기능

- 제품정보 입력 및 Excel 배합표 붙여넣기
- 지식베이스 기반 문안 제작·검토, 응답 스트리밍
- 라벨 이미지 붙여넣기·첨부 및 브라우저 내 한국어/영어 OCR
- 원본·수정본 보관함, 자동 저장, TXT 내보내기
- 조절 가능한 좌우 패널과 생성 진행 표시
- 암호화된 서버 공용 API 연결

진행률은 예상값이며 실제 완료 이벤트를 받은 후에만 100%로 표시합니다. 생성 결과는 인용된 문서와 제품별 적용 조건을 확인해야 합니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `index.html` | 화면 구성 |
| `style.css` | 반응형 스타일 |
| `app.js` | 화면 동작, OCR, 저장 및 API 호출 |
| `server.py` | 정적 파일 제공 및 AgentStream 중계 |
| `session_store.py` | API 키 암호화와 세션 관리 |

## 현재 배포 구조

Python 3과 `cryptography` 패키지가 필요합니다. OCR은 브라우저에서 CDN의 Tesseract.js를 사용합니다.

```sh
python3 -m pip install -r requirements.txt
mkdir -p /home/ubuntu/ax26-web/ax26
cp index.html style.css app.js /home/ubuntu/ax26-web/ax26/
cp server.py /home/ubuntu/ax26-server.py
cp session_store.py /home/ubuntu/session_store.py
python3 /home/ubuntu/ax26-server.py
```

서버 포트는 8501입니다. 이 저장소는 운영 중인 배포의 소스 스냅샷으로, 새로운 서버에서 사용하려면 `server.py`의 ROOT, ENDPOINT, 허용 Origin 및 프록시 경로, `session_store.py`의 저장 경로, `index.html`의 챗봇 안내 정보를 해당 환경에 맞게 변경해야 합니다. 기존 프록시는 `/i/.../ax26/` 경로를 서버의 `/ax26/` 경로로 전달합니다.

## 키와 저장 데이터

**API 키, SSH 개인키, 암호화 키, 세션 DB 및 사용자 데이터는 포함하지 않습니다.**

운영 서버의 키는 웹 루트 밖 `/home/ubuntu/.ax26-private`에서 Fernet으로 암호화됩니다. `shared-api.enc`가 설정되면 방문자는 키를 입력하지 않고 공용 챗봇을 사용할 수 있으며, 공개 API에서는 공용 키 변경·삭제를 허용하지 않습니다. 공용 키 설정은 서버 관리자 작업입니다. 키가 없는 새로운 배포는 별도로 API 연결을 설정해야 합니다.

회사 정보와 보관함은 사용자 브라우저의 localStorage에 저장됩니다. 원본 이미지는 브라우저에서 OCR 처리하며, 문안 제작·검토 시 입력 텍스트는 설정된 AgentStream 서비스로 전송됩니다.

공용 API 모드는 주소를 아는 방문자에게 연결된 챗봇과 지식베이스 이용을 제공합니다. 별도 로그인이나 요청량 제한은 포함되어 있지 않습니다.

## 검증 범위

개발 과정에서 JavaScript/Python 구문, Excel 파싱, 완료 이벤트 처리, 원본·수정본 저장, 분할 패널, 진행률, 공용 연결의 쿠키 없는 인증 및 공개 키 변경 차단을 점검했습니다. 실제 지식베이스 답변은 연결된 챗봇 설정과 문서에 따라 달라집니다.

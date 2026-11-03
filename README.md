# 이어하기 (Resume)

출근 1분 만에 어제 작업으로 복귀하는 개인 웹 도구. 멈출 때 한 줄 남기면, 돌아올 때 이어하기 카드 한 장이 기다린다.
기획: `PRD-이어하기 v1.2`

## 구성

| 경로 | 내용 |
|---|---|
| `index.py` | Vercel 진입점 (`backend.main:app`) |
| `backend/main.py` | FastAPI 라우트 (인증, 홈/카드, 프로젝트·작업, 기록, 브리핑, 체험 모드, Cron) |
| `backend/db.py` | SQLAlchemy 모델 — 로컬 SQLite / 배포 PostgreSQL 같은 코드 |
| `backend/services.py` | 이어하기 카드 조립 (수동 기록 + 자동 초안 병합 규칙) |
| `backend/vcs.py` | `git status`(영/한), `git status --short`, `svn status` 해석 |
| `backend/briefing.py` | Gemini 복귀 브리핑 (버튼 호출만, 입력 해시가 같으면 캐시) |
| `backend/seed.py` | 체험 모드 샘플 데이터 (결제시스템 #7, 인사관리 SVN, 다음엔 없는 날) |
| `public/` | 빌드 없는 SPA (`index.html`, `app.js`, `style.css`) |
| `tests/test_api.py` | 파서·API·체험 모드 테스트 |

## 로컬 실행 (Windows)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt uvicorn httpx
.venv/Scripts/python -m uvicorn index:app --reload --port 8000
```

http://localhost:8000 — DB는 `./resume.db`(SQLite)에 자동 생성.

로컬 환경 변수는 `.env.example`을 `.env`로 복사해서 채운다 (예: `GEMINI_API_KEY=...`). `.env`는 git·Vercel 배포에서 제외되며, 이미 설정된 환경 변수가 우선한다. 바꾼 뒤에는 서버를 재시작해야 반영된다.

테스트:

```bash
.venv/Scripts/python tests/test_api.py
```

## 배포 (Vercel + Supabase)

1. Supabase 프로젝트 생성 → Project Settings → Database → **Connection pooling(Transaction 모드, 포트 6543)** URL 복사
2. Vercel에 이 폴더를 import (Framework: 자동 감지 FastAPI / Other)
3. 환경 변수 설정
   - `DATABASE_URL` — 1번 URL (필수, 없으면 `/tmp` SQLite라 데이터가 사라짐)
   - `SECRET_KEY` — 임의의 긴 문자열 (필수)
   - `GEMINI_API_KEY` — 선택. 없으면 요약 버튼이 숨겨짐
   - `GEMINI_MODEL` — 선택. 기본 `gemini-3.5-flash` (`gemini-2.5-flash`는 신규 사용자 제공 종료). 바쁘거나 없으면 `GEMINI_FALLBACK_MODELS` 순서로 재시도
   - `CRON_SECRET` — 선택. 설정하면 Cron 엔드포인트 보호
4. 배포. 테이블은 첫 요청 때 자동 생성됨
5. `vercel.json`의 Cron이 매일 03:00(KST)에 만료된 체험 계정을 삭제. 체험 계정을 새로 만들 때도 만료분을 정리함

### 구현 시작 시 확인 항목 (PRD 8.3·8.6)
- Vercel Python 런타임: FastAPI 무설정 배포 지원. `public/`은 CDN에서 정적 서빙
- Vercel Cron: Hobby 플랜은 하루 1회까지 → 매일 1회로 설정, 요청 시 정리로 보완
- Supabase 무료 플랜: 일정 기간 미사용 시 일시정지됨 → 매일 쓰면 문제없음. 평가 기간엔 대시보드에서 상태 확인
- Gemini: 무료 등급 입력은 Google 제품 개선에 쓰일 수 있음 → 실제 업무 데이터로 요약 기능을 쓰지 말 것. 모델명·무료 한도는 Google AI Studio에서 확인 후 `GEMINI_MODEL`로 지정

## 주요 동작 규칙
- 자동 저장: 멈춤 기록 화면에서 5분마다 + 탭 전환/창 닫기/페이지 이동 시(`sendBeacon`, 실패 시 `keepalive fetch`). 화면을 연 직후 값과 직전 저장 값 모두와 다를 때만 저장. 마지막 수동 기록 이후 자동 기록은 1건만 유지하고 갱신
- 카드: 활성 작업의 최신 기록. 자동 초안이 더 새로우면 초안 내용 + 수동 기록의 "다음엔"을 합쳐 표시
- 로그인: 이름 + 4자리 PIN(PBKDF2 해시), 5회 실패 시 5분 잠금, HttpOnly 서명 쿠키 30일
- 체험 모드: 방문자별 임시 계정 + 샘플 복사, 24시간 후 삭제, IP당 시간당 20회 제한(IP는 해시로만 저장). 브리핑은 미리 만든 요약만 보여주고 API를 호출하지 않음
- 단축키(시작 화면): `S` 멈춤 기록, `C` 프롬프트 복사. 한글 입력 상태에서도 동작

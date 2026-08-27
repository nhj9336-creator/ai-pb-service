"""AI PB 서비스 백엔드 API 서버.

- 매일 08:00(Asia/Seoul)에 collector.py -> pb_engine.py 파이프라인을 자동 실행해
  pb_report_latest.json을 갱신한다.
- GET  /api/pb-report    : 저장된 최신 리포트를 반환.
- POST /api/generate-now : 즉시 수집+리포트 생성을 실행(개발/테스트용).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from collector import collect_market_data
from pb_engine import OUTPUT_PATH_DEFAULT, generate_pb_report_async

# Windows 콘솔 기본 인코딩(cp949)에서 한글 로그가 깨지는 것을 방지
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ai-pb-service")
logger.setLevel(logging.INFO)

# pykrx는 KRX 응답 오류 시 루트 로거(logging.info)에 직접 잘못된 포맷 인자를 넘겨
# "Logging error" 스택트레이스를 대량으로 찍는 버그가 있다(기능상 무해하지만 실제
# 에러를 로그에서 찾기 어렵게 만든다). 루트 로거 레벨을 올려 이 노이즈만 걸러낸다.
logging.getLogger().setLevel(logging.WARNING)

REPORT_PATH = Path(OUTPUT_PATH_DEFAULT)
SCHEDULE_TIMEZONE = "Asia/Seoul"

class GenerationInProgressError(RuntimeError):
    """이미 다른 리포트 생성 작업이 진행 중일 때 발생."""


# 스케줄러 작업과 수동 트리거(/api/generate-now)가 동시에 겹쳐 파일을 쓰지 않도록 보호.
# 정상 종료 시에는 finally에서 항상 해제되지만(원래도 그랬음), 클라이언트/프록시가
# 응답을 기다리다 타임아웃되어도 서버 스레드는 계속 실행되므로 - 그 사이 락은 여전히
# "정당하게" 점유된 상태다. 다만 예상치 못한 하드행(hang) 등 만약의 경우에 대비해
# TTL을 넘기면 죽은 락으로 간주하고 강제로 회수하는 워치독을 추가한다.
_generation_lock = threading.Lock()
_generation_started_monotonic: Optional[float] = None
GENERATION_LOCK_TTL_SECONDS = 120  # 2분


def _acquire_generation_lock() -> bool:
    global _generation_started_monotonic

    if _generation_lock.acquire(blocking=False):
        _generation_started_monotonic = time.monotonic()
        return True

    started = _generation_started_monotonic
    if started is not None and (time.monotonic() - started) > GENERATION_LOCK_TTL_SECONDS:
        logger.warning(
            "리포트 생성 락이 %d초 넘게 유지되어 정체된 것으로 판단, 강제로 회수합니다.",
            GENERATION_LOCK_TTL_SECONDS,
        )
        try:
            _generation_lock.release()
        except RuntimeError:
            pass  # 그 사이 원래 작업이 정상 종료하며 이미 해제한 경우
        if _generation_lock.acquire(blocking=False):
            _generation_started_monotonic = time.monotonic()
            return True

    return False


def _release_generation_lock() -> None:
    global _generation_started_monotonic
    _generation_started_monotonic = None
    try:
        _generation_lock.release()
    except RuntimeError:
        pass  # TTL 워치독이 그 사이 이미 강제 회수한 경우 대비


def reset_generation_lock() -> None:
    """서버 시작 시 잠금 상태를 명시적으로 초기화한다(신규 프로세스라 이미 비어있지만,
    상태를 명확히 하고 향후 리팩터링에도 안전하도록 방어적으로 호출한다)."""
    global _generation_started_monotonic
    if _generation_lock.locked():
        try:
            _generation_lock.release()
        except RuntimeError:
            pass
    _generation_started_monotonic = None
    _generation_status.update(running=False, last_error=None)


_generation_status: dict[str, Any] = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_success": None,
    "last_error": None,
}


async def run_report_pipeline(target_date: Optional[str] = None, provider: Optional[str] = None) -> dict:
    """collector + pb_engine 파이프라인을 1회 실행한다. 스케줄러/엔드포인트 공용 진입점.

    pb_engine의 리포트 생성 단계는 시장총평/자산배분, 국내종목, 미국종목, 뉴스 4개 태스크를
    asyncio.gather로 동시에 호출하므로(각 태스크 자체 타임아웃 LLM_TIMEOUT_SECONDS + 재시도),
    이 함수 전체를 async로 유지해 이벤트 루프 위에서 곧바로 await한다. try/finally로 예외가
    나더라도 _generation_lock이 항상 안전하게 해제되도록 한다.
    """
    if not _acquire_generation_lock():
        raise GenerationInProgressError("이미 리포트 생성이 진행 중입니다. 잠시 후 다시 시도하세요.")

    _generation_status.update(running=True, last_started_at=_now_iso(), last_error=None)
    try:
        logger.info("리포트 생성 시작 (target_date=%s, provider=%s)", target_date, provider)
        # collect_market_data는 동기/블로킹 함수이므로 스레드풀로 위임해 이벤트 루프를 막지 않는다.
        market_data = await run_in_threadpool(collect_market_data, target_date)
        report = await generate_pb_report_async(target_date=target_date, provider=provider, market_data=market_data)
        _generation_status.update(last_success=_now_iso(), last_error=None)
        logger.info("리포트 생성 완료: %s", report.get("meta"))
        return report
    except Exception as exc:
        _generation_status.update(last_error=str(exc))
        logger.exception("리포트 생성 실패")
        raise
    finally:
        _generation_status.update(running=False, last_finished_at=_now_iso())
        _release_generation_lock()


def _now_iso() -> str:
    from collector import _now_kst

    return _now_kst().isoformat(timespec="seconds")


def _scheduled_job() -> None:
    """매일 08:00에 실행되는 자동 갱신 작업. 실패해도 스케줄러 자체는 죽지 않는다.

    APScheduler의 BackgroundScheduler는 별도 OS 스레드(이벤트 루프 없음)에서 잡을 실행하므로,
    async 파이프라인은 asyncio.run()으로 새 이벤트 루프를 만들어 실행한다.
    """
    try:
        asyncio.run(run_report_pipeline())
    except Exception:
        logger.exception("스케줄된 리포트 자동 갱신 실패")


scheduler = BackgroundScheduler(timezone=SCHEDULE_TIMEZONE)
scheduler.add_job(
    _scheduled_job,
    trigger=CronTrigger(hour=8, minute=0, timezone=SCHEDULE_TIMEZONE),
    id="daily_pb_report",
    name="매일 08:00 PB 리포트 자동 갱신",
    replace_existing=True,
    misfire_grace_time=3600,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    reset_generation_lock()
    scheduler.start()
    logger.info("스케줄러 시작: 매일 08:00(Asia/Seoul) PB 리포트 자동 갱신 등록됨")
    yield
    scheduler.shutdown(wait=False)
    logger.info("스케줄러 종료")


app = FastAPI(title="AI PB Service", description="AI 기반 Senior PB 리포트 API", version="1.0.0", lifespan=lifespan)

# 웹 프론트엔드(별도 오리진)에서 API를 호출할 수 있도록 허용.
# ALLOWED_ORIGINS 환경 변수(콤마 구분)로 배포 도메인을 지정하며, 미설정 시 운영 Vercel 도메인만 허용한다.
_DEFAULT_ALLOWED_ORIGINS = "https://ai-pb-service-7chm.vercel.app,http://localhost:3000"
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# 차트 히스토리 확장(2024-01~현재, 종목 유니버스 22개)으로 리포트 payload가 수 MB로 커져
# 압축 없이는 전송이 느릴 수 있다.
app.add_middleware(GZipMiddleware, minimum_size=1000)


class GenerateNowRequest(BaseModel):
    target_date: Optional[str] = None  # "YYYY-MM-DD" (생략 시 오늘)
    provider: Optional[str] = None  # "openai" | "gemini" (생략 시 환경 변수로 자동 판단)


@app.get("/api/pb-report")
def get_pb_report() -> dict:
    """마지막으로 저장된 최신 PB 리포트를 반환한다."""
    if not REPORT_PATH.exists():
        raise HTTPException(status_code=404, detail="아직 생성된 리포트가 없습니다. POST /api/generate-now로 먼저 생성하세요.")
    try:
        with REPORT_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"저장된 리포트 파일이 손상되었습니다: {exc}") from exc


@app.post("/api/generate-now")
async def generate_now(payload: Optional[GenerateNowRequest] = None) -> dict:
    """개발/테스트용: 즉시 시장 데이터를 수집하고 PB 리포트를 생성해 저장한다."""
    target_date = payload.target_date if payload else None
    provider = payload.provider if payload else None

    try:
        report = await run_report_pipeline(target_date, provider)
    except GenerationInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"리포트 생성 중 오류가 발생했습니다: {exc}") from exc

    return {"message": "리포트가 생성되었습니다.", "report": report}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

"""AI PB 서비스 백엔드 API 서버.

- 매일 08:00(Asia/Seoul)에 collector.py -> pb_engine.py 파이프라인을 자동 실행해
  pb_report_latest.json을 갱신한다.
- GET  /api/pb-report    : 저장된 최신 리포트를 반환.
- POST /api/generate-now : 즉시 수집+리포트 생성을 실행(개발/테스트용).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from collector import collect_market_data
from pb_engine import OUTPUT_PATH_DEFAULT, generate_pb_report

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


# 스케줄러 작업과 수동 트리거(/api/generate-now)가 동시에 겹쳐 파일을 쓰지 않도록 보호
_generation_lock = threading.Lock()
_generation_status: dict[str, Any] = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_success": None,
    "last_error": None,
}


def run_report_pipeline(target_date: Optional[str] = None, provider: Optional[str] = None) -> dict:
    """collector + pb_engine 파이프라인을 1회 실행한다. 스케줄러/엔드포인트 공용 진입점."""
    if not _generation_lock.acquire(blocking=False):
        raise GenerationInProgressError("이미 리포트 생성이 진행 중입니다. 잠시 후 다시 시도하세요.")

    _generation_status.update(running=True, last_started_at=_now_iso(), last_error=None)
    try:
        logger.info("리포트 생성 시작 (target_date=%s, provider=%s)", target_date, provider)
        market_data = collect_market_data(target_date)
        report = generate_pb_report(target_date=target_date, provider=provider, market_data=market_data)
        _generation_status.update(last_success=_now_iso(), last_error=None)
        logger.info("리포트 생성 완료: %s", report.get("meta"))
        return report
    except Exception as exc:
        _generation_status.update(last_error=str(exc))
        logger.exception("리포트 생성 실패")
        raise
    finally:
        _generation_status.update(running=False, last_finished_at=_now_iso())
        _generation_lock.release()


def _now_iso() -> str:
    import datetime as dt

    return dt.datetime.now().isoformat(timespec="seconds")


def _scheduled_job() -> None:
    """매일 08:00에 실행되는 자동 갱신 작업. 실패해도 스케줄러 자체는 죽지 않는다."""
    try:
        run_report_pipeline()
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
    scheduler.start()
    logger.info("스케줄러 시작: 매일 08:00(Asia/Seoul) PB 리포트 자동 갱신 등록됨")
    yield
    scheduler.shutdown(wait=False)
    logger.info("스케줄러 종료")


app = FastAPI(title="AI PB Service", description="AI 기반 Senior PB 리포트 API", version="1.0.0", lifespan=lifespan)

# 웹 프론트엔드(별도 오리진)에서 API를 호출할 수 있도록 허용.
# ALLOWED_ORIGINS 환경 변수(콤마 구분)로 배포 도메인을 지정하며, 미설정 시 운영 Vercel 도메인만 허용한다.
_DEFAULT_ALLOWED_ORIGINS = "https://ai-pb-service-7chm.vercel.app"
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
        report = await run_in_threadpool(run_report_pipeline, target_date, provider)
    except GenerationInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"리포트 생성 중 오류가 발생했습니다: {exc}") from exc

    return {"message": "리포트가 생성되었습니다.", "report": report}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

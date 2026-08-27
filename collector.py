"""지정일 기준 종합 시장 데이터 수집 모듈.

국내/미국 지수 및 수급, 주요 종목 기술적 지표, 미국 거시경제(FRED) 지표,
DART 주요 공시, 구글 뉴스 RSS 팩트 뉴스를 한 번에 수집해 JSON 직렬화 가능한
dict로 반환한다.

필요 환경 변수 (.env 또는 시스템 환경 변수):
    DART_API_KEY  - Open DART API 인증키
    FRED_API_KEY  - FRED API 인증키
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional, TypeVar, Union
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from fredapi import Fred
from pykrx import stock as krx

try:
    import OpenDartReader
except ImportError:  # pragma: no cover
    OpenDartReader = None

load_dotenv()

logger = logging.getLogger("ai-pb-service.collector")

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

GLOBAL_INDEX_TICKERS: dict[str, str] = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DJI": "^DJI",   # 다우존스 산업지수
    "SOX": "^SOX",   # 필라델피아 반도체 지수
}

# 기관/외국인 순매수 수급은 국내(KRX) 시장에만 존재
KRX_SUPPLY_DEMAND_MARKETS: dict[str, str] = {
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
}

# 기술적 지표(OHLCV/이평선/지지·저항)를 수집할 국내 종목 유니버스 (KRX 종목코드 -> 종목명).
# 초대형주 편중을 피하기 위해 반도체/자동차/2차전지/플랫폼/바이오/게임 등 주도 섹터별로
# 중형주까지 섞어 구성한다 - AI 추천은 이 유니버스 내에서만 가능하므로 다양성의 원천이 된다.
MAJOR_STOCKS: dict[str, str] = {
    "005930": "삼성전자",       # 반도체
    "000660": "SK하이닉스",     # 반도체
    "035420": "NAVER",         # 플랫폼
    "035720": "카카오",         # 플랫폼
    "005380": "현대차",         # 자동차
    "012330": "현대모비스",     # 자동차부품
    "051910": "LG화학",        # 2차전지/화학
    "006400": "삼성SDI",       # 2차전지
    "373220": "LG에너지솔루션", # 2차전지
    "207940": "삼성바이오로직스", # 바이오
    "068270": "셀트리온",       # 바이오
    "259960": "크래프톤",       # 게임(중형주)
}

# 기술적 지표를 수집할 미국 종목 유니버스 (티커 -> 종목명). 메가캡 위주에 반도체/AI 성장주를 더해
# 섹터 다양성을 확보한다.
US_STOCKS: dict[str, str] = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "GOOGL": "Alphabet",
    "TSLA": "Tesla",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "PLTR": "Palantir",
    "AMZN": "Amazon",
    "META": "Meta Platforms",
}

FRED_SERIES: dict[str, str] = {
    "US_BASE_RATE": "FEDFUNDS",      # 미국 기준금리(effective federal funds rate)
    "US_10Y_TREASURY": "DGS10",      # 미국 10년물 국채금리
    "US_CPI": "CPIAUCSL",            # 미국 소비자물가지수(CPI, 도시 전체 항목)
}

NEWS_RSS_QUERIES: list[str] = ["코스피", "코스닥", "미국 증시", "금리", "반도체", "환율"]

MA_WINDOWS = (5, 20, 60, 120)
CHART_HISTORY_START_DATE = dt.date(2024, 1, 1)  # 인터랙티브 차트 조회 시작일(약 2.5년치)
OHLCV_MAX_BARS = 800  # 페이로드 폭주 방지용 안전 상한(2024-01 기준 실제 거래일수보다 넉넉함)
TREND_CHANNEL_LOOKBACK = 60  # 대각선 추세선(고점-고점/저점-저점) 계산에 사용할 최근 거래일수
PIVOT_LOOKBACK_DAYS = 5  # 피봇 포인트 계산에 사용할 최근 거래일수(주간 단위 - 전일 단순 피봇 대비 굵직한 지지/저항선)
DART_LOOKBACK_DAYS = 7
DART_UNIVERSE_SIZE = 20  # DART 공시를 조회할 시가총액 상위 종목 수
DART_DISCLOSURES_PER_STOCK = 3  # 종목당 프롬프트에 넘길 최근 공시 개수(유니버스 확대에 따른 과다 방지)
DART_MAX_WORKERS = 5  # DART 공시 조회 동시 요청 수 상한
NEWS_MAX_WORKERS = 4  # 뉴스 RSS 쿼리 동시 요청 수 상한
NEWS_ITEMS_PER_QUERY = 5
FRED_LOOKBACK_OBS = 24

# 외부 시세 API(yfinance/KRX) 호출 시 레이트리밋을 피하기 위한 지연 및 재시도 설정
REQUEST_DELAY_SEC = 0.7
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 2.0

DateLike = Union[str, dt.date, dt.datetime, None]
_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

# 배포 서버(Render 등)는 보통 UTC로 동작하지만, 이 서비스는 한국 투자자를 위한 KRX/국내
# 시황 서비스이므로 "오늘"/"지금"은 항상 한국 표준시(KST, UTC+9) 기준이어야 한다. 서버
# 타임존에 의존하는 dt.date.today()/dt.datetime.now()를 직접 쓰지 않고 아래 헬퍼를 통해서만
# 현재 시각을 구한다.
KST = ZoneInfo("Asia/Seoul")


def _now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def _today_kst() -> dt.date:
    return _now_kst().date()


def _to_date(target_date: DateLike) -> dt.date:
    if target_date is None:
        return _today_kst()
    if isinstance(target_date, dt.datetime):
        return target_date.date()
    if isinstance(target_date, dt.date):
        return target_date
    return dt.datetime.strptime(target_date, "%Y-%m-%d").date()


_NUMERIC_CLEAN_RE = re.compile(r"[^0-9.\-]")


def _safe_num(value: Any, ndigits: int = 2) -> Optional[float]:
    """숫자로 변환을 시도하고, 콤마/통화기호/손상된 문자열 등으로 실패하면 None을 반환한다.

    KRX/yfinance가 드물게 비정상적인 문자열(예: "84,000원", 손상된 값)을 돌려주더라도
    이 함수가 예외를 던지지 않으므로 호출부의 데이터 한 건이 통째로 유실되지 않는다.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        if isinstance(value, str):
            cleaned = _NUMERIC_CLEAN_RE.sub("", value)
            if cleaned in ("", "-", "."):
                return None
            value = cleaned
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    num = _safe_num(value, ndigits=0)
    return int(num) if num is not None else None


def _clean_numeric_column(series: "pd.Series") -> "pd.Series":
    """OHLCV 컬럼에 섞여 들어온 비정상 문자열을 정리해 숫자로 변환한다.

    끝내 변환 불가능한 값은 NaN으로 남기고(행 삭제는 호출부에서 판단), 정상 숫자
    컬럼(int64/float64)에 적용해도 안전하다.
    """
    cleaned = series.astype(str).str.replace(_NUMERIC_CLEAN_RE, "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def _retry_with_backoff(fn: Callable[[], _T], *, context: str = "") -> _T:
    """일시적 오류(레이트리밋 등)에 대해 지수 백오프로 최대 RETRY_MAX_ATTEMPTS회 재시도한다."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 마지막 시도 실패 시 위로 그대로 전파
            last_exc = exc
            if attempt < RETRY_MAX_ATTEMPTS:
                time.sleep(RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# 1. 국내/미국 지수 및 수급
# ---------------------------------------------------------------------------

SUPPLY_DEMAND_HISTORY_DAYS = 10  # 매집 구간 분석용으로 함께 제공할 최근 거래일 수

def _fetch_yf_index_snapshot(ticker: str, target_date: dt.date) -> dict:
    start = target_date - dt.timedelta(days=30)
    end = target_date + dt.timedelta(days=1)

    def _fetch() -> pd.DataFrame:
        return yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), interval="1d")

    hist = _retry_with_backoff(_fetch, context=f"index:{ticker}")
    if hist.empty:
        raise ValueError(f"{ticker}: 조회 구간에 데이터가 없습니다.")
    hist = hist[hist.index.date <= target_date]
    if hist.empty:
        raise ValueError(f"{ticker}: {target_date} 이전 거래 데이터가 없습니다.")

    last = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) >= 2 else None
    last_close = _safe_num(last["Close"])
    last_date = hist.index[-1].date()

    # 조회 시점이 오늘(KST)이고 방금 받아온 봉도 오늘자라면, 장중에 계속 갱신 중인
    # "진행 중" 봉일 수 있다 - history()의 일봉 종가보다 fast_info의 실시간 시세가 조회
    # 시점을 더 정확히 반영하므로 있으면 우선 사용한다(실패해도 일봉 종가로 안전하게 대체).
    if last_date == target_date == _today_kst():
        try:
            live_price = yf.Ticker(ticker).fast_info.last_price
            if live_price:
                last_close = _safe_num(live_price)
        except Exception:
            pass

    prev_close = _safe_num(prev["Close"]) if prev is not None else None
    change = (last_close - prev_close) if (last_close is not None and prev_close is not None) else None
    change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None

    # 매집 구간 분석(수급 시계열과 가격대 대조)을 위해 최근 며칠의 종가도 함께 넘긴다.
    # 이미 받아온 hist 윈도우를 재사용하므로 추가 API 호출은 없다.
    recent = hist.tail(SUPPLY_DEMAND_HISTORY_DAYS)
    price_history = [
        {"date": idx.date().isoformat(), "close": _safe_num(row["Close"])} for idx, row in recent.iterrows()
    ]

    return {
        "date": last_date.isoformat(),
        "close": last_close,
        "change": _safe_num(change),
        "change_pct": _safe_num(change_pct),
        "volume": _safe_int(last.get("Volume")),
        "price_history": price_history,
        "source": "yfinance",
    }


# KRX가 직접 발표하는 공식 지수 코드(야후 파이낸스 미러보다 권위 있는 1차 소스)
KRX_OFFICIAL_INDEX_TICKERS: dict[str, str] = {
    "KOSPI": "1001",
    "KOSDAQ": "2001",
}


def _fetch_krx_official_index_snapshot(name: str, index_ticker: str, target_date: dt.date) -> dict:
    """data.krx.co.kr이 직접 발표하는 공식 지수 종가를 가져온다.

    야후 파이낸스의 ^KS11/^KQ11는 제3자 미러라 KRX 공식 발표치와 미세한 차이가 날 수 있다.
    이 함수는 KRX 원천 데이터를 사용해 그 오차를 없앤다. 다만 이 엔드포인트도 다른 KRX
    통계 API와 마찬가지로 로그인 세션을 요구하므로, 로그인 정보가 없거나 조회가 실패하면
    예외를 던져 호출부가 yfinance로 안전하게 폴백하게 한다(수치 공백보다는 근사치가 낫다).
    """
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        raise ValueError(f"{name}: KRX 공식 지수 조회에는 로그인 세션이 필요합니다.")

    start_str = (target_date - dt.timedelta(days=30)).strftime("%Y%m%d")
    end_str = target_date.strftime("%Y%m%d")

    def _fetch() -> pd.DataFrame:
        return krx.get_index_ohlcv_by_date(start_str, end_str, index_ticker)

    df = _retry_with_backoff(_fetch, context=f"official_index:{name}")
    if df is None or df.empty:
        raise ValueError(f"{name}: KRX 공식 지수 데이터를 찾을 수 없습니다.")
    df = df[df.index.date <= target_date]
    if df.empty:
        raise ValueError(f"{name}: {target_date} 이전 KRX 공식 지수 데이터가 없습니다.")

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    last_close = _safe_num(last["종가"])
    prev_close = _safe_num(prev["종가"]) if prev is not None else None
    change = (last_close - prev_close) if (last_close is not None and prev_close is not None) else None
    change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None

    recent = df.tail(SUPPLY_DEMAND_HISTORY_DAYS)
    price_history = [
        {"date": idx.date().isoformat(), "close": _safe_num(row["종가"])} for idx, row in recent.iterrows()
    ]

    return {
        "date": df.index[-1].date().isoformat(),
        "close": last_close,
        "change": _safe_num(change),
        "change_pct": _safe_num(change_pct),
        "volume": _safe_int(last.get("거래량")),
        "price_history": price_history,
        "source": "KRX",
    }


def _fetch_krx_supply_demand(market: str, target_date: dt.date) -> dict:
    """target_date까지 최근 SUPPLY_DEMAND_HISTORY_DAYS 거래일치 기관/외국인 순매수 시계열을 가져온다.

    KRX의 투자자별 거래실적(기관/외국인 순매수) 엔드포인트는 data.krx.co.kr 로그인 세션을
    요구한다 - 비로그인 요청은 날짜/형식과 무관하게 서버가 HTTP 400 "LOGOUT"으로 즉시
    거부한다는 것을 실제 요청으로 확인했다. KRX_ID/KRX_PW 환경변수가 없으면 pykrx가 처음부터
    비인증 세션으로 요청하므로, 여기서 미리 걸러 불필요한 요청을 반복하지 않고 정확한 사유로
    즉시 실패한다.
    """
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        raise ValueError(
            f"{market}: 기관/외국인 수급 데이터는 KRX 로그인 세션이 있어야 조회할 수 있습니다 "
            "(KRX_ID/KRX_PW 환경변수 미설정 - data.krx.co.kr가 비로그인 요청을 거부함)."
        )

    # 주말/공휴일을 감안해 넉넉히 달력일 기준으로 조회한 뒤 실제 거래일만 남긴다.
    start_str = (target_date - dt.timedelta(days=SUPPLY_DEMAND_HISTORY_DAYS * 2)).strftime("%Y%m%d")
    end_str = target_date.strftime("%Y%m%d")

    def _fetch() -> pd.DataFrame:
        return krx.get_market_trading_value_by_date(start_str, end_str, market)

    df = _retry_with_backoff(_fetch, context=f"flow:{market}")
    if df is None or df.empty:
        # 장중에는 당일자 수급 통계가 아직 집계되지 않아 조회 구간에 데이터가 전혀 없을 수
        # 있다(휴장일도 동일). 호출부(collect_index_and_flow_data)가 이미 예외를 잡아
        # supply_demand=None으로 안전하게 처리하므로, 여기서는 원인이 분명한 메시지로
        # 빠르게 실패시켜 디버깅을 돕는다.
        raise ValueError(f"{market}: {target_date} 기준 유효한 수급 데이터가 없습니다(장중 집계 지연 또는 휴장일 가능).")

    institution_col = next((c for c in df.columns if "기관" in c), None)
    foreign_col = next((c for c in df.columns if c.startswith("외국인")), None)
    individual_col = next((c for c in df.columns if "개인" in c), None)

    df = df.sort_index().tail(SUPPLY_DEMAND_HISTORY_DAYS)
    history = [
        {
            "date": idx.date().isoformat(),
            "institution_net_buy": _safe_int(row.get(institution_col)) if institution_col else None,
            "foreign_net_buy": _safe_int(row.get(foreign_col)) if foreign_col else None,
            "individual_net_buy": _safe_int(row.get(individual_col)) if individual_col else None,
        }
        for idx, row in df.iterrows()
    ]
    latest = history[-1]
    return {
        "date": latest["date"],
        "institution_net_buy": latest["institution_net_buy"],
        "foreign_net_buy": latest["foreign_net_buy"],
        "individual_net_buy": latest["individual_net_buy"],
        "history": history,
    }


def _fetch_single_index(name: str, ticker: str, target_date: dt.date) -> dict:
    """지수 하나를 가져온다. KOSPI/KOSDAQ는 KRX 공식 수치를 우선 시도하고(야후 미러보다
    권위 있는 1차 소스), 실패하면 조용히 yfinance로 폴백한다.

    장중(09:00~15:30)에는 KRX 공식 통계에 당일자가 아직 게시되지 않아 조회에는 성공하되
    전일 종가로 남아있을 수 있다(예외가 아니라 "정상적으로 오래된 값"이라 위 실패 폴백만으로는
    잡히지 않는다). 오늘 날짜를 조회하는데 KRX 결과가 오늘자가 아니면, 일별 봉이 장중에도
    실시간으로 갱신되는 yfinance로 대신 폴백해 더 최신 값을 쓴다."""
    if name in KRX_OFFICIAL_INDEX_TICKERS:
        try:
            snapshot = _fetch_krx_official_index_snapshot(name, KRX_OFFICIAL_INDEX_TICKERS[name], target_date)
            if target_date != _today_kst() or snapshot.get("date") == target_date.isoformat():
                return snapshot
        except Exception:
            pass
    return _fetch_yf_index_snapshot(ticker, target_date)


def collect_index_and_flow_data(target_date: dt.date) -> dict:
    errors: list[str] = []

    # 1단계: 지수 스냅샷을 병렬로 조회한다.
    result: dict = {}
    with ThreadPoolExecutor(max_workers=len(GLOBAL_INDEX_TICKERS)) as executor:
        future_to_name = {
            executor.submit(_fetch_single_index, name, ticker, target_date): name
            for name, ticker in GLOBAL_INDEX_TICKERS.items()
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result[name] = future.result()
            except Exception as exc:
                result[name] = None
                errors.append(f"[index:{name}] {exc}")

    # 2단계: 위에서 성공한 국내 지수에 한해 수급(기관/외국인 순매수)을 병렬로 덧붙인다.
    targets = {name: market for name, market in KRX_SUPPLY_DEMAND_MARKETS.items() if result.get(name) is not None}
    if targets:
        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            future_to_name = {
                executor.submit(_fetch_krx_supply_demand, market, target_date): name
                for name, market in targets.items()
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    result[name]["supply_demand"] = future.result()
                except Exception as exc:
                    result[name]["supply_demand"] = None
                    errors.append(f"[flow:{name}] {exc}")

    return {"data": result, "errors": errors}


# ---------------------------------------------------------------------------
# 2. 차트용 기술적 지표 (OHLCV, 이동평균, 피봇/지지·저항)
# ---------------------------------------------------------------------------

def _compute_pivot_levels(df: pd.DataFrame) -> dict:
    """최근 PIVOT_LOOKBACK_DAYS 거래일(주간 단위)의 고가/저가와 최근 종가로 피보나치 피봇을
    계산한다. 전일 단순(Daily Floor Trader's) 피봇은 하루짜리 변동폭만 반영해 지지/저항선이
    현재가 근처에 촘촘하게 몰리는 문제가 있어, 스윙 타임프레임에 맞는 주간 고가/저가 기준으로
    바꾸고 간격도 피보나치 비율(0.382, 0.618)로 넓게 전개한다.

    Fibonacci Pivot Points:
        P  = (WeeklyHigh + WeeklyLow + Close) / 3
        R1 = P + 0.382 * (WeeklyHigh - WeeklyLow)
        S1 = P - 0.382 * (WeeklyHigh - WeeklyLow)
        R2 = P + 0.618 * (WeeklyHigh - WeeklyLow)
        S2 = P - 0.618 * (WeeklyHigh - WeeklyLow)
    """
    empty = {"pivot": None, "resistance_1": None, "resistance_2": None, "support_1": None, "support_2": None}

    window = df.tail(PIVOT_LOOKBACK_DAYS)
    if window.empty:
        return empty

    high, low = _safe_num(window["고가"].max()), _safe_num(window["저가"].min())
    close = _safe_num(df.iloc[-1]["종가"])
    if high is None or low is None or close is None:
        return empty

    pivot = (high + low + close) / 3
    diff = high - low
    r1, s1 = pivot + 0.382 * diff, pivot - 0.382 * diff
    r2, s2 = pivot + 0.618 * diff, pivot - 0.618 * diff
    return {
        "pivot": _safe_num(pivot),
        "resistance_1": _safe_num(r1),
        "resistance_2": _safe_num(r2),
        "support_1": _safe_num(s1),
        "support_2": _safe_num(s2),
    }


def _compute_support_resistance(df: pd.DataFrame) -> dict:
    """가장 최근 봉을 제외한 구간의 전고점/전저점을 지지/저항 후보로 계산한다."""
    prior = df.iloc[:-1]
    result: dict = {}
    for window in (20, 60):
        window_df = prior.tail(window)
        result[f"prev_high_{window}d"] = _safe_num(window_df["고가"].max()) if not window_df.empty else None
        result[f"prev_low_{window}d"] = _safe_num(window_df["저가"].min()) if not window_df.empty else None
    return result


def _find_swing_indices(values: pd.Series, window: int, mode: str) -> list[int]:
    """values 내에서 앞뒤 window개 봉보다 극단적인(고점/저점) 위치의 정수 인덱스 목록을 찾는다.

    표준 프랙탈(fractal) 스윙 포인트 정의: 중심 봉의 값이 좌우 window개씩을 포함한 구간에서
    최댓값(mode='high')이거나 최솟값(mode='low')이면 스윙 포인트로 본다.
    """
    n = len(values)
    idxs: list[int] = []
    for i in range(window, n - window):
        segment = values.iloc[i - window : i + window + 1]
        target = values.iloc[i]
        if mode == "high" and target == segment.max():
            idxs.append(i)
        elif mode == "low" and target == segment.min():
            idxs.append(i)
    return idxs


def _compute_trend_channel(df: pd.DataFrame) -> dict:
    """최근 TREND_CHANNEL_LOOKBACK 거래일 내 고점-고점, 저점-저점을 이은 대각선 추세선을 계산한다.

    표준 기술적 분석의 프랙탈 스윙 포인트 방식으로 고점/저점을 찾고, 구간 내 첫 번째와 마지막
    스윙 포인트를 직선으로 연결해 마지막 봉(가장 최근 거래일) 위치까지 연장한 값을 반환한다.
    스윙 포인트가 2개 미만이면(추세를 판단할 근거가 부족하면) null을 반환하고 억지로 선을
    만들지 않는다.
    """
    recent = df.tail(TREND_CHANNEL_LOOKBACK)
    if len(recent) < 10:
        return {"resistance_trendline": None, "support_trendline": None}

    dates = list(recent.index)
    last_i = len(recent) - 1

    def _line_from_swings(values: pd.Series, mode: str) -> Optional[dict]:
        idxs = _find_swing_indices(values, window=2, mode=mode)
        if len(idxs) < 2:
            return None
        i1, i2 = idxs[0], idxs[-1]
        if i1 == i2:
            return None
        v1, v2 = _safe_num(values.iloc[i1]), _safe_num(values.iloc[i2])
        if v1 is None or v2 is None:
            return None
        slope = (v2 - v1) / (i2 - i1)
        end_value = v1 + slope * (last_i - i1)
        return {
            "start_date": dates[i1].date().isoformat(),
            "start_value": _safe_num(v1),
            "end_date": dates[last_i].date().isoformat(),
            "end_value": _safe_num(end_value),
            "direction": "상승" if slope > 0 else ("하락" if slope < 0 else "횡보"),
        }

    return {
        "resistance_trendline": _line_from_swings(recent["고가"], "high"),
        "support_trendline": _line_from_swings(recent["저가"], "low"),
    }


def _build_technical_payload(df: pd.DataFrame, name: str) -> dict:
    """시가/고가/저가/종가/거래량(한글 컬럼) DataFrame으로부터 차트용 페이로드를 만든다."""
    df = df.tail(OHLCV_MAX_BARS).copy()

    # KRX/yfinance가 드물게 손상된 문자열 값을 섞어 보내는 경우를 대비해 숫자 컬럼을 정리한다.
    # 정리 후에도 시가/고가/저가/종가가 유효하지 않은 행은 제외한다(거래량은 부가 정보라 유지).
    for col in ("시가", "고가", "저가", "종가", "거래량"):
        df[col] = _clean_numeric_column(df[col])
    df = df.dropna(subset=["시가", "고가", "저가", "종가"])
    if df.empty:
        raise ValueError(f"{name}: 유효한 OHLCV 데이터가 없습니다(전량 파싱 실패).")

    for w in MA_WINDOWS:
        df[f"MA{w}"] = df["종가"].rolling(window=w, min_periods=1).mean()

    dates = [d.date().isoformat() for d in df.index]
    close = [_safe_num(v) for v in df["종가"]]

    return {
        "name": name,
        "dates": dates,
        "open": [_safe_num(v) for v in df["시가"]],
        "high": [_safe_num(v) for v in df["고가"]],
        "low": [_safe_num(v) for v in df["저가"]],
        "close": close,
        "volume": [_safe_int(v) for v in df["거래량"]],
        **{f"ma{w}": [_safe_num(v) for v in df[f"MA{w}"]] for w in MA_WINDOWS},
        "pivot_point": _compute_pivot_levels(df),
        "support_resistance": _compute_support_resistance(df),
        "trend_channel": _compute_trend_channel(df),
        # 기본값은 마지막 확정 종가. 장중 KRX 통계 지연으로 당일 확정 종가가 아직 없는
        # 국내 종목은 호출부(_fetch_stock_technical)가 실시간 시세로 덮어쓴다.
        "current_price": close[-1] if close else None,
        "current_price_is_realtime": False,
    }


def _fetch_realtime_krx_price(code: str) -> Optional[float]:
    """장중(09:00~15:30)에는 KRX 일별 통계에 당일 확정 종가가 아직 게시되지 않는다.
    이 경우 yfinance의 실시간에 가까운 시세(KOSPI는 .KS, KOSDAQ은 .KQ)로 현재가를 보완한다.
    두 접미사 모두 실패하면(상장폐지/코드 오류 등) 예외를 던지지 않고 None을 반환한다 -
    이 값은 부가 정보일 뿐이므로 실패해도 기존 OHLCV 기반 지표(이평선/피봇 등)는 그대로 유효하다."""
    for suffix in (".KS", ".KQ"):
        try:
            price = yf.Ticker(f"{code}{suffix}").fast_info.last_price
            if price:
                return _safe_num(price)
        except Exception:
            continue
    return None


def resolve_domestic_ticker(query: str, target_date: dt.date) -> tuple[str, str]:
    """보유 종목 진단 기능에서 사용자가 입력한 "종목명 또는 종목코드"를 (코드, 종목명)
    튜플로 변환한다.

    1) 6자리 숫자면 종목코드로 간주하고 이름만 조회한다(get_market_ticker_name은 단건
       조회라 로그인 없이도 동작함 - 실제로 확인함).
    2) 그 외 문자열이면 종목명으로 간주해 먼저 고정 유니버스(MAJOR_STOCKS)에서 빠르게
       찾고, 없으면 KRX 전체 종목 목록에서 탐색한다. 이 전체 목록 조회(get_market_ticker_list)는
       투자자별 거래실적 조회와 마찬가지로 KRX 로그인 세션이 필요하므로, 로그인 정보가
       없으면 반복 실패 대신 즉시 명확한 에러로 안내한다.
    """
    query = query.strip()
    if re.fullmatch(r"\d{6}", query):
        try:
            name = krx.get_market_ticker_name(query)
        except Exception:
            name = None
        return query, (name or query)

    for code, name in MAJOR_STOCKS.items():
        if name == query:
            return code, name

    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        raise ValueError(
            f"'{query}'는 종목코드가 아니라 종목명으로 보이는데, 종목명 검색에는 KRX 로그인 세션이 "
            "필요합니다(KRX_ID/KRX_PW 미설정). 정확한 6자리 종목코드를 입력해 주세요."
        )

    date_str = target_date.strftime("%Y%m%d")
    for market in ("KOSPI", "KOSDAQ"):
        try:
            tickers = krx.get_market_ticker_list(date_str, market=market)
        except Exception:
            continue
        for code in tickers:
            try:
                name = krx.get_market_ticker_name(code)
            except Exception:
                continue
            if name == query:
                return code, name

    raise ValueError(f"'{query}'에 해당하는 국내 종목을 찾을 수 없습니다. 정확한 종목코드(6자리)를 입력해 주세요.")


def _fetch_stock_technical(code: str, name: str, target_date: dt.date) -> dict:
    end_str = target_date.strftime("%Y%m%d")
    start_str = CHART_HISTORY_START_DATE.strftime("%Y%m%d")

    def _fetch() -> pd.DataFrame:
        return krx.get_market_ohlcv_by_date(start_str, end_str, code)

    df = _retry_with_backoff(_fetch, context=f"technical:{code}")
    if df is None or df.empty:
        raise ValueError(f"{code}({name}): OHLCV 데이터를 찾을 수 없습니다.")

    payload = _build_technical_payload(df, name)

    # dates[-1]이 target_date(오늘)와 다르면 장중이라 KRX 일별 통계에 당일자가 아직 없다는
    # 뜻이다(과거 날짜 조회는 원래도 확정 데이터라 이 분기를 타지 않는다). 이 때만 실시간
    # 시세로 current_price를 보완한다.
    if target_date == _today_kst() and payload["dates"] and payload["dates"][-1] != target_date.isoformat():
        realtime_price = _fetch_realtime_krx_price(code)
        if realtime_price is not None:
            payload["current_price"] = realtime_price
            payload["current_price_is_realtime"] = True

    return payload


def _fetch_us_stock_technical(ticker: str, name: str, target_date: dt.date) -> dict:
    start = CHART_HISTORY_START_DATE
    end = target_date + dt.timedelta(days=1)

    def _fetch() -> pd.DataFrame:
        return yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), interval="1d")

    hist = _retry_with_backoff(_fetch, context=f"technical_us:{ticker}")
    if hist.empty:
        raise ValueError(f"{ticker}({name}): OHLCV 데이터를 찾을 수 없습니다.")
    hist = hist[hist.index.date <= target_date]
    if hist.empty:
        raise ValueError(f"{ticker}({name}): {target_date} 이전 거래 데이터가 없습니다.")

    df = hist.rename(columns={"Open": "시가", "High": "고가", "Low": "저가", "Close": "종가", "Volume": "거래량"})
    return _build_technical_payload(df, name)


TECHNICAL_MAX_WORKERS = 4  # 동시 KRX/yfinance 요청 수 상한(과도한 병렬은 레이트리밋 위험이 있어 제한)


def _parallel_fetch(
    items: dict[str, str],
    fetch_fn: Callable[[str, str, dt.date], dict],
    target_date: dt.date,
    error_prefix: str,
    max_workers: int = TECHNICAL_MAX_WORKERS,
) -> dict:
    """{키: 이름} 목록을 fetch_fn(키, 이름, target_date)로 병렬 수집한다.

    개별 항목 실패는 서로 격리되어(한 종목 실패가 다른 종목에 영향 없음) errors에 기록되고
    result[키]는 None으로 남는다. max_workers로 동시 요청 수를 제한해 외부 API 레이트리밋을
    피하면서도, 기존 순차 처리(항목당 지연 포함) 대비 전체 소요 시간을 크게 단축한다.
    """
    result: dict = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {executor.submit(fetch_fn, key, name, target_date): key for key, name in items.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                result[key] = future.result()
            except Exception as exc:
                result[key] = None
                errors.append(f"[{error_prefix}:{key}] {exc}")
    return {"data": result, "errors": errors}


def collect_technical_data(target_date: dt.date, stocks: Optional[dict] = None) -> dict:
    """국내(KRX) 종목의 기술적 지표를 병렬로 수집한다."""
    stocks = stocks or MAJOR_STOCKS
    return _parallel_fetch(stocks, _fetch_stock_technical, target_date, "technical")


def collect_us_technical_data(target_date: dt.date, stocks: Optional[dict] = None) -> dict:
    """미국 종목의 기술적 지표를 병렬로 수집한다."""
    stocks = stocks or US_STOCKS
    return _parallel_fetch(stocks, _fetch_us_stock_technical, target_date, "technical_us")


# ---------------------------------------------------------------------------
# 3. 거시경제 (FRED)
# ---------------------------------------------------------------------------

def _fetch_fred_series(fred: Fred, series_id: str, target_date: dt.date, lookback_obs: int) -> list[dict]:
    start = target_date - dt.timedelta(days=lookback_obs * 45)  # 월간 시계열까지 감안한 여유 버퍼
    series = fred.get_series(
        series_id,
        observation_start=start.isoformat(),
        observation_end=target_date.isoformat(),
    )
    series = series.dropna().tail(lookback_obs)
    return [{"date": idx.date().isoformat(), "value": _safe_num(val, 3)} for idx, val in series.items()]


def collect_macro_data(target_date: dt.date) -> dict:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        return {"data": {}, "errors": ["FRED_API_KEY 환경 변수가 설정되지 않았습니다."]}

    fred = Fred(api_key=api_key)
    result: dict = {}
    errors: list[str] = []
    for name, series_id in FRED_SERIES.items():
        try:
            result[name] = _fetch_fred_series(fred, series_id, target_date, FRED_LOOKBACK_OBS)
        except Exception as exc:
            result[name] = None
            errors.append(f"[macro:{name}] {exc}")
    return {"data": result, "errors": errors}


# ---------------------------------------------------------------------------
# 4. DART 주요 공시
# ---------------------------------------------------------------------------

def _fetch_top_market_cap_codes(target_date: dt.date, top_n: int = DART_UNIVERSE_SIZE) -> dict[str, str]:
    """시가총액 상위 top_n개 종목의 {코드: 코드}를 반환한다.

    DART 공시 조회 대상을 초대형주 몇 종목에서 벗어나 폭넓게 확장하기 위한 용도라
    표시용 종목명은 필요 없다(DART 응답 자체의 corp_name을 그대로 쓴다). 이 조회가
    실패해도(예: KRX 로그인 필요, 일시 오류) 빈 dict를 반환해 호출부가 기존 MAJOR_STOCKS
    만으로 안전하게 계속 진행하게 한다 - DART 대상 확장은 부가 기능이지 필수 경로가 아니다.
    """
    # get_market_trading_value_by_date와 마찬가지로 이 엔드포인트도 KRX 로그인 세션을
    # 요구한다(비로그인 시 즉시 실패가 확정적이므로 불필요한 재시도를 생략한다).
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        return {}

    date_str = target_date.strftime("%Y%m%d")

    def _fetch() -> pd.DataFrame:
        return krx.get_market_cap_by_ticker(date_str, market="ALL")

    try:
        df = _retry_with_backoff(_fetch, context="market_cap")
    except Exception:
        return {}
    if df is None or df.empty or "시가총액" not in df.columns:
        return {}

    # 우선주는 KRX 관행상 보통주 코드의 마지막 자리만 다르며(예: 005930 -> 005935),
    # DART는 법인 단위로만 공시를 관리해 우선주 자체의 고유 조회 코드가 없는 경우가 많다.
    # 시가총액 상위권에 우선주가 섞여 있으면 대부분 그 본주도 이미 상위권에 있으므로,
    # DART 조회 대상 선정 단계에서부터 우선주로 추정되는 코드는 제외해 불필요한 조회
    # 실패를 원천 차단한다(코드 자체는 마지막 자리 "0"인 것을 보통주로 간주).
    df = df[df.index.to_series().str.fullmatch(r"\d{6}0")]
    if df.empty:
        return {}
    top = df.sort_values("시가총액", ascending=False).head(top_n)
    return {str(code): str(code) for code in top.index}


def _fetch_dart_disclosures(dart: "OpenDartReader", code: str, name: str, target_date: dt.date) -> list[dict]:
    start = (target_date - dt.timedelta(days=DART_LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = target_date.strftime("%Y%m%d")
    try:
        df = dart.list(code, start=start, end=end)
    except Exception:
        # 우선주/스팩/신주인수권 등은 DART 법인 코드가 보통주와 다르거나 아예 없어 조회가
        # 실패할 수 있다. KRX 관행상 우선주 코드는 보통주 코드의 마지막 자리만 다른 경우가
        # 많으므로, 마지막 자리를 "0"으로 바꾼 본주 후보 코드로 한 번 더 시도한다.
        fallback_code = code[:-1] + "0" if len(code) == 6 and code[-1] != "0" else None
        if not fallback_code or fallback_code == code:
            raise
        df = dart.list(fallback_code, start=start, end=end)
    if df is None or df.empty:
        return []
    df = df.sort_values("rcept_dt", ascending=False).head(DART_DISCLOSURES_PER_STOCK)
    return [
        {
            "corp_name": row.get("corp_name", name),
            "report_nm": row.get("report_nm"),
            "rcept_dt": row.get("rcept_dt"),
            "rcept_no": row.get("rcept_no"),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.get('rcept_no')}",
        }
        for _, row in df.iterrows()
    ]


def collect_dart_disclosures(target_date: dt.date, stocks: Optional[dict] = None) -> dict:
    if stocks is None:
        # 기본값: 고정 유니버스(MAJOR_STOCKS, 이름 보존) + 시가총액 상위 종목(코드만, 이름 중복 방지)
        stocks = dict(MAJOR_STOCKS)
        for code, name in _fetch_top_market_cap_codes(target_date).items():
            stocks.setdefault(code, name)

    api_key = os.getenv("DART_API_KEY")
    if not api_key or OpenDartReader is None:
        return {
            "data": {},
            "errors": ["DART_API_KEY 환경 변수가 없거나 OpenDartReader 패키지가 설치되지 않았습니다."],
        }

    # OpenDartReader 생성 시 전체 법인코드 목록을 내려받아 당일 캐시에 저장하므로, 스레드마다
    # 새로 생성하면 캐시 파일에 동시 쓰기 경쟁이 생길 수 있다. 여기서 한 번만 만들고
    # 이후 조회(.list())만 여러 스레드가 공유해서 병렬로 사용한다.
    dart = OpenDartReader(api_key)
    result: dict = {}
    with ThreadPoolExecutor(max_workers=DART_MAX_WORKERS) as executor:
        future_to_code = {
            executor.submit(_fetch_dart_disclosures, dart, code, name, target_date): code
            for code, name in stocks.items()
        }
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                result[code] = future.result()
            except Exception as exc:
                # 개별 종목의 DART 조회 실패(우선주/스팩 등 법인코드 불일치, 일시 오류 등)는
                # 흔하고 무해한 상황이라 사용자 화면(source_data_errors)에는 절대 노출하지
                # 않는다. 서버 로그에만 남겨 운영자가 필요 시 확인할 수 있게 한다.
                result[code] = []
                logger.warning("[dart:%s] 공시 조회 실패(사용자 화면에는 노출하지 않음): %s", code, exc)
    return {"data": result, "errors": []}


# ---------------------------------------------------------------------------
# 5. 구글 뉴스 RSS 팩트 뉴스
# ---------------------------------------------------------------------------

def _fetch_google_news(query: str, limit: int) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:limit]:
        source = entry.get("source")
        items.append(
            {
                "title": entry.get("title"),
                "link": entry.get("link"),
                "published": entry.get("published"),
                "source": source.get("title") if isinstance(source, dict) else source,
            }
        )
    return items


def collect_news_data(queries: Optional[list[str]] = None) -> dict:
    queries = queries or NEWS_RSS_QUERIES
    result: dict = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=NEWS_MAX_WORKERS) as executor:
        future_to_query = {executor.submit(_fetch_google_news, q, NEWS_ITEMS_PER_QUERY): q for q in queries}
        for future in as_completed(future_to_query):
            q = future_to_query[future]
            try:
                result[q] = future.result()
            except Exception as exc:
                result[q] = []
                errors.append(f"[news:{q}] {exc}")
    return {"data": result, "errors": errors}


# ---------------------------------------------------------------------------
# 종합 수집 엔트리포인트
# ---------------------------------------------------------------------------

def _run_section(section_name: str, fn, *args, **kwargs) -> dict:
    """수집 섹션 하나를 실행한다.

    각 collect_* 함수는 이미 항목 단위(종목별/지표별)로 예외를 잡아 errors에
    기록하지만, 혹시 그 방어망을 뚫고 섹션 함수 자체가 예상치 못한 예외를
    던지더라도(예: pykrx/외부 라이브러리의 예상 밖 동작) 리포트 생성 전체가
    죽지 않도록 한 겹 더 격리한다.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"data": {}, "errors": [f"[section:{section_name}] {exc}"]}


KRX_MARKET_OPEN = dt.time(9, 0)
KRX_MARKET_CLOSE = dt.time(15, 30)


def _is_krx_market_hours_now() -> bool:
    """지금(KST)이 KRX 정규장 시간(평일 09:00~15:30) 안인지 판단한다.

    공휴일 캘린더까지는 반영하지 않는 단순화된 판단이다(평일이지만 임시 휴장일이면
    "장중"으로 오판할 수 있음) - 다만 이 정도로도 실제 문제였던 "장중인데 장마감으로 표시"
    케이스는 정확히 해결된다.
    """
    now = _now_kst()
    if now.weekday() >= 5:  # 5=토, 6=일
        return False
    return KRX_MARKET_OPEN <= now.time() <= KRX_MARKET_CLOSE


def _determine_data_freshness(resolved_date: dt.date) -> dict:
    """조회 시점(KST)이 KRX 정규장 시간 안이면 "장중 실시간", 아니면 "장마감"으로 판정한다.

    이전에는 "지수 데이터에 오늘 날짜가 붙어 있는지"로 판정했으나, yfinance는 장중에도
    당일 날짜가 붙은 진행 중인 봉을 돌려주기 때문에 그 방식으로는 장중을 장마감으로
    오판하는 문제가 있었다. 조회 시각의 KST 벽시계 기준으로 직접 판단하는 편이 사용자가
    실제로 원하는 "지금 이 순간 장이 열려 있는가"에 훨씬 정확히 대응한다. 조회일이
    오늘이 아니면(과거 날짜 조회) 항상 확정된 장마감 데이터다.
    """
    if resolved_date != _today_kst():
        return {"status": "market_closed", "label": "장마감 데이터 분석", "asof_time": None}

    if _is_krx_market_hours_now():
        return {
            "status": "intraday",
            "label": "장중 실시간 분석",
            "asof_time": _now_kst().strftime("%H:%M"),
        }
    return {"status": "market_closed", "label": "장마감 데이터 분석", "asof_time": None}


def collect_market_data(target_date: DateLike = None) -> dict:
    """지정일 기준 종합 시장 데이터를 수집해 JSON 직렬화 가능한 dict로 반환한다.

    Args:
        target_date: "YYYY-MM-DD" 문자열, datetime.date/datetime, 또는 None(기본값=오늘).

    Returns:
        indices, technical(domestic/us), macro, dart_disclosures, news 섹션과
        수집 중 발생한 부분 오류 목록(errors)을 담은 dict.
        개별 소스 실패는 전체 수집을 중단시키지 않고 해당 항목만 None/빈 값으로 남는다.
    """
    resolved_date = _to_date(target_date)

    # 6개 섹션은 서로 데이터 의존성이 없으므로(indices/technical_domestic/technical_us/
    # macro/dart/news) 순차 실행 대신 동시에 실행해 전체 소요 시간을 단축한다. 각 섹션은
    # 이미 내부적으로도 병렬화되어 있고 _run_section이 예외를 흡수하므로, 여기서 하나가
    # 오래 걸리거나 실패해도 다른 섹션에는 영향이 없다.
    section_jobs = {
        "indices": (collect_index_and_flow_data, (resolved_date,)),
        "technical_domestic": (collect_technical_data, (resolved_date,)),
        "technical_us": (collect_us_technical_data, (resolved_date,)),
        "macro": (collect_macro_data, (resolved_date,)),
        "dart": (collect_dart_disclosures, (resolved_date,)),
        "news": (collect_news_data, ()),
    }
    with ThreadPoolExecutor(max_workers=len(section_jobs)) as executor:
        future_to_section = {
            executor.submit(_run_section, name, fn, *args): name for name, (fn, args) in section_jobs.items()
        }
        sections = {future_to_section[future]: future.result() for future in as_completed(future_to_section)}

    index_flow = sections["indices"]
    technical_domestic = sections["technical_domestic"]
    technical_us = sections["technical_us"]
    macro = sections["macro"]
    dart_disclosures = sections["dart"]
    news = sections["news"]

    errors = (
        index_flow["errors"]
        + technical_domestic["errors"]
        + technical_us["errors"]
        + macro["errors"]
        + dart_disclosures["errors"]
        + news["errors"]
    )

    freshness = _determine_data_freshness(resolved_date)

    return {
        "meta": {
            "target_date": resolved_date.isoformat(),
            "generated_at": _now_kst().isoformat(timespec="seconds"),
            "data_freshness": freshness["status"],
            "data_freshness_label": freshness["label"],
            "data_asof_time": freshness["asof_time"],
        },
        "indices": index_flow["data"],
        "technical": {
            "domestic": technical_domestic["data"],
            "us": technical_us["data"],
        },
        "macro": macro["data"],
        "dart_disclosures": dart_disclosures["data"],
        "news": news["data"],
        "errors": errors,
    }


if __name__ == "__main__":
    import json

    result = collect_market_data()
    print(json.dumps(result, ensure_ascii=False, indent=2))

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
from typing import Any, Callable, Optional, TypeVar, Union
from urllib.parse import quote

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

NEWS_RSS_QUERIES: list[str] = ["코스피", "미국 증시", "금리"]

MA_WINDOWS = (5, 20, 60, 120)
OHLCV_LOOKBACK_DAYS = 120
DART_LOOKBACK_DAYS = 7
DART_UNIVERSE_SIZE = 20  # DART 공시를 조회할 시가총액 상위 종목 수
DART_DISCLOSURES_PER_STOCK = 3  # 종목당 프롬프트에 넘길 최근 공시 개수(유니버스 확대에 따른 과다 방지)
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

def _to_date(target_date: DateLike) -> dt.date:
    if target_date is None:
        return dt.date.today()
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
        "date": hist.index[-1].date().isoformat(),
        "close": last_close,
        "change": _safe_num(change),
        "change_pct": _safe_num(change_pct),
        "volume": _safe_int(last.get("Volume")),
        "price_history": price_history,
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
        raise ValueError(f"{market}: {target_date} 기준 최근 수급 데이터를 찾을 수 없습니다.")

    institution_col = next((c for c in df.columns if "기관" in c), None)
    foreign_col = next((c for c in df.columns if c.startswith("외국인")), None)

    df = df.sort_index().tail(SUPPLY_DEMAND_HISTORY_DAYS)
    history = [
        {
            "date": idx.date().isoformat(),
            "institution_net_buy": _safe_int(row.get(institution_col)) if institution_col else None,
            "foreign_net_buy": _safe_int(row.get(foreign_col)) if foreign_col else None,
        }
        for idx, row in df.iterrows()
    ]
    latest = history[-1]
    return {
        "date": latest["date"],
        "institution_net_buy": latest["institution_net_buy"],
        "foreign_net_buy": latest["foreign_net_buy"],
        "history": history,
    }


def collect_index_and_flow_data(target_date: dt.date) -> dict:
    result: dict = {}
    errors: list[str] = []

    for idx, (name, ticker) in enumerate(GLOBAL_INDEX_TICKERS.items()):
        if idx > 0:
            time.sleep(REQUEST_DELAY_SEC)
        try:
            result[name] = _fetch_yf_index_snapshot(ticker, target_date)
        except Exception as exc:
            result[name] = None
            errors.append(f"[index:{name}] {exc}")

    for idx, (name, market) in enumerate(KRX_SUPPLY_DEMAND_MARKETS.items()):
        if idx > 0:
            time.sleep(REQUEST_DELAY_SEC)
        if result.get(name) is None:
            continue
        try:
            result[name]["supply_demand"] = _fetch_krx_supply_demand(market, target_date)
        except Exception as exc:
            result[name]["supply_demand"] = None
            errors.append(f"[flow:{name}] {exc}")

    return {"data": result, "errors": errors}


# ---------------------------------------------------------------------------
# 2. 차트용 기술적 지표 (OHLCV, 이동평균, 피봇/지지·저항)
# ---------------------------------------------------------------------------

def _compute_pivot_levels(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    high, low, close = _safe_num(last["고가"]), _safe_num(last["저가"]), _safe_num(last["종가"])
    if high is None or low is None or close is None:
        return {"pivot": None, "resistance_1": None, "resistance_2": None, "support_1": None, "support_2": None}

    pivot = (high + low + close) / 3
    r1, s1 = 2 * pivot - low, 2 * pivot - high
    r2, s2 = pivot + (high - low), pivot - (high - low)
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


def _build_technical_payload(df: pd.DataFrame, name: str) -> dict:
    """시가/고가/저가/종가/거래량(한글 컬럼) DataFrame으로부터 차트용 페이로드를 만든다."""
    df = df.tail(OHLCV_LOOKBACK_DAYS).copy()

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

    return {
        "name": name,
        "dates": dates,
        "open": [_safe_num(v) for v in df["시가"]],
        "high": [_safe_num(v) for v in df["고가"]],
        "low": [_safe_num(v) for v in df["저가"]],
        "close": [_safe_num(v) for v in df["종가"]],
        "volume": [_safe_int(v) for v in df["거래량"]],
        **{f"ma{w}": [_safe_num(v) for v in df[f"MA{w}"]] for w in MA_WINDOWS},
        "pivot_point": _compute_pivot_levels(df),
        "support_resistance": _compute_support_resistance(df),
    }


def _fetch_stock_technical(code: str, name: str, target_date: dt.date) -> dict:
    end_str = target_date.strftime("%Y%m%d")
    # 영업일 기준 120일을 확보하기 위해 달력일 기준 넉넉히 조회 후 tail로 자른다.
    start_str = (target_date - dt.timedelta(days=int(OHLCV_LOOKBACK_DAYS * 2.2))).strftime("%Y%m%d")

    def _fetch() -> pd.DataFrame:
        return krx.get_market_ohlcv_by_date(start_str, end_str, code)

    df = _retry_with_backoff(_fetch, context=f"technical:{code}")
    if df is None or df.empty:
        raise ValueError(f"{code}({name}): OHLCV 데이터를 찾을 수 없습니다.")

    return _build_technical_payload(df, name)


def _fetch_us_stock_technical(ticker: str, name: str, target_date: dt.date) -> dict:
    start = target_date - dt.timedelta(days=int(OHLCV_LOOKBACK_DAYS * 2.2))
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


def collect_technical_data(target_date: dt.date, stocks: Optional[dict] = None) -> dict:
    """국내(KRX) 종목의 기술적 지표를 수집한다."""
    stocks = stocks or MAJOR_STOCKS
    result: dict = {}
    errors: list[str] = []
    for idx, (code, name) in enumerate(stocks.items()):
        if idx > 0:
            time.sleep(REQUEST_DELAY_SEC)
        try:
            result[code] = _fetch_stock_technical(code, name, target_date)
        except Exception as exc:
            result[code] = None
            errors.append(f"[technical:{code}] {exc}")
    return {"data": result, "errors": errors}


def collect_us_technical_data(target_date: dt.date, stocks: Optional[dict] = None) -> dict:
    """미국 종목의 기술적 지표를 수집한다."""
    stocks = stocks or US_STOCKS
    result: dict = {}
    errors: list[str] = []
    for idx, (ticker, name) in enumerate(stocks.items()):
        if idx > 0:
            time.sleep(REQUEST_DELAY_SEC)
        try:
            result[ticker] = _fetch_us_stock_technical(ticker, name, target_date)
        except Exception as exc:
            result[ticker] = None
            errors.append(f"[technical_us:{ticker}] {exc}")
    return {"data": result, "errors": errors}


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

    dart = OpenDartReader(api_key)
    result: dict = {}
    for code, name in stocks.items():
        try:
            result[code] = _fetch_dart_disclosures(dart, code, name, target_date)
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
    for q in queries:
        try:
            result[q] = _fetch_google_news(q, NEWS_ITEMS_PER_QUERY)
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

    index_flow = _run_section("indices", collect_index_and_flow_data, resolved_date)
    technical_domestic = _run_section("technical_domestic", collect_technical_data, resolved_date)
    technical_us = _run_section("technical_us", collect_us_technical_data, resolved_date)
    macro = _run_section("macro", collect_macro_data, resolved_date)
    dart_disclosures = _run_section("dart", collect_dart_disclosures, resolved_date)
    news = _run_section("news", collect_news_data)

    errors = (
        index_flow["errors"]
        + technical_domestic["errors"]
        + technical_us["errors"]
        + macro["errors"]
        + dart_disclosures["errors"]
        + news["errors"]
    )

    return {
        "meta": {
            "target_date": resolved_date.isoformat(),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
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

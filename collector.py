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
import os
from typing import Any, Optional, Union
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

# 기술적 지표(OHLCV/이평선/지지·저항)를 수집할 국내 주요 종목 (KRX 종목코드 -> 종목명)
MAJOR_STOCKS: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "005380": "현대차",
    "051910": "LG화학",
}

# 기술적 지표를 수집할 미국 주요 종목 (티커 -> 종목명)
US_STOCKS: dict[str, str] = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "GOOGL": "Alphabet",
    "TSLA": "Tesla",
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
NEWS_ITEMS_PER_QUERY = 5
FRED_LOOKBACK_OBS = 24

DateLike = Union[str, dt.date, dt.datetime, None]


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


def _safe_num(value: Any, ndigits: int = 2) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), ndigits)


def _safe_int(value: Any) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    return int(value)


# ---------------------------------------------------------------------------
# 1. 국내/미국 지수 및 수급
# ---------------------------------------------------------------------------

def _fetch_yf_index_snapshot(ticker: str, target_date: dt.date) -> dict:
    start = target_date - dt.timedelta(days=20)
    end = target_date + dt.timedelta(days=1)
    hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), interval="1d")
    if hist.empty:
        raise ValueError(f"{ticker}: 조회 구간에 데이터가 없습니다.")
    hist = hist[hist.index.date <= target_date]
    if hist.empty:
        raise ValueError(f"{ticker}: {target_date} 이전 거래 데이터가 없습니다.")

    last = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) >= 2 else None
    change = float(last["Close"] - prev["Close"]) if prev is not None else None
    change_pct = (
        change / float(prev["Close"]) * 100 if prev is not None and prev["Close"] else None
    )
    return {
        "date": hist.index[-1].date().isoformat(),
        "close": _safe_num(last["Close"]),
        "change": _safe_num(change),
        "change_pct": _safe_num(change_pct),
        "volume": _safe_int(last.get("Volume")),
    }


def _fetch_krx_supply_demand(market: str, target_date: dt.date, max_lookback: int = 10) -> dict:
    """target_date로부터 최대 max_lookback일 역순으로 최근 거래일 수급 데이터를 찾는다."""
    for offset in range(max_lookback):
        d = target_date - dt.timedelta(days=offset)
        date_str = d.strftime("%Y%m%d")
        try:
            df = krx.get_market_trading_value_by_date(date_str, date_str, market)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        row = df.iloc[-1]
        institution_col = next((c for c in df.columns if "기관" in c), None)
        foreign_col = next((c for c in df.columns if c.startswith("외국인")), None)
        return {
            "date": d.isoformat(),
            "institution_net_buy": _safe_int(row.get(institution_col)) if institution_col else None,
            "foreign_net_buy": _safe_int(row.get(foreign_col)) if foreign_col else None,
        }
    raise ValueError(f"{market}: {target_date} 기준 수급 데이터를 찾을 수 없습니다.")


def collect_index_and_flow_data(target_date: dt.date) -> dict:
    result: dict = {}
    errors: list[str] = []

    for name, ticker in GLOBAL_INDEX_TICKERS.items():
        try:
            result[name] = _fetch_yf_index_snapshot(ticker, target_date)
        except Exception as exc:
            result[name] = None
            errors.append(f"[index:{name}] {exc}")

    for name, market in KRX_SUPPLY_DEMAND_MARKETS.items():
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
    high, low, close = float(last["고가"]), float(last["저가"]), float(last["종가"])
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

    df = krx.get_market_ohlcv_by_date(start_str, end_str, code)
    if df is None or df.empty:
        raise ValueError(f"{code}({name}): OHLCV 데이터를 찾을 수 없습니다.")

    return _build_technical_payload(df, name)


def _fetch_us_stock_technical(ticker: str, name: str, target_date: dt.date) -> dict:
    start = target_date - dt.timedelta(days=int(OHLCV_LOOKBACK_DAYS * 2.2))
    end = target_date + dt.timedelta(days=1)

    hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), interval="1d")
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
    for code, name in stocks.items():
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
    for ticker, name in stocks.items():
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

def _fetch_dart_disclosures(dart: "OpenDartReader", code: str, name: str, target_date: dt.date) -> list[dict]:
    start = (target_date - dt.timedelta(days=DART_LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = target_date.strftime("%Y%m%d")
    df = dart.list(code, start=start, end=end)
    if df is None or df.empty:
        return []
    df = df.sort_values("rcept_dt", ascending=False).head(5)
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
    stocks = stocks or MAJOR_STOCKS
    api_key = os.getenv("DART_API_KEY")
    if not api_key or OpenDartReader is None:
        return {
            "data": {},
            "errors": ["DART_API_KEY 환경 변수가 없거나 OpenDartReader 패키지가 설치되지 않았습니다."],
        }

    dart = OpenDartReader(api_key)
    result: dict = {}
    errors: list[str] = []
    for code, name in stocks.items():
        try:
            result[code] = _fetch_dart_disclosures(dart, code, name, target_date)
        except Exception as exc:
            result[code] = []
            errors.append(f"[dart:{code}] {exc}")
    return {"data": result, "errors": errors}


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

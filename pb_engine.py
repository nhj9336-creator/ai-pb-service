"""수집된 시장 데이터를 바탕으로 Senior PB 수준의 종합 리포트를 생성하는 엔진.

collector.collect_market_data()의 결과를 압축된 컨텍스트로 가공해 LLM(OpenAI 또는
Gemini)에 전달하고, 엄격한 JSON 스키마로 응답을 받아 pb_report_latest.json에
저장한다.

필요 환경 변수 (.env 또는 시스템 환경 변수):
    AI_PROVIDER     - "openai" | "gemini" (생략 시 설정된 API 키로 자동 판단)
    OPENAI_API_KEY  - OpenAI API 키
    OPENAI_MODEL    - 기본값 "gpt-4o-mini"
    GEMINI_API_KEY  - Gemini API 키 (GOOGLE_API_KEY도 허용)
    GEMINI_MODEL    - 기본값 "gemini-3.6-flash"
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from collector import (
    DateLike,
    MAJOR_STOCKS,
    US_STOCKS,
    _fetch_stock_technical,
    _fetch_us_stock_technical,
    _now_kst,
    _to_date,
    collect_market_data,
    resolve_domestic_ticker,
)

load_dotenv()

OUTPUT_PATH_DEFAULT = "pb_report_latest.json"
MAX_GENERATION_ATTEMPTS = 2
LLM_TIMEOUT_SECONDS = 110  # LLM 응답이 이보다 오래 걸리면 실패로 간주하고 재시도/에러 처리한다.
# (기존 90초에서 상향: market_overview에 요구사항이 늘며 Task A 단일 호출이 90초를 넘겨
# "504 Deadline expired" 오류가 발생한 사례가 있었다. Task A를 A/A2로 쪼개 태스크당 응답
# 시간 자체도 줄였지만, 여유를 더 두기 위해 타임아웃도 함께 늘렸다.)

# 추천 종목 개수는 collector의 종목 유니버스 크기와 항상 일치시킨다(유니버스 전체가
# 분석·랭킹되어 프론트엔드 "더보기"로 전부 확인 가능하도록).
DOMESTIC_RECOMMENDATION_COUNT = len(MAJOR_STOCKS)
US_RECOMMENDATION_COUNT = len(US_STOCKS)
NEWS_IMPACT_COUNT = 10  # 프론트엔드 "뉴스 더보기"에서 전부 노출할 주요 뉴스 분석 개수

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# ---------------------------------------------------------------------------
# 리포트 JSON 스키마 - 5개 독립 태스크로 분할한다(asyncio.gather로 동시 호출).
#   A) 시장 총평   A2) 금융상품 추천 + 자산배분 전략   B) 국내 추천 종목   C) 미국 추천 종목
#   D) 뉴스 파급력 분석
# 각 태스크는 서로 다른 LLM 응답으로, 병렬 호출 후 하나의 리포트로 병합된다.
# (원래 A/A2는 하나의 태스크였으나, market_overview에 Option A/B 계좌 시나리오 등 요구사항이
# 늘면서 단일 호출 응답 시간이 길어져 Gemini의 LLM_TIMEOUT_SECONDS를 초과하는 504
# "Deadline expired" 오류가 발생했다 - 태스크당 요구 출력량을 줄여 응답 시간을 단축하기 위해
# 자산배분/금융상품 부분을 별도 태스크(A2)로 분리했다.)
# DART 공시는 collector가 제공하는 제목/날짜 메타데이터뿐이라(본문 전문이 없음) AI가
# "분석"하면 근거 없는 해석을 지어낼 위험이 있어, 의도적으로 AI 태스크에 포함하지 않고
# 프론트엔드에 원본 사실 그대로("더보기" 목록) 노출한다.
# ---------------------------------------------------------------------------

DISCLAIMER_TEXT = "본 리포트는 참고용 정보이며, 투자 판단과 그 결과에 대한 책임은 투자자 본인에게 있습니다."

VALID_STRATEGY_OPINIONS = {"매수", "관망", "비중축소"}
VALID_SUPPLY_DEMAND_STATUS = {"매집", "이탈", "혼조", "데이터없음"}
VALID_ENTRY_SIGNALS = {"진입유효", "눌림목대기", "고점매수주의", "진입보류"}

TASK_A_SCHEMA = {
    "market_overview": {
        "summary": "국내외 지수, 수급, 거시지표를 종합한 3~4문장 시장 흐름 분석. 결론(헤드라인)을 먼저 던지고 근거 수치로 뒷받침하는 브리핑 톤으로 작성(장황한 배경 설명 없이 핵심만)",
        "pb_strategy_opinion": "매수 | 관망 | 비중축소 중 하나",
        "strategy_rationale": "위 전략 의견을 제시한 근거 2문장",
        "supply_demand_status": "매집 | 이탈 | 혼조 | 데이터없음 중 하나 (기관/외국인/개인 수급 데이터가 없으면 데이터없음)",
        "supply_demand_analysis": "국내 증시 수급 심층 분석 3~4문장. institution_net_buy/foreign_net_buy/individual_net_buy가 있으면 recent_days 시계열(날짜별 종가+기관/외국인/개인 순매수)을 대조해 (1)기관·외국인·개인 3주체의 매매 방향이 서로 같은지/엇갈리는지(예: 외국인·기관 매수 vs 개인 매도의 손바뀜 구도), (2)순매수가 집중된 종가 구간(매집 구간)과 순매도가 집중된 구간(이탈 구간)을 실제 가격 수치로 제시할 것. institution_net_buy/foreign_net_buy/individual_net_buy가 모두 null이면(데이터 미제공) 이를 명시하고 change_pct·volume 기반 장중 모멘텀 해석으로 대체할 것",
        "intraday_playbook": "장중 실시간 대응 시나리오 2문장. '상승 돌파 시(구체적 가격 이상)' 대응과 '지지선 이탈 시(구체적 가격 이하)' 손절/비중조절 기준을 각각 실제 수치로 명시할 것",
        "account_scenario_bullish": "Option A - 지수가 주요 저항선을 안착 돌파했을 때의 계좌 전체 대응 전략 2문장. 종목별 타점이 아니라 '주식 비중을 얼마나 늘릴지'와 '어떤 업종/섹터가 주도할지'를 실제 지수 가격 기준과 함께 제시",
        "account_scenario_bearish": "Option B - 지수가 주요 지지선을 이탈했을 때의 계좌 전체 대응 전략 2문장. 종목별 타점이 아니라 '현금 비중을 얼마나 늘리고 어떤 기준으로 관망할지'를 실제 지수 가격 기준과 함께 제시",
    },
}

TASK_A2_SCHEMA = {
    "financial_products": [
        {
            "type": "섹터ETF | 채권형 | MMF | 리츠 | 기타",
            "name": "상품/자산군 이름",
            "description": "상품 개요",
            "allocation_reason": "현재 시장 상황에서 이 상품을 추천하는 이유 및 자산배분 전략",
        }
    ],
    "portfolio_allocation": {
        "assets": [
            {
                "name": "국내주식",
                "percent": 30,
                "representative_instruments": "해당 자산군의 구체적 대표 종목/ETF 실명(예: KODEX 200(069500), 삼성전자(005930))과 비중 조절 가이드 1문장",
            },
            {"name": "미국주식", "percent": 25, "representative_instruments": "예: TIGER 미국S&P500(360750) 또는 SPY, QQQ 등 실제 상품명"},
            {"name": "채권/MMF", "percent": 20, "representative_instruments": "예: TLT, KODEX 국고채3년, CMA/MMF 상품명"},
            {"name": "리츠/대체투자", "percent": 10, "representative_instruments": "예: 국내외 리츠 ETF 실명"},
            {"name": "현금성자산", "percent": 15, "representative_instruments": "파킹형 상품 또는 단기 CMA 등"},
        ],
        "rebalancing_strategy": "VIP 고객 1:1 브리핑 톤의 리밸런싱 전략 2~3문장. 단순 비율 나열이 아니라 '왜 지금 이 시점에' 이 비중으로 조정해야 하는지를 매크로 지표(금리·환율 등)와 증시 수급 흐름 등 객관적 팩트에 근거해 구체적으로 설명할 것",
    },
}


def _stock_item_schema(ticker_example: str) -> dict:
    return {
        "name": "종목명",
        "ticker": f"종목코드/티커(예: {ticker_example})",
        "reason": "추천 이유",
        "buy_point": "매수 관전 포인트(최소 3~4문장의 상세 PB 대응 노트)",
        "entry_price_low": "권장 진입 범위 하단(숫자). 판단 불가 시 null",
        "entry_price_high": "권장 진입 범위 상단(숫자). 판단 불가 시 null",
        "entry_signal": "진입유효 | 눌림목대기 | 고점매수주의 | 진입보류 중 하나",
        "entry_signal_reason": "진입 시그널 판정 근거 1문장(시장 총평 스탠스와의 정합성 포함)",
        "breakout_price": "상승 돌파 시 대응 기준가(숫자). 판단 불가 시 null",
        "stop_loss_price": "지지선 이탈 시 손절 기준가(숫자). 판단 불가 시 null",
        "risk": "투자 리스크",
    }


TASK_B_SCHEMA = {"domestic": [_stock_item_schema("005930")]}
TASK_C_SCHEMA = {"us": [_stock_item_schema("AAPL")]}
TASK_D_SCHEMA = {
    "news_impact_analysis": [
        {
            "headline": "실제 수집된 뉴스 제목 중 하나를 그대로 인용",
            "summary": "해당 뉴스의 핵심 내용 1~2문장 요약",
            "impact": "이 뉴스가 시장/섹터에 미치는 파급 효과 분석 2~3문장",
            "affected_sectors": ["영향받는 섹터/업종 목록"],
        }
    ]
}

# 보유 종목 맞춤 진단(부가 기능) - 메인 4-태스크 파이프라인과 독립적으로, 사용자가 요청할
# 때만 단발성으로 호출된다.
VALID_RISK_LEVELS = {"낮음", "보통", "높음"}


def _strategy_schema() -> dict:
    return {
        "action": "해당 투자 호흡에 맞는 구체적 대응(추가매수/손절/익절 가격을 실제 숫자로 포함) 2~3문장",
        "risk_level": "낮음 | 보통 | 높음 중 하나",
        "risk_reward_note": "손절폭 대비 목표 상승폭 등 위험 대비 기대수익을 정성적으로 설명(승률 등 지어낸 통계 수치 금지)",
    }


TASK_DIAGNOSIS_SCHEMA = {
    "profit_diagnosis": "평가손익(pnl_amount/pnl_pct)과 평단가가 기술적 지표 대비 어느 위치에 있는지 진단하는 3~4문장",
    "strategies": {
        "day_trading": _strategy_schema(),
        "swing": _strategy_schema(),
        "long_term": _strategy_schema(),
    },
    "market_consistency_note": "전체 시장 총평 스탠스와 이 진단이 어떻게 연결되는지 1~2문장",
}


# ---------------------------------------------------------------------------
# 1. 수집 데이터 -> 프롬프트용 압축 컨텍스트
# ---------------------------------------------------------------------------

def _describe_trend(close: Optional[float], mas: dict[str, Optional[float]]) -> str:
    values = [close, mas.get("ma5"), mas.get("ma20"), mas.get("ma60"), mas.get("ma120")]
    if any(v is None for v in values):
        return "판단불가(데이터 부족)"
    if values == sorted(values, reverse=True):
        return "상승추세(정배열)"
    if values == sorted(values):
        return "하락추세(역배열)"
    return "혼조/박스권"


def _condense_indices(indices: dict) -> dict:
    condensed = {}
    for name, snap in (indices or {}).items():
        if not snap:
            condensed[name] = None
            continue
        sd = snap.get("supply_demand")
        sd_history_by_date = {h["date"]: h for h in (sd.get("history") or [])} if sd else {}

        # 최근 거래일의 종가와 기관/외국인/개인 순매수를 날짜별로 대조한 시계열.
        # institution_net_buy/foreign_net_buy/individual_net_buy가 전부 null이면 수급 데이터
        # 자체가 없는 상태(KRX 로그인 미설정 등)이며, 이 경우 프롬프트에서 거래량·등락률 기반
        # 모멘텀 분석으로 대체하도록 안내한다. 데이터가 있으면 종가 대비 순매수 흐름을 대조해
        # 수급 주체별 매집/이탈 구간을 판단하는 데 사용한다.
        recent_days = [
            {
                "date": p["date"],
                "close": p.get("close"),
                "institution_net_buy": sd_history_by_date.get(p["date"], {}).get("institution_net_buy"),
                "foreign_net_buy": sd_history_by_date.get(p["date"], {}).get("foreign_net_buy"),
                "individual_net_buy": sd_history_by_date.get(p["date"], {}).get("individual_net_buy"),
            }
            for p in (snap.get("price_history") or [])
        ]

        condensed[name] = {
            "date": snap.get("date"),
            "close": snap.get("close"),
            "change_pct": snap.get("change_pct"),
            "volume": snap.get("volume"),
            "supply_demand_date": sd.get("date") if sd else None,
            "institution_net_buy": sd.get("institution_net_buy") if sd else None,
            "foreign_net_buy": sd.get("foreign_net_buy") if sd else None,
            "individual_net_buy": sd.get("individual_net_buy") if sd else None,
            "recent_days": recent_days,
        }
    return condensed


def _condense_volume_momentum(volumes: list) -> dict:
    """최근 거래량을 최근 20거래일 평균과 비교해 돌파/이탈의 신뢰도(거래량 실림 여부)를 판단할
    근거를 만든다."""
    clean = [v for v in (volumes or []) if v is not None]
    if not clean:
        return {"latest_volume": None, "avg_volume_20d": None, "volume_ratio": None}
    latest = clean[-1]
    window = clean[-20:]
    avg20 = sum(window) / len(window) if window else None
    ratio = round(latest / avg20, 2) if avg20 else None
    return {"latest_volume": latest, "avg_volume_20d": round(avg20) if avg20 else None, "volume_ratio": ratio}


def _condense_technical(technical: dict) -> dict:
    condensed = {}
    for code, t in (technical or {}).items():
        if not t:
            condensed[code] = None
            continue
        mas = {w: (t.get(f"ma{w}") or [None])[-1] for w in ("5", "20", "60", "120")}
        mas = {f"ma{k}": v for k, v in mas.items()}
        close = (t.get("close") or [None])[-1]
        condensed[code] = {
            "name": t.get("name"),
            "last_date": (t.get("dates") or [None])[-1],
            "close": close,
            # 장중에는 close가 전일 확정 종가일 수 있다 - current_price_is_realtime이 true면
            # current_price(실시간에 가까운 시세)를 우선 판단 기준으로 삼을 것.
            "current_price": t.get("current_price"),
            "current_price_is_realtime": t.get("current_price_is_realtime", False),
            "moving_averages": mas,
            "trend": _describe_trend(close, mas),
            "pivot_point": t.get("pivot_point"),
            "support_resistance": t.get("support_resistance"),
            "trend_channel": t.get("trend_channel"),
            "volume_momentum": _condense_volume_momentum(t.get("volume")),
        }
    return condensed


def _condense_macro(macro: dict) -> dict:
    condensed = {}
    for name, series in (macro or {}).items():
        if not series:
            condensed[name] = None
            continue
        latest = series[-1]
        prev = series[-2] if len(series) >= 2 else None
        condensed[name] = {
            "latest_date": latest.get("date"),
            "latest_value": latest.get("value"),
            "previous_value": prev.get("value") if prev else None,
        }
    return condensed


def _condense_dart(dart: dict, limit_per_stock: int = 3) -> dict:
    # 종목 유니버스가 넓어(최대 20개+) 대부분은 당일 공시가 없다 - 빈 배열까지 프롬프트에
    # 넣으면 토큰만 낭비하므로 실제 공시가 있는 종목만 남긴다.
    condensed = {}
    for code, items in (dart or {}).items():
        if not items:
            continue
        condensed[code] = [
            {"corp_name": i.get("corp_name"), "report_nm": i.get("report_nm"), "rcept_dt": i.get("rcept_dt")}
            for i in items[:limit_per_stock]
        ]
    return condensed


def _condense_news(news: dict, limit_per_query: int = 5) -> list[dict]:
    flattened = []
    for query, items in (news or {}).items():
        for i in (items or [])[:limit_per_query]:
            flattened.append({"query": query, "title": i.get("title"), "source": i.get("source")})
    return flattened


def build_prompt_context(market_data: dict) -> dict:
    """collect_market_data() 원본 결과를 LLM 프롬프트에 넣기 좋은 형태로 압축한다."""
    technical = market_data.get("technical") or {}
    meta = market_data.get("meta", {})
    return {
        "target_date": meta.get("target_date"),
        "analysis_timestamp": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        # "intraday"(장중, 당일 확정 종가 미게시) | "market_closed"(장마감, 확정 데이터)
        "data_freshness": meta.get("data_freshness"),
        "data_freshness_label": meta.get("data_freshness_label"),
        "indices": _condense_indices(market_data.get("indices")),
        "technical": {
            "domestic": _condense_technical(technical.get("domestic")),
            "us": _condense_technical(technical.get("us")),
        },
        "macro": _condense_macro(market_data.get("macro")),
        "dart_disclosures": _condense_dart(market_data.get("dart_disclosures")),
        "news": _condense_news(market_data.get("news")),
    }


# ---------------------------------------------------------------------------
# 2. 프롬프트 구성
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "당신은 국내 최상위 증권사 Private Banking 센터에서 20년째 UHNW(초고자산가) 고객을 "
    "전담해온 Senior PB입니다. 매 장중, 고객이 지금 이 순간의 시황을 5분 안에 파악하고 "
    "바로 의사결정을 내릴 수 있도록 리포트를 씁니다. 이 리포트는 불특정 다수를 위한 "
    "일반 뉴스 요약이 아니라, 실제 자산을 맡긴 고객 한 명에게 보내는 사적인 전략 노트입니다.\n\n"
    "문체 원칙:\n"
    "- 각 항목은 결론(헤드라인 한 문장)을 먼저 던지고, 그 근거가 되는 실제 수치를 바로 이어 "
    "붙여 대비시킬 것. 예: '외국인 수급이 뚜렷하게 살아나고 있습니다 - 최근 3거래일간 "
    "6,900원~7,100원 구간에서 순매수 1,200억원이 집중됐습니다' 같은 결론-근거 구조.\n"
    "- '~함', '~임', '~됨' 같은 개조식 나열체를 쓰지 말 것. 실제 사람이 구두로 브리핑하듯 "
    "자연스러운 존댓말 문장으로 쓰되, 군더더기 수식어나 '~할 수 있습니다', '~로 보여집니다' "
    "같은 흐릿한 헷지 표현을 남발하지 말고 단정적으로 판단할 것.\n"
    "- 모호한 표현('적절히', '유의미하게', '어느 정도') 대신 항상 구체적 수치(가격, %, 금액, "
    "거래량)로 말할 것. 숫자로 뒷받침되지 않는 주장은 쓰지 말 것.\n"
    "- 제공된 시장 데이터(지수, 수급, 기술적 지표, 거시경제, 공시, 뉴스)만을 근거로 분석하며, "
    "데이터에 없는 사실이나 가격을 지어내지 않습니다.\n\n"
    "객관성 원칙: 당신은 감정도, 직관적 추측도 배제한 정량 분석가입니다. "
    "'기대된다', '전망이 밝다', '분위기가 좋다' 같은 정성적·심리적 표현을 쓰지 말 것. "
    "모든 판단(추천/비추천, 매수/관망/비중축소, 돌파/이탈 대응)은 반드시 다음 중 하나 이상의 "
    "객관적 지표를 명시적으로 인용해 뒷받침할 것: (1) 이동평균선 정배열/역배열 여부, "
    "(2) Pivot 기반 지지선·저항선 수치, (3) 거래량 및 등락률, (4) 기관/외국인 순매수 수급 수치, "
    "(5) 금리·CPI 등 거시지표. 근거 지표를 특정하지 못하는 판단은 서술하지 말 것.\n\n"
    "반드시 요청된 JSON 스키마와 동일한 키 구조로만 응답하고, JSON 이외의 설명이나 "
    "마크다운 코드블록(```) 표시는 절대 포함하지 않습니다."
)


def _build_task_a_prompt(context: dict) -> str:
    """Task A: 시장 총평(수급 분석/장중 대응/계좌 시나리오). 응답 시간을 줄이기 위해
    자산배분·금융상품 추천은 Task A2로 분리했다(각 요구 문장 수도 소폭 압축)."""
    schema_str = json.dumps(TASK_A_SCHEMA, ensure_ascii=False, indent=2)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    freshness_label = context.get("data_freshness_label") or "시장 데이터"
    return f"""아래는 {context.get('target_date')} 기준, {context.get('analysis_timestamp')}({freshness_label})에 수집된 지수/수급/거시경제 데이터입니다.
data_freshness가 "intraday"이면 당일 공식 종가가 아직 확정되지 않은 장중 시점이라는 뜻이므로, 이 시각 이후 시세가 더 움직일 수 있다는 점을 summary 초반에 자연스럽게 밝힐 것("장중 실시간 기준"임을 명시). data_freshness가 "market_closed"이면 당일 확정 마감 데이터이므로 그렇게 서술할 것(장중이라고 지어내지 말 것).
응답은 각 필드에 명시된 문장 수를 넘기지 말고 핵심만 간결하게 작성할 것(응답 생성 시간 단축을 위함).

[시장 데이터]
{context_str}

[작성 요구사항]
1. 국내/미국 지수, 수급, 거시지표를 종합해 시장 흐름을 분석하고, PB로서 매수/관망/비중축소 중 하나의 전략 의견을 제시할 것.
   - indices의 국내 지수(KOSPI/KOSDAQ)에 institution_net_buy/foreign_net_buy/individual_net_buy 수치가 있으면, recent_days(날짜별 종가 + 기관/외국인/개인 순매수 시계열)를 직접 대조해 다음을 포함한 심층 수급 분석을 작성할 것(supply_demand_analysis):
     a) 기관·외국인·개인 3주체의 매매 방향이 최근 며칠간 서로 같은지, 엇갈리는지(예: 외국인·기관 동반 매수에 개인이 매도로 대응하는 손바뀜, 혹은 개인이 저가 매수에 나서고 외국인이 차익 실현하는 구도 등).
     b) recent_days에서 순매수(양수)가 몰린 날짜들의 종가 범위를 "매집 구간"으로, 순매도(음수)가 몰린 날짜들의 종가 범위를 "이탈 구간"으로 짚어낼 것 - 실제 수치에 근거해야 하며 데이터에 없는 가격대를 지어내지 말 것. 어느 주체가 그 구간을 주도했는지(기관/외국인/개인 중)도 함께 밝힐 것.
     c) 위에서 짚은 매집/이탈 구간을 근거로 장중 대응 지지선·저항선 및 대응전략(매수/관망/비중축소 중 어느 시점에 어떤 대응)을 구체적으로 제시할 것.
     이 내용을 바탕으로 supply_demand_status를 매집/이탈/혼조 중 하나로 판정할 것.
     supply_demand_date가 target_date보다 이전이면 "직전 영업일 기준" 데이터임을 밝힐 것.
   - institution_net_buy/foreign_net_buy/individual_net_buy가 모두 null이면(KRX 로그인 미설정 등으로 수급 데이터 자체가 없는 경우) supply_demand_status를 "데이터없음"으로 설정하고 이를 명시한 뒤, 대신 change_pct(등락률)와 volume(거래량)을 근거로 한 장중 모멘텀 분석으로 대체할 것.
   - intraday_playbook에는 "OO,OOO원 상향 돌파 시 추가 매수/비중 확대", "OO,OOO원 이탈 시 손절 또는 비중 축소"처럼 지수의 실제 가격 수치를 기준으로 한 이분법적 시나리오를 제시할 것.
2. account_scenario_bullish/account_scenario_bearish에는 종목별 타점이 아니라 "계좌 전체" 관점의 조건부 대응 시나리오를 둘로 나눠 제시할 것(거시적 포트폴리오 관점):
   - account_scenario_bullish (Option A, 상방 시나리오): KOSPI/KOSDAQ 또는 대표 지수가 pivot_point/trend_channel 등 데이터상의 주요 저항선을 안착 돌파했을 때, 계좌 내 주식 비중을 얼마나/어떻게 확대할지와 그 국면에서 수급·거래량상 주도력이 있는 섹터를 실제 수치에 근거해 제시할 것.
   - account_scenario_bearish (Option B, 하방 시나리오): 주요 지지선을 이탈했을 때, 리스크 관리를 위해 현금 비중을 얼마나 확대하고 어떤 조건(가격/수급 재확인 등)을 관망 기준으로 삼을지 제시할 것.
   - 두 시나리오 모두 지어낸 추측이 아니라 이미 계산된 지수 가격 데이터(indices의 close/recent_days, technical 근거는 여기 없으므로 지수 자체의 가격 흐름과 수급 데이터)만으로 판단할 것. 확정할 근거가 부족하면 보수적으로 "현 비중 유지"를 기본값으로 서술할 것 - 없는 수치를 지어내지 말 것.
3. 아래 JSON 스키마와 정확히 동일한 키 구조로, 다른 어떤 텍스트도 없이 JSON 객체 하나만 출력할 것.

[출력 JSON 스키마]
{schema_str}
"""


def _build_task_a2_prompt(context: dict) -> str:
    """Task A2: 금융상품 추천 + 자산배분 전략(Task A의 시장 총평과 분리된 별도 태스크)."""
    schema_str = json.dumps(TASK_A2_SCHEMA, ensure_ascii=False, indent=2)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    freshness_label = context.get("data_freshness_label") or "시장 데이터"
    return f"""아래는 {context.get('target_date')} 기준, {context.get('analysis_timestamp')}({freshness_label})에 수집된 지수/거시경제 데이터입니다.
응답은 각 필드에 명시된 문장 수를 넘기지 말고 핵심만 간결하게 작성할 것(응답 생성 시간 단축을 위함).

[시장 데이터]
{context_str}

[작성 요구사항]
1. 현재 시장 상황(금리, 수급, 지수 흐름)에 맞는 맞춤형 금융 상품(섹터 ETF, 채권형 상품, MMF, 리츠 등)을 자산관리 전략과 함께 추천할 것(financial_products).
2. portfolio_allocation.assets에 국내주식/미국주식/채권·MMF/리츠·대체투자/현금성자산 등 자산군별 추천 비중(percent, 정수)을 제시하고 percent 합계는 100이 되도록 할 것. 각 자산군의 representative_instruments에는 실제 존재하는 대표 종목/ETF명(예: KODEX 200, TIGER 미국S&P500, TLT 등)과 비중 조절 가이드를 구체적으로 명시할 것 - 카테고리명만 나열하지 말 것.
   - rebalancing_strategy는 단순히 "비중을 몇 %로 하라"는 나열이 아니라, VIP 고객에게 1:1로 브리핑하는 Senior PB의 어조(냉철하고 전문적이되 친절한 설명)로 "왜 지금 이 시점에" 이 비중으로 조정해야 하는지를 서술할 것 - macro(금리·환율 등 거시지표)와 indices의 수급/가격 흐름 중 실제로 제공된 객관적 팩트를 최소 1개 이상 근거로 명시적으로 인용하고, 데이터에 없는 이유를 지어내지 말 것.
3. 아래 JSON 스키마와 정확히 동일한 키 구조로, 다른 어떤 텍스트도 없이 JSON 객체 하나만 출력할 것.

[출력 JSON 스키마]
{schema_str}
"""


def _build_stock_task_prompt(
    context: dict, market_key: str, schema: dict, count: int, ticker_example: str, has_dart: bool
) -> str:
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    freshness_label = context.get("data_freshness_label") or "시장 데이터"
    dart_note = (
        "dart_disclosures에 이 종목의 공시가 있으면 그 제목을 사실 그대로 인용하고(내용을 임의로 해석하거나 호재/악재를 추측하지 말 것), "
        if has_dart
        else ""
    )
    return f"""아래는 {context.get('target_date')} 기준, {context.get('analysis_timestamp')}({freshness_label})에 수집된 종목별 기술적 지표 데이터입니다.
market_stance는 이미 확정된 이번 리포트의 시장 총평 결론이다 - 아래 entry_signal 판정은 반드시 이 결론과 모순되지 않아야 한다([매우 중요] 규칙 참고).

[시장 총평 결론(market_stance) - 이미 확정됨, 재해석하지 말고 그대로 전제할 것]
{json.dumps(context.get("market_stance", {}), ensure_ascii=False, indent=2)}

[기술적 지표 데이터]
{context_str}

[작성 요구사항]
1. technical.{market_key}에 있는 종목 {count}개 전부에 대해 빠짐없이 분석 항목을 작성할 것(유니버스 종목 각각이 프론트엔드에서 클릭 가능한 카드와 차트로 이어지므로 누락 없이 전부 채워야 한다). 시가총액이나 지명도로 순서를 매기지 말고, 각 종목마다 다음 4가지 객관적 지표를 전부 종합해 냉정하게 평가할 것 - 지표가 약한 종목이라도 risk에 그 약점을 명확히 쓸 것. 종목별 current_price_is_realtime이 true이면 close(전일 확정 종가)가 아니라 current_price(실시간에 가까운 현재가)를 기준으로 등락 위치·돌파/이탈 여부를 판단할 것(장중이라 close가 아직 전일 값으로 남아있는 경우):
   a) moving_averages/trend: 이동평균 정배열(상승 추세)/역배열(하락 추세)/혼조 여부.
   b) pivot_point: 차트에는 피봇(P)·1차저항(R1)·1차지지(S1) 핵심 3선만 표시되므로, 이 3개 가격대를 중심으로 서술할 것(R2/S2는 근거가 필요할 때만 보조적으로 언급).
   c) trend_channel: 고점-고점을 이은 저항 추세선(resistance_trendline), 저점-저점을 이은 지지 추세선(support_trendline)의 최근 값과 방향(상승/하락/횡보). null이면 추세선을 판단할 스윙 포인트가 부족하다는 뜻이니 언급하지 말 것.
   d) volume_momentum: latest_volume이 avg_volume_20d 대비 몇 배(volume_ratio)인지 - ratio가 1.5 이상이면 "거래량을 실은" 신뢰도 높은 신호, 1.0 미만이면 "거래량이 실리지 않은" 약한 신호로 명시적으로 구분할 것.
   각각 종목명·티커·추천 이유·매수 관전 포인트·투자 리스크를 제시할 것.
   - buy_point는 3~4문장 분량으로, 차트 근거와 모멘텀·매크로 근거를 함께 담은 PB 대응 노트로 작성할 것(짧은 한 줄 요약 금지):
     (1) 차트 근거(1~2문장, 핵심만 압축): 이 가격대가 관전포인트인 이유(피봇/추세선의 의미)와, 거래량 동반(volume_ratio 1.5배 이상) 돌파 시·거래량 없는(1.0배 미만) 돌파 시의 대응 차이를 간결히 제시하고(거래량이 뒷받침되지 않으면 되돌림/가짜 돌파 가능성이 높다는 근거만 짧게 짚을 것), 지지선 이탈 시 손절 기준(실제 가격 포함)을 이어 붙일 것.
     (2) 모멘텀·매크로 근거(1~2문장): {dart_note}news/macro 중 이 종목·업종과 명백히 관련된 항목이 있으면 함께 짚을 것(실적 발표, 산업 이슈, 금리·거시 이벤트, 지정학적 이슈 등). 관련된 사실이 데이터에 전혀 없으면 이 문장은 생략하고 (1)만으로 구성할 것 - 근거 없는 이유를 지어내지 말 것.
     감정적 수식어("기대된다", "유망하다") 없이 수치와 사실 근거로만 서술할 것.
   - breakout_price에는 technical의 resistance_1(또는 prev_high, 저항 추세선 근접값) 등을 근거로 한 상승 돌파 대응 가격을, stop_loss_price에는 support_1(또는 prev_low, 지지 추세선 근접값) 등을 근거로 한 손절/비중조절 가격을 실제 숫자로 넣을 것. 근거가 부족하면 null로 둘 것(임의 추정 금지).
   - entry_price_low/entry_price_high에는 pivot_point의 지지선(S1)이나 최근 눌림목 가격대 등 데이터에 근거한 "권장 진입 범위"를 실제 숫자로 제시할 것(현재가가 이미 그 범위 안이면 현재가 부근으로 좁게 설정 가능). 근거가 부족하면 둘 다 null로 둘 것.
   - entry_signal은 진입유효/눌림목대기/고점매수주의/진입보류 중 하나로 판정하고, entry_signal_reason에 1문장으로 근거를 밝힐 것:
     · 진입유효: 현재가(또는 current_price)가 entry_price 범위 안에 있고 추세/거래량 지표가 우호적일 때.
     · 눌림목대기: 추세는 우호적이나 현재가가 저항권에 가까워 조정을 기다려야 할 때.
     · 고점매수주의: 단기 급등·거래량 없는 돌파 등으로 추격 매수 위험이 클 때.
     · 진입보류: 기술적 지표가 불리할 때, 또는 아래 [매우 중요] 규칙에 해당할 때.
   - [매우 중요·시장 총평과의 정합성] market_stance.pb_strategy_opinion이 "비중축소"이면, 개별 종목의 차트가 아무리 좋아도 entry_signal을 "진입유효"로 판정하지 말 것 - 반드시 "눌림목대기" 또는 "진입보류" 중 하나를 선택하고, entry_signal_reason에 시장 총평이 비중축소 국면이라는 점을 명시적으로 언급할 것. pb_strategy_opinion이 "관망"이면 "진입유효"는 예외적으로 강한 기술적 근거가 있는 경우에만 신중하게 사용할 것. pb_strategy_opinion이 "매수"이면 종목별 지표에 따라 자유롭게 판정할 것.
   - ticker 필드는 반드시 technical.{market_key}의 키(종목코드 또는 티커)와 정확히 동일한 값을 사용할 것(예: "{ticker_example}").
2. 아래 JSON 스키마와 정확히 동일한 키 구조로, 다른 어떤 텍스트도 없이 JSON 객체 하나만 출력할 것. 정확히 {count}개의 원소를 가질 것.

[출력 JSON 스키마]
{schema_str}
"""


def _build_task_b_prompt(context: dict) -> str:
    """Task B: 국내 종목 PB 전략 노트."""
    return _build_stock_task_prompt(
        context, "domestic", TASK_B_SCHEMA, DOMESTIC_RECOMMENDATION_COUNT, "005930", has_dart=True
    )


def _build_task_c_prompt(context: dict) -> str:
    """Task C: 미국 종목 PB 전략 노트. DART는 국내 전자공시 시스템이라 미국 종목에는 데이터가
    없으므로(has_dart=False), 프롬프트에서 DART 언급 자체를 하지 않는다."""
    return _build_stock_task_prompt(
        context, "us", TASK_C_SCHEMA, US_RECOMMENDATION_COUNT, "AAPL", has_dart=False
    )


def _build_task_d_prompt(context: dict) -> str:
    """Task D: 뉴스 파급력 분석."""
    schema_str = json.dumps(TASK_D_SCHEMA, ensure_ascii=False, indent=2)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    return f"""아래는 {context.get('target_date')} 기준, {context.get('analysis_timestamp')}(장중 실시간)에 수집된 뉴스 데이터입니다.

[뉴스 데이터]
{context_str}

[작성 요구사항]
1. news 항목(코스피/코스닥/미국 증시/금리/반도체/환율 등 다양한 쿼리에서 수집됨) 중 시장에 실질적 영향을 줄 만한 주요 뉴스를 정확히 {NEWS_IMPACT_COUNT}개 선정해 각각의 시장 파급 효과(Impact Analysis)를 해석할 것. 특정 쿼리에 편중되지 말고 국내/해외/거시/섹터 뉴스가 고르게 섞이도록 할 것. news에 없는 헤드라인을 지어내지 말 것.
2. 아래 JSON 스키마와 정확히 동일한 키 구조로, 다른 어떤 텍스트도 없이 JSON 객체 하나만 출력할 것. news_impact_analysis는 정확히 {NEWS_IMPACT_COUNT}개의 원소를 가질 것.

[출력 JSON 스키마]
{schema_str}
"""


def _build_diagnosis_prompt(context: dict) -> str:
    """보유 종목 맞춤 진단(부가 기능) 프롬프트.

    [분석 대상] 줄을 항상 고정 포맷("종목명 (종목코드)")으로 못박는 이유: 사용자가 화면에
    이름으로 입력하든 코드로 입력하든 resolve_domestic_ticker()가 이미 동일한 (코드, 공식명)
    쌍으로 수렴시켰으므로(collector.resolve_domestic_ticker 참고), 프롬프트에서도 그 확정된
    쌍만 유일한 분석 대상으로 제시해 LLM이 사용자의 원문 입력 형태(이름/코드/띄어쓰기 차이)에
    따라 서로 다른 맥락으로 해석할 여지를 없앤다.
    """
    schema_str = json.dumps(TASK_DIAGNOSIS_SCHEMA, ensure_ascii=False, indent=2)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    holding = context.get("holding", {})
    return f"""[분석 대상]: {holding.get('name')} ({holding.get('ticker')})

아래는 위 종목 1개에 대한 보유자의 진단 요청 데이터입니다. {context.get('target_date')} 기준, {context.get('analysis_timestamp')}에 수집됐습니다.

[보유 종목 및 시장 데이터]
{context_str}

[작성 요구사항]
1. profit_diagnosis: pnl_amount/pnl_pct는 이미 정확히 계산되어 주어졌으니 재계산하지 말고 그대로 인용할 것. 이를 근거로 현재 평가손익을 진단하고, avg_price(평단가)가 technical의 pivot_point(P/R1/S1)나 이동평균 대비 어느 위치에 있는지 기술적으로 해석할 것(3~4문장). current_price_is_realtime이 true이면 장중 실시간 시세 기준임을 밝힐 것.
2. strategies.day_trading(단타: 당일~수일)/swing(스윙: 수주~수개월)/long_term(장기: 수개월 이상) 각각에 대해:
   - action: 해당 투자 호흡에 맞는 구체적 대응을 pivot_point/trend_channel/volume_momentum 등 실제 수치를 근거로 제시할 것(추가매수/손절/익절 가격을 실제 숫자로 포함). 평단가(avg_price) 대비 현재 손익 상황도 함께 고려할 것.
   - risk_level: 낮음/보통/높음 중 하나.
   - risk_reward_note: 손절폭 대비 목표 상승폭 비율 등 위험 대비 기대수익을 정성적으로 설명할 것. "승률 OO%"처럼 실제 백테스트 없이 지어낸 통계 수치는 절대 쓰지 말 것.
3. market_consistency_note: market_stance가 null이 아니면 그 pb_strategy_opinion(매수/관망/비중축소)과 이 진단이 어떻게 연결되는지 명시할 것(예: 시장이 비중축소 국면이면 세 전략 모두 신규 추가매수보다 손절/비중축소 관점을 우선 고려하도록 서술). market_stance가 null이면 "최신 시장 총평 데이터가 없어 이 종목 자체의 기술적 지표만으로 진단했다"는 취지를 밝힐 것.
4. 감정적 수식어("기대된다", "유망하다") 없이 수치와 사실 근거로만 서술할 것. 아래 JSON 스키마와 정확히 동일한 키 구조로, 다른 어떤 텍스트도 없이 JSON 객체 하나만 출력할 것.

[출력 JSON 스키마]
{schema_str}
"""


# ---------------------------------------------------------------------------
# 3. LLM 호출 (OpenAI / Gemini)
# ---------------------------------------------------------------------------

def _resolve_provider(provider: Optional[str]) -> str:
    provider = provider or os.getenv("AI_PROVIDER")
    if provider:
        return provider.lower()
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    raise RuntimeError(
        "AI_PROVIDER를 지정하거나 OPENAI_API_KEY / GEMINI_API_KEY(GOOGLE_API_KEY) 중 "
        "하나를 환경 변수에 설정해야 합니다."
    )


def _call_openai(system_prompt: str, user_prompt: str) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")

    # SDK 기본 타임아웃(수 분)이 너무 길어, 네트워크 문제 시 요청 락이 장시간 묶이는
    # 원인이 될 수 있다. 명시적으로 짧게 설정해 빠르게 실패하고 재시도하게 한다.
    client = OpenAI(api_key=api_key, timeout=LLM_TIMEOUT_SECONDS)
    # getenv(key, default)는 환경변수가 "존재하지만 빈 문자열"인 경우 기본값을 쓰지 않으므로 or로 방어한다.
    model = os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    return response.choices[0].message.content


def _call_gemini(system_prompt: str, user_prompt: str) -> str:
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY(GOOGLE_API_KEY) 환경 변수가 설정되지 않았습니다.")

    genai.configure(api_key=api_key)
    # getenv(key, default)는 환경변수가 "존재하지만 빈 문자열"인 경우 기본값을 쓰지 않으므로 or로 방어한다.
    # (예: Render에 GEMINI_MODEL을 빈 값으로 등록하면 SDK가 "models/"라는 깨진 모델명을 만들어 400 에러가 난다.)
    model_name = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    model = genai.GenerativeModel(model_name=model_name, system_instruction=system_prompt)
    response = model.generate_content(
        user_prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.4,
        ),
        # SDK 기본 타임아웃이 너무 길어질 수 있어 명시적으로 짧게 설정한다(요청 락 장시간
        # 점유 방지).
        request_options={"timeout": LLM_TIMEOUT_SECONDS},
    )
    return response.text


def call_llm(system_prompt: str, user_prompt: str, provider: Optional[str] = None) -> str:
    resolved = _resolve_provider(provider)
    if resolved == "openai":
        return _call_openai(system_prompt, user_prompt)
    if resolved == "gemini":
        return _call_gemini(system_prompt, user_prompt)
    raise ValueError(f"지원하지 않는 AI_PROVIDER 입니다: {resolved} (openai | gemini)")


async def _call_openai_async(system_prompt: str, user_prompt: str) -> str:
    from openai import AsyncOpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")

    client = AsyncOpenAI(api_key=api_key, timeout=LLM_TIMEOUT_SECONDS)
    model = os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    return response.choices[0].message.content


async def _call_gemini_async(system_prompt: str, user_prompt: str) -> str:
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY(GOOGLE_API_KEY) 환경 변수가 설정되지 않았습니다.")

    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    model = genai.GenerativeModel(model_name=model_name, system_instruction=system_prompt)
    response = await model.generate_content_async(
        user_prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.4,
        ),
        request_options={"timeout": LLM_TIMEOUT_SECONDS},
    )
    return response.text


async def call_llm_async(system_prompt: str, user_prompt: str, provider: Optional[str] = None) -> str:
    resolved = _resolve_provider(provider)
    if resolved == "openai":
        return await _call_openai_async(system_prompt, user_prompt)
    if resolved == "gemini":
        return await _call_gemini_async(system_prompt, user_prompt)
    raise ValueError(f"지원하지 않는 AI_PROVIDER 입니다: {resolved} (openai | gemini)")


# ---------------------------------------------------------------------------
# 4. 응답 파싱 및 스키마 검증
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_FENCE_RE.search(text)
    if match:
        return json.loads(match.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("모델 응답에서 유효한 JSON을 추출하지 못했습니다.")


def _validate_task_a(data: dict) -> None:
    overview = data.get("market_overview")
    if not isinstance(overview, dict):
        raise ValueError("market_overview가 없습니다.")
    opinion = overview.get("pb_strategy_opinion")
    if opinion not in VALID_STRATEGY_OPINIONS:
        raise ValueError(f"pb_strategy_opinion 값이 올바르지 않습니다: {opinion!r}")
    supply_demand_status = overview.get("supply_demand_status")
    if supply_demand_status not in VALID_SUPPLY_DEMAND_STATUS:
        raise ValueError(f"supply_demand_status 값이 올바르지 않습니다: {supply_demand_status!r}")
    if not overview.get("supply_demand_analysis"):
        raise ValueError("market_overview.supply_demand_analysis가 비어있습니다.")
    if not overview.get("intraday_playbook"):
        raise ValueError("market_overview.intraday_playbook이 비어있습니다.")
    if not overview.get("account_scenario_bullish"):
        raise ValueError("market_overview.account_scenario_bullish가 비어있습니다.")
    if not overview.get("account_scenario_bearish"):
        raise ValueError("market_overview.account_scenario_bearish가 비어있습니다.")


def _validate_task_a2(data: dict) -> None:
    if not isinstance(data.get("financial_products"), list):
        raise ValueError("financial_products는 리스트여야 합니다.")

    allocation = data.get("portfolio_allocation", {})
    assets = allocation.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("portfolio_allocation.assets는 비어있지 않은 리스트여야 합니다.")
    total_percent = 0
    for asset in assets:
        if "name" not in asset or "percent" not in asset:
            raise ValueError(f"portfolio_allocation.assets 항목에 name/percent가 필요합니다: {asset}")
        if not asset.get("representative_instruments"):
            raise ValueError(f"portfolio_allocation.assets 항목에 representative_instruments가 필요합니다: {asset}")
        total_percent += asset["percent"]
    if not (95 <= total_percent <= 105):
        raise ValueError(f"portfolio_allocation.assets의 percent 합계가 100에서 크게 벗어났습니다: {total_percent}")
    if not allocation.get("rebalancing_strategy"):
        raise ValueError("portfolio_allocation.rebalancing_strategy가 비어있습니다.")


def _validate_stock_items(items: Any, expected_count: int, label: str) -> None:
    if not isinstance(items, list) or len(items) != expected_count:
        raise ValueError(f"{label}는 정확히 {expected_count}개의 종목이어야 합니다.")
    required_fields = {
        "name",
        "ticker",
        "reason",
        "buy_point",
        "risk",
        "entry_price_low",
        "entry_price_high",
        "entry_signal",
        "entry_signal_reason",
        "breakout_price",
        "stop_loss_price",
    }
    for item in items:
        if not required_fields.issubset(item):
            raise ValueError(f"{label} 항목에 누락된 필드가 있습니다: {item}")
        if item.get("entry_signal") not in VALID_ENTRY_SIGNALS:
            raise ValueError(f"{label} 항목의 entry_signal 값이 올바르지 않습니다: {item.get('entry_signal')!r}")


def _validate_task_b(data: dict) -> None:
    _validate_stock_items(data.get("domestic"), DOMESTIC_RECOMMENDATION_COUNT, "recommended_stocks.domestic")


def _validate_task_c(data: dict) -> None:
    _validate_stock_items(data.get("us"), US_RECOMMENDATION_COUNT, "recommended_stocks.us")


def _validate_task_d(data: dict) -> None:
    news_items = data.get("news_impact_analysis")
    if not isinstance(news_items, list) or len(news_items) != NEWS_IMPACT_COUNT:
        raise ValueError(f"news_impact_analysis는 정확히 {NEWS_IMPACT_COUNT}개여야 합니다.")


def _validate_diagnosis(data: dict) -> None:
    if not data.get("profit_diagnosis"):
        raise ValueError("profit_diagnosis가 비어있습니다.")
    strategies = data.get("strategies")
    if not isinstance(strategies, dict):
        raise ValueError("strategies가 없습니다.")
    for key in ("day_trading", "swing", "long_term"):
        item = strategies.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"strategies.{key}가 없습니다.")
        if not item.get("action"):
            raise ValueError(f"strategies.{key}.action이 비어있습니다.")
        if item.get("risk_level") not in VALID_RISK_LEVELS:
            raise ValueError(f"strategies.{key}.risk_level 값이 올바르지 않습니다: {item.get('risk_level')!r}")
        if not item.get("risk_reward_note"):
            raise ValueError(f"strategies.{key}.risk_reward_note가 비어있습니다.")
    if not data.get("market_consistency_note"):
        raise ValueError("market_consistency_note가 비어있습니다.")


# ---------------------------------------------------------------------------
# 5. 저장
# ---------------------------------------------------------------------------

def save_report(report: dict, output_path: str = OUTPUT_PATH_DEFAULT) -> str:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return output_path


# ---------------------------------------------------------------------------
# 6. 종합 엔트리포인트 - Task A/D를 먼저 동시 호출해 시장 총평을 확정한 뒤,
#    그 결론(market_stance)을 받아 Task B/C(종목별 entry_signal)를 동시 호출한다.
# ---------------------------------------------------------------------------


async def _generate_task(
    task_name: str,
    context: dict,
    build_prompt: Callable[[dict], str],
    validator: Callable[[dict], None],
    provider: Optional[str],
) -> dict:
    """단일 태스크를 호출->파싱->검증하고, 실패 시 MAX_GENERATION_ATTEMPTS까지 재시도한다."""
    user_prompt = build_prompt(context)
    last_error: Optional[Exception] = None
    for _ in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            raw_text = await call_llm_async(SYSTEM_PROMPT, user_prompt, provider=provider)
            parsed = _extract_json(raw_text)
            validator(parsed)
            return parsed
        except Exception as exc:  # noqa: BLE001 - 재시도 후 최종 실패 시 위로 전파
            last_error = exc
            continue
    raise RuntimeError(f"Task {task_name} 생성에 {MAX_GENERATION_ATTEMPTS}회 실패했습니다: {last_error}") from last_error


async def generate_pb_report_async(
    target_date: DateLike = None,
    provider: Optional[str] = None,
    output_path: str = OUTPUT_PATH_DEFAULT,
    market_data: Optional[dict] = None,
) -> dict:
    """시장 데이터를 수집(또는 재사용)하고 Senior PB 리포트를 생성, 저장한다.

    Task A(시장총평·자산배분)와 Task D(뉴스)를 먼저 동시 호출해 시장 총평 결론을 확정한 뒤,
    그 결론(market_stance)을 Task B(국내종목)/Task C(미국종목)에 전달해 동시 호출한다.
    종목별 entry_signal이 시장 총평의 pb_strategy_opinion과 모순되지 않게 하려면(예: 비중축소
    국면인데 개별 종목만 "진입유효") B/C가 A의 실제 결론을 알아야 하므로, 4개 태스크를 전부
    동시에 실행할 수는 없다 - 대신 뉴스(D)는 시장 스탠스와 무관하므로 A와 묶어 병렬로 돌려
    지연을 최소화한다.

    Args:
        target_date: 리포트 기준일. None이면 오늘.
        provider: "openai" | "gemini". None이면 환경 변수로 자동 판단.
        output_path: 저장할 JSON 파일 경로.
        market_data: 이미 collect_market_data()로 수집한 결과가 있으면 재사용(중복 수집 방지).

    Returns:
        저장된 리포트 dict (meta, source_errors 포함).
    """
    resolved_date = _to_date(target_date)
    # collect_market_data는 동기/블로킹 함수이므로 이벤트 루프를 막지 않도록 스레드로 위임한다.
    market_data = market_data or await asyncio.to_thread(collect_market_data, resolved_date)
    full_context = build_prompt_context(market_data)

    context_a = {
        k: full_context[k]
        for k in ("target_date", "analysis_timestamp", "data_freshness", "data_freshness_label", "indices", "macro")
    }
    context_a2 = {
        k: full_context[k]
        for k in ("target_date", "analysis_timestamp", "data_freshness_label", "indices", "macro")
    }
    context_d = {k: full_context[k] for k in ("target_date", "analysis_timestamp", "news")}

    # 1단계: 시장총평(A)+자산배분/금융상품(A2)+뉴스(D) 동시 실행 - B/C는 A의 결론이 나와야
    # 시작할 수 있다. A/A2는 원래 하나의 태스크였으나, 요구 출력량이 늘면서 응답 시간이 길어져
    # LLM_TIMEOUT_SECONDS를 초과하는 사례가 있어(504 Deadline exceeded) 둘로 쪼갰다.
    phase1 = await asyncio.gather(
        _generate_task("A", context_a, _build_task_a_prompt, _validate_task_a, provider),
        _generate_task("A2", context_a2, _build_task_a2_prompt, _validate_task_a2, provider),
        _generate_task("D", context_d, _build_task_d_prompt, _validate_task_d, provider),
        return_exceptions=True,
    )
    phase1_errors = [r for r in phase1 if isinstance(r, Exception)]
    if phase1_errors:
        raise RuntimeError(
            f"PB 리포트 생성 중 {len(phase1_errors)}개 태스크가 실패했습니다: {phase1_errors[0]}"
        ) from phase1_errors[0]
    task_a, task_a2, task_d = phase1

    # A가 확정한 시장 총평 결론을 B/C에 "이미 확정된 전제"로 전달한다(재해석 금지 - 프롬프트에서 강제).
    market_stance = {
        "pb_strategy_opinion": task_a["market_overview"]["pb_strategy_opinion"],
        "strategy_rationale": task_a["market_overview"]["strategy_rationale"],
    }

    context_b = {
        "target_date": full_context["target_date"],
        "analysis_timestamp": full_context["analysis_timestamp"],
        "data_freshness_label": full_context["data_freshness_label"],
        "market_stance": market_stance,
        "technical": {"domestic": full_context["technical"]["domestic"]},
        # 매수 관전포인트에 모멘텀/매크로 근거(실적 공시, 관련 뉴스, 금리 등)를 사실에
        # 근거해 덧붙일 수 있도록 국내 종목 유니버스에 해당하는 공시만 추려 전달한다.
        "dart_disclosures": {
            code: v
            for code, v in full_context["dart_disclosures"].items()
            if code in full_context["technical"]["domestic"]
        },
        "macro": full_context["macro"],
        "news": full_context["news"],
    }
    context_c = {
        "target_date": full_context["target_date"],
        "analysis_timestamp": full_context["analysis_timestamp"],
        "data_freshness_label": full_context["data_freshness_label"],
        "market_stance": market_stance,
        "technical": {"us": full_context["technical"]["us"]},
        # DART는 국내 전자공시 시스템이라 미국 종목에는 해당 데이터가 없으므로 dart_disclosures는
        # 전달하지 않는다(has_dart=False와 짝을 맞춤).
        "macro": full_context["macro"],
        "news": full_context["news"],
    }

    # 2단계: 국내(B)/미국(C) 종목 태스크 동시 실행.
    phase2 = await asyncio.gather(
        _generate_task("B", context_b, _build_task_b_prompt, _validate_task_b, provider),
        _generate_task("C", context_c, _build_task_c_prompt, _validate_task_c, provider),
        return_exceptions=True,
    )
    phase2_errors = [r for r in phase2 if isinstance(r, Exception)]
    if phase2_errors:
        raise RuntimeError(
            f"PB 리포트 생성 중 {len(phase2_errors)}개 태스크가 실패했습니다: {phase2_errors[0]}"
        ) from phase2_errors[0]
    task_b, task_c = phase2

    # 결정론적 안전장치: 비중축소 국면에서는 개별 종목의 진입유효 시그널을 코드 레벨에서
    # 강제로 하향 조정해, LLM이 프롬프트 지시를 놓치더라도 시장 총평과의 모순을 원천 차단한다.
    if market_stance["pb_strategy_opinion"] == "비중축소":
        for stock in [*task_b["domestic"], *task_c["us"]]:
            if stock.get("entry_signal") == "진입유효":
                stock["entry_signal"] = "진입보류"
                stock["entry_signal_reason"] = (
                    "시장 총평이 비중축소 국면으로 판단되어, 개별 기술적 신호와 무관하게 신규 진입을 보류합니다."
                )

    report_body = {
        "market_overview": task_a["market_overview"],
        "news_impact_analysis": task_d["news_impact_analysis"],
        "recommended_stocks": {
            "domestic": task_b["domestic"],
            "us": task_c["us"],
        },
        "financial_products": task_a2["financial_products"],
        "portfolio_allocation": task_a2["portfolio_allocation"],
        "disclaimer": DISCLAIMER_TEXT,
    }

    # 프론트엔드가 캔들차트 등을 그릴 수 있도록 원본 시장 데이터(전체 OHLCV/이평선/지수/거시지표 등)를 함께 저장.
    raw_market_data = {k: v for k, v in market_data.items() if k not in ("meta", "errors")}
    collector_meta = market_data.get("meta", {})

    report = {
        "meta": {
            "target_date": resolved_date.isoformat(),
            "generated_at": _now_kst().isoformat(timespec="seconds"),
            "ai_provider": _resolve_provider(provider),
            # "intraday"(장중, 당일 확정 종가 미게시) | "market_closed"(장마감, 확정 데이터)
            "data_freshness": collector_meta.get("data_freshness"),
            "data_freshness_label": collector_meta.get("data_freshness_label"),
            "data_asof_time": collector_meta.get("data_asof_time"),
        },
        **report_body,
        "market_data": raw_market_data,
        "source_data_errors": market_data.get("errors", []),
    }

    save_report(report, output_path)
    return report


def generate_pb_report(
    target_date: DateLike = None,
    provider: Optional[str] = None,
    output_path: str = OUTPUT_PATH_DEFAULT,
    market_data: Optional[dict] = None,
) -> dict:
    """generate_pb_report_async()의 동기 래퍼(CLI 등 기존 동기 호출부 호환용)."""
    return asyncio.run(
        generate_pb_report_async(
            target_date=target_date,
            provider=provider,
            output_path=output_path,
            market_data=market_data,
        )
    )


# ---------------------------------------------------------------------------
# 7. 보유 종목 맞춤 진단 (부가 기능) - 메인 4-태스크 파이프라인과 독립적으로,
#    사용자가 인터랙티브 차트 하단 폼에서 요청할 때만 단발성으로 호출된다.
# ---------------------------------------------------------------------------

def _load_latest_market_stance() -> Optional[dict]:
    """가장 최근 저장된 리포트의 시장 총평 스탠스를 읽어온다(진단 프롬프트의 시장 정합성
    근거용). 저장된 리포트가 없거나 읽기에 실패하면 None을 반환한다 - 이 경우 프롬프트가
    스탠스 정보 없이 종목 자체의 기술적 지표만으로 진단하도록 안내한다."""
    try:
        with open(OUTPUT_PATH_DEFAULT, "r", encoding="utf-8") as f:
            report = json.load(f)
        overview = report.get("market_overview") or {}
        opinion = overview.get("pb_strategy_opinion")
        if not opinion:
            return None
        return {
            "pb_strategy_opinion": opinion,
            "strategy_rationale": overview.get("strategy_rationale"),
        }
    except Exception:
        return None


async def diagnose_stock_holding(
    query: str,
    market: str,
    quantity: float,
    avg_price: float,
    target_date: DateLike = None,
    provider: Optional[str] = None,
) -> dict:
    """사용자가 입력한 보유 종목 1개에 대해 실시간 기술적 지표를 조회하고, 평가손익을
    코드에서 정확히 계산한 뒤(LLM이 산술을 틀리지 않도록), 최신 시장 총평 스탠스와
    모순되지 않는 맞춤 PB 진단(단타/스윙/장기 전략)을 생성한다.

    Args:
        query: 종목명 또는 종목코드/티커.
        market: "domestic" | "us".
        quantity: 보유 수량(0보다 커야 함, 호출부에서 검증).
        avg_price: 매수 평균단가(0보다 커야 함, 호출부에서 검증).
        target_date: 기준일. None이면 오늘(KST).
        provider: "openai" | "gemini". None이면 환경 변수로 자동 판단.

    Returns:
        {meta, holding(수량/평단가/현재가/평가손익), diagnosis(AI 진단), technical(차트용 원본 데이터)}
    """
    if market not in ("domestic", "us"):
        raise ValueError(f"market은 domestic 또는 us여야 합니다: {market!r}")

    resolved_date = _to_date(target_date)

    if market == "domestic":
        code, name = await asyncio.to_thread(resolve_domestic_ticker, query, resolved_date)
        ticker = code
        technical = await asyncio.to_thread(_fetch_stock_technical, code, name, resolved_date)
    else:
        ticker = query.strip().upper()
        name = ticker
        technical = await asyncio.to_thread(_fetch_us_stock_technical, ticker, name, resolved_date)

    current_price = technical.get("current_price")
    if current_price is None:
        raise ValueError(f"{name}({ticker})의 현재가를 확인할 수 없습니다.")

    pnl_amount = (current_price - avg_price) * quantity
    pnl_pct = (current_price - avg_price) / avg_price * 100
    position_value = current_price * quantity

    holding = {
        "name": name,
        "ticker": ticker,
        "market": market,
        "quantity": quantity,
        "avg_price": avg_price,
        "current_price": current_price,
        "current_price_is_realtime": technical.get("current_price_is_realtime", False),
        "pnl_amount": round(pnl_amount, 2),
        "pnl_pct": round(pnl_pct, 2),
        "position_value": round(position_value, 2),
    }

    market_stance = _load_latest_market_stance()
    diagnosis_context = {
        "target_date": resolved_date.isoformat(),
        "analysis_timestamp": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "holding": holding,
        "market_stance": market_stance,
        "technical": _condense_technical({ticker: technical})[ticker],
    }

    prompt = _build_diagnosis_prompt(diagnosis_context)
    last_error: Optional[Exception] = None
    diagnosis_body: Optional[dict] = None
    for _ in range(MAX_GENERATION_ATTEMPTS):
        try:
            raw_text = await call_llm_async(SYSTEM_PROMPT, prompt, provider=provider)
            parsed = _extract_json(raw_text)
            _validate_diagnosis(parsed)
            diagnosis_body = parsed
            break
        except Exception as exc:  # noqa: BLE001 - 재시도 후 최종 실패 시 위로 전파
            last_error = exc
            continue
    if diagnosis_body is None:
        raise RuntimeError(f"보유 종목 진단 생성에 실패했습니다: {last_error}") from last_error

    return {
        "meta": {
            "target_date": resolved_date.isoformat(),
            "generated_at": _now_kst().isoformat(timespec="seconds"),
            "ai_provider": _resolve_provider(provider),
        },
        "holding": holding,
        "diagnosis": diagnosis_body,
        # 프론트엔드가 인터랙티브 차트와 동일한 컴포넌트로 렌더링할 수 있도록 원본 기술적
        # 지표(OHLCV/이평선/피봇/추세선)를 그대로 함께 반환한다.
        "technical": technical,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Senior PB 종합 리포트 생성기")
    parser.add_argument("--date", dest="target_date", default=None, help="기준일 YYYY-MM-DD (기본값: 오늘)")
    parser.add_argument("--provider", dest="provider", default=None, choices=["openai", "gemini"])
    parser.add_argument("--output", dest="output_path", default=OUTPUT_PATH_DEFAULT)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = generate_pb_report(
        target_date=args.target_date,
        provider=args.provider,
        output_path=args.output_path,
    )
    print(f"PB 리포트가 저장되었습니다: {args.output_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))

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
import datetime as dt
import json
import os
import re
from typing import Any, Optional

from dotenv import load_dotenv

from collector import DateLike, MAJOR_STOCKS, US_STOCKS, _to_date, collect_market_data

load_dotenv()

OUTPUT_PATH_DEFAULT = "pb_report_latest.json"
MAX_GENERATION_ATTEMPTS = 2

# 추천 종목 개수는 collector의 종목 유니버스 크기와 항상 일치시킨다(유니버스 전체가
# 분석·랭킹되어 프론트엔드 "더보기"로 전부 확인 가능하도록).
DOMESTIC_RECOMMENDATION_COUNT = len(MAJOR_STOCKS)
US_RECOMMENDATION_COUNT = len(US_STOCKS)

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# ---------------------------------------------------------------------------
# 리포트 JSON 스키마 (프롬프트에 그대로 포함해 모델이 형식을 따르게 한다)
# ---------------------------------------------------------------------------

REPORT_JSON_TEMPLATE = {
    "market_overview": {
        "summary": "국내외 지수, 수급, 거시지표를 종합한 3~5문장 시장 흐름 분석. 결론(헤드라인)을 먼저 던지고 근거 수치로 뒷받침하는 브리핑 톤으로 작성",
        "pb_strategy_opinion": "매수 | 관망 | 비중축소 중 하나",
        "strategy_rationale": "위 전략 의견을 제시한 근거 2~3문장",
        "supply_demand_status": "매집 | 이탈 | 혼조 | 데이터없음 중 하나 (기관/외국인 수급 데이터가 없으면 데이터없음)",
        "supply_demand_analysis": "국내 증시 수급 심층 분석 3~5문장. institution_net_buy/foreign_net_buy가 있으면 recent_days 시계열(날짜별 종가+기관/외국인 순매수)을 대조해 (1)기관과 외국인의 매매 방향이 같은지/엇갈리는지, (2)순매수가 집중된 종가 구간(매집 구간)과 순매도가 집중된 구간(이탈 구간)을 실제 가격 수치로, (3)그 매집/이탈 구간을 기술적 지지·저항선과 겹쳐 본 장중 대응 전략까지 제시할 것. institution_net_buy/foreign_net_buy가 모두 null이면(데이터 미제공) 이를 명시하고 change_pct·volume 기반 장중 모멘텀 해석 및 technical의 pivot_point 기반 단기 지지/저항선으로 대체할 것",
        "intraday_playbook": "장중 실시간 대응 시나리오 2~3문장. '상승 돌파 시(구체적 가격 이상)' 대응과 '지지선 이탈 시(구체적 가격 이하)' 손절/비중조절 기준을 각각 실제 수치로 명시할 것",
    },
    "news_impact_analysis": [
        {
            "headline": "실제 수집된 뉴스 제목 중 하나를 그대로 인용",
            "summary": "해당 뉴스의 핵심 내용 1~2문장 요약",
            "impact": "이 뉴스가 시장/섹터에 미치는 파급 효과 분석 2~3문장",
            "affected_sectors": ["영향받는 섹터/업종 목록"],
        }
    ],
    "recommended_stocks": {
        "domestic": [
            {
                "name": "종목명",
                "ticker": "종목코드(예: 005930)",
                "reason": "추천 이유",
                "buy_point": "매수 관전 포인트(가격대, 이벤트, 기술적 신호 등)",
                "breakout_price": "상승 돌파 시 추가 매수/대응 기준가(숫자, technical의 resistance_1 등 활용). 판단 불가 시 null",
                "stop_loss_price": "지지선 이탈 시 손절/비중조절 기준가(숫자, technical의 support_1 등 활용). 판단 불가 시 null",
                "risk": "투자 리스크",
            }
        ],
        "us": [
            {
                "name": "종목명",
                "ticker": "티커(예: AAPL)",
                "reason": "추천 이유",
                "buy_point": "매수 관전 포인트",
                "breakout_price": "상승 돌파 시 대응 기준가(숫자). 판단 불가 시 null",
                "stop_loss_price": "지지선 이탈 시 손절 기준가(숫자). 판단 불가 시 null",
                "risk": "투자 리스크",
            }
        ],
    },
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
        "rebalancing_strategy": "현재 시장 상황에 맞춘 리밸런싱 전략 2~3문장 (예: 비중 확대/축소할 자산군과 그 이유, 리밸런싱 주기 제안)",
    },
    "disclaimer": "투자 판단의 최종 책임은 투자자 본인에게 있다는 취지의 안내 문구",
}

REQUIRED_TOP_LEVEL_KEYS = (
    "market_overview",
    "news_impact_analysis",
    "recommended_stocks",
    "financial_products",
    "portfolio_allocation",
    "disclaimer",
)

VALID_STRATEGY_OPINIONS = {"매수", "관망", "비중축소"}
VALID_SUPPLY_DEMAND_STATUS = {"매집", "이탈", "혼조", "데이터없음"}


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

        # 최근 거래일의 종가와 기관/외국인 순매수를 날짜별로 대조한 시계열.
        # institution_net_buy/foreign_net_buy가 전부 null이면 수급 데이터 자체가 없는 상태
        # (KRX 로그인 미설정 등)이며, 이 경우 프롬프트에서 거래량·등락률 기반 모멘텀
        # 분석으로 대체하도록 안내한다. 데이터가 있으면 종가 대비 순매수 흐름을 대조해
        # 수급 주체별 매집/이탈 구간을 판단하는 데 사용한다.
        recent_days = [
            {
                "date": p["date"],
                "close": p.get("close"),
                "institution_net_buy": sd_history_by_date.get(p["date"], {}).get("institution_net_buy"),
                "foreign_net_buy": sd_history_by_date.get(p["date"], {}).get("foreign_net_buy"),
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
    return {
        "target_date": market_data.get("meta", {}).get("target_date"),
        "analysis_timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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


def build_user_prompt(context: dict) -> str:
    schema_str = json.dumps(REPORT_JSON_TEMPLATE, ensure_ascii=False, indent=2)
    context_str = json.dumps(context, ensure_ascii=False, indent=2)
    return f"""아래는 {context.get('target_date')} 기준, {context.get('analysis_timestamp')}(장중 실시간)에 수집된 시장 데이터입니다.
이 시각 이후 시세가 더 움직였을 수 있다는 점을 염두에 두고, "지금 이 시각 기준"임을 리포트 안에서 자연스럽게 드러낼 것.

[시장 데이터]
{context_str}

[작성 요구사항]
1. 국내/미국 지수, 수급, 거시지표를 종합해 시장 흐름을 분석하고, PB로서 매수/관망/비중축소 중 하나의 전략 의견을 제시할 것.
   - indices의 국내 지수(KOSPI/KOSDAQ)에 institution_net_buy/foreign_net_buy 수치가 있으면, recent_days(날짜별 종가 + 기관/외국인 순매수 시계열)를 직접 대조해 다음을 포함한 심층 수급 분석을 작성할 것(supply_demand_analysis):
     a) 기관과 외국인의 매매 방향이 최근 며칠간 같은 방향인지, 엇갈리는지(예: 외국인 매수·기관 매도의 수급 공방).
     b) recent_days에서 순매수(양수)가 몰린 날짜들의 종가 범위를 "매집 구간"으로, 순매도(음수)가 몰린 날짜들의 종가 범위를 "이탈 구간"으로 짚어낼 것 - 실제 수치에 근거해야 하며 데이터에 없는 가격대를 지어내지 말 것.
     c) 위에서 짚은 매집/이탈 구간을 technical.domestic 종목들의 pivot_point(지지/저항선)와 겹쳐 보고, 장중 대응 지지선·저항선 및 대응전략(매수/관망/비중축소 중 어느 시점에 어떤 대응)을 구체적으로 제시할 것.
     이 내용을 바탕으로 supply_demand_status를 매집/이탈/혼조 중 하나로 판정할 것.
     supply_demand_date가 target_date보다 이전이면 "직전 영업일 기준" 데이터임을 밝힐 것.
   - institution_net_buy/foreign_net_buy가 모두 null이면(KRX 로그인 미설정 등으로 수급 데이터 자체가 없는 경우) supply_demand_status를 "데이터없음"으로 설정하고 이를 명시한 뒤, 대신 change_pct(등락률)와 volume(거래량)을 근거로 한 장중 모멘텀 분석으로 대체할 것 - 단기 지지/저항선(technical의 pivot_point 활용), 수급 유입이 기대되는 업종(뉴스의 affected_sectors 참고), 장중 대응전략을 구체적으로 제시할 것.
   - intraday_playbook에는 "OO,OOO원 상향 돌파 시 추가 매수/비중 확대", "OO,OOO원 이탈 시 손절 또는 비중 축소"처럼 지수 또는 대표 종목의 실제 가격 수치를 기준으로 한 이분법적 시나리오를 제시할 것.
2. news 항목 중 시장에 실질적 영향을 줄 만한 주요 뉴스를 골라 각각의 시장 파급 효과(Impact Analysis)를 해석할 것.
3. technical.domestic에 있는 국내 종목 {DOMESTIC_RECOMMENDATION_COUNT}개 전부, technical.us에 있는 미국 종목 {US_RECOMMENDATION_COUNT}개 전부에 대해 빠짐없이 분석 항목을 작성할 것(유니버스 종목 각각이 프론트엔드에서 클릭 가능한 카드와 차트로 이어지므로 누락 없이 전부 채워야 한다). 시가총액이나 지명도로 순서를 매기지 말고, 각 종목마다 다음 4가지 객관적 지표를 전부 종합해 냉정하게 평가할 것 - 지표가 약한 종목이라도 risk에 그 약점을 명확히 쓸 것:
   a) moving_averages/trend: 이동평균 정배열(상승 추세)/역배열(하락 추세)/혼조 여부.
   b) pivot_point: 피봇 기준 지지선(support_1/2)·저항선(resistance_1/2) 가격대.
   c) trend_channel: 고점-고점을 이은 저항 추세선(resistance_trendline), 저점-저점을 이은 지지 추세선(support_trendline)의 최근 값과 방향(상승/하락/횡보). null이면 추세선을 판단할 스윙 포인트가 부족하다는 뜻이니 언급하지 말 것.
   d) volume_momentum: latest_volume이 avg_volume_20d 대비 몇 배(volume_ratio)인지 - ratio가 1.5 이상이면 "거래량을 실은" 신뢰도 높은 신호, 1.0 미만이면 "거래량이 실리지 않은" 약한 신호로 명시적으로 구분할 것.
   각각 종목명·티커·추천 이유·매수 관전 포인트·투자 리스크를 제시할 것.
   - buy_point에는 위 a)~d)를 종합한 구체적 매매 전략 노트를 쓸 것. 예: "MA5>MA20>MA60 정배열 유지 중, 저항 추세선(약 OOO)과 피봇 저항선(약 OOO)이 겹치는 구간을 거래량 동반(ratio 1.5배 이상) 돌파하면 추가 매수, 지지 추세선(약 OOO) 이탈 시에는 거래량 증가 여부와 무관하게 비중 축소" 같이 구체적 가격·배수와 함께 서술할 것.
   - breakout_price에는 technical의 resistance_1(또는 prev_high, 저항 추세선 근접값) 등을 근거로 한 상승 돌파 대응 가격을, stop_loss_price에는 support_1(또는 prev_low, 지지 추세선 근접값) 등을 근거로 한 손절/비중조절 가격을 실제 숫자로 넣을 것. 근거가 부족하면 null로 둘 것(임의 추정 금지).
   - ticker 필드는 반드시 technical.domestic/technical.us의 키(종목코드 또는 티커)와 정확히 동일한 값을 사용할 것(예: "005930", "AAPL").
4. 현재 시장 상황(금리, 수급, 지수 흐름)에 맞는 맞춤형 금융 상품(섹터 ETF, 채권형 상품, MMF, 리츠 등)을 자산관리 전략과 함께 추천할 것.
5. portfolio_allocation.assets에 국내주식/미국주식/채권·MMF/리츠·대체투자/현금성자산 등 자산군별 추천 비중(percent, 정수)을 제시하고 percent 합계는 100이 되도록 할 것. 각 자산군의 representative_instruments에는 실제 존재하는 대표 종목/ETF명(예: KODEX 200, TIGER 미국S&P500, TLT 등)과 비중 조절 가이드를 구체적으로 명시할 것 - 카테고리명만 나열하지 말 것. rebalancing_strategy에는 현재 시장 상황에 맞춘 구체적 리밸런싱 전략을 서술할 것.
6. 아래 JSON 스키마와 정확히 동일한 키 구조로, 다른 어떤 텍스트도 없이 JSON 객체 하나만 출력할 것. recommended_stocks.domestic은 정확히 {DOMESTIC_RECOMMENDATION_COUNT}개, recommended_stocks.us는 정확히 {US_RECOMMENDATION_COUNT}개의 원소를 가질 것.

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

    client = OpenAI(api_key=api_key)
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
    )
    return response.text


def call_llm(system_prompt: str, user_prompt: str, provider: Optional[str] = None) -> str:
    resolved = _resolve_provider(provider)
    if resolved == "openai":
        return _call_openai(system_prompt, user_prompt)
    if resolved == "gemini":
        return _call_gemini(system_prompt, user_prompt)
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


def validate_report_schema(report: dict) -> None:
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in report]
    if missing:
        raise ValueError(f"리포트에 필수 키가 없습니다: {missing}")

    overview = report.get("market_overview", {})
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

    stocks = report.get("recommended_stocks", {})
    expected_counts = {"domestic": DOMESTIC_RECOMMENDATION_COUNT, "us": US_RECOMMENDATION_COUNT}
    for key, expected_count in expected_counts.items():
        items = stocks.get(key)
        if not isinstance(items, list) or len(items) != expected_count:
            raise ValueError(f"recommended_stocks.{key}는 정확히 {expected_count}개의 종목이어야 합니다.")
        for item in items:
            required_fields = {"name", "ticker", "reason", "buy_point", "risk", "breakout_price", "stop_loss_price"}
            if not required_fields.issubset(item):
                raise ValueError(f"recommended_stocks.{key} 항목에 누락된 필드가 있습니다: {item}")

    if not isinstance(report.get("news_impact_analysis"), list):
        raise ValueError("news_impact_analysis는 리스트여야 합니다.")
    if not isinstance(report.get("financial_products"), list):
        raise ValueError("financial_products는 리스트여야 합니다.")

    allocation = report.get("portfolio_allocation", {})
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


# ---------------------------------------------------------------------------
# 5. 저장
# ---------------------------------------------------------------------------

def save_report(report: dict, output_path: str = OUTPUT_PATH_DEFAULT) -> str:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return output_path


# ---------------------------------------------------------------------------
# 6. 종합 엔트리포인트
# ---------------------------------------------------------------------------

def generate_pb_report(
    target_date: DateLike = None,
    provider: Optional[str] = None,
    output_path: str = OUTPUT_PATH_DEFAULT,
    market_data: Optional[dict] = None,
) -> dict:
    """시장 데이터를 수집(또는 재사용)하고 AI로 Senior PB 리포트를 생성해 파일로 저장한다.

    Args:
        target_date: 리포트 기준일. None이면 오늘.
        provider: "openai" | "gemini". None이면 환경 변수로 자동 판단.
        output_path: 저장할 JSON 파일 경로.
        market_data: 이미 collect_market_data()로 수집한 결과가 있으면 재사용(중복 수집 방지).

    Returns:
        저장된 리포트 dict (meta, source_errors 포함).
    """
    resolved_date = _to_date(target_date)
    market_data = market_data or collect_market_data(resolved_date)
    context = build_prompt_context(market_data)

    system_prompt = SYSTEM_PROMPT
    user_prompt = build_user_prompt(context)

    last_error: Optional[Exception] = None
    report_body: Optional[dict] = None

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            raw_text = call_llm(system_prompt, user_prompt, provider=provider)
            parsed = _extract_json(raw_text)
            validate_report_schema(parsed)
            report_body = parsed
            break
        except Exception as exc:  # noqa: BLE001 - 재시도 후 최종 실패 시 위로 전파
            last_error = exc
            continue

    if report_body is None:
        raise RuntimeError(f"PB 리포트 생성에 {MAX_GENERATION_ATTEMPTS}회 실패했습니다: {last_error}")

    # 프론트엔드가 캔들차트 등을 그릴 수 있도록 원본 시장 데이터(전체 OHLCV/이평선/지수/거시지표 등)를 함께 저장.
    raw_market_data = {k: v for k, v in market_data.items() if k not in ("meta", "errors")}

    report = {
        "meta": {
            "target_date": resolved_date.isoformat(),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "ai_provider": _resolve_provider(provider),
        },
        **report_body,
        "market_data": raw_market_data,
        "source_data_errors": market_data.get("errors", []),
    }

    save_report(report, output_path)
    return report


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

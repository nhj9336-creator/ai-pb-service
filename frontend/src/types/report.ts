// 백엔드(main.py GET /api/pb-report)가 반환하는 pb_report_latest.json 구조와 1:1로 대응하는 타입.
// 각 소스는 부분 실패 시 null/빈 값이 될 수 있으므로 낙관적으로 단정하지 않는다.

export type PbStrategyOpinion = "매수" | "관망" | "비중축소";
export type SupplyDemandStatus = "매집" | "이탈" | "혼조" | "데이터없음";

export interface SupplyDemandDay {
  date: string;
  institution_net_buy: number | null;
  foreign_net_buy: number | null;
}

export interface SupplyDemand extends SupplyDemandDay {
  /** 최근 거래일들의 기관/외국인 순매수 시계열(매집 구간 분석용). */
  history: SupplyDemandDay[];
}

export interface IndexPricePoint {
  date: string;
  close: number | null;
}

export interface IndexSnapshot {
  date: string;
  close: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  /** 최근 거래일들의 종가 시계열(매집 구간 분석용). */
  price_history?: IndexPricePoint[];
  supply_demand?: SupplyDemand | null;
}

export interface PivotPoint {
  pivot: number | null;
  resistance_1: number | null;
  resistance_2: number | null;
  support_1: number | null;
  support_2: number | null;
}

export interface SupportResistance {
  prev_high_20d: number | null;
  prev_low_20d: number | null;
  prev_high_60d: number | null;
  prev_low_60d: number | null;
}

export interface TechnicalStock {
  name: string;
  dates: string[];
  open: (number | null)[];
  high: (number | null)[];
  low: (number | null)[];
  close: (number | null)[];
  volume: (number | null)[];
  ma5: (number | null)[];
  ma20: (number | null)[];
  ma60: (number | null)[];
  ma120: (number | null)[];
  pivot_point: PivotPoint;
  support_resistance: SupportResistance;
}

export interface MacroPoint {
  date: string;
  value: number | null;
}

export interface DartDisclosure {
  corp_name: string;
  report_nm: string;
  rcept_dt: string;
  rcept_no: string;
  url: string;
}

export interface NewsItem {
  title: string;
  link: string;
  published: string | null;
  source: string | null;
}

export interface MarketData {
  indices: Record<"KOSPI" | "KOSDAQ" | "SP500" | "NASDAQ", IndexSnapshot | null>;
  technical: {
    domestic: Record<string, TechnicalStock | null>;
    us: Record<string, TechnicalStock | null>;
  };
  macro: Record<"US_BASE_RATE" | "US_10Y_TREASURY" | "US_CPI", MacroPoint[] | null>;
  dart_disclosures: Record<string, DartDisclosure[]>;
  news: Record<string, NewsItem[]>;
}

export interface NewsImpactAnalysis {
  headline: string;
  summary: string;
  impact: string;
  affected_sectors: string[];
}

export interface StockRecommendation {
  name: string;
  ticker: string;
  reason: string;
  buy_point: string;
  breakout_price: number | null;
  stop_loss_price: number | null;
  risk: string;
}

export interface FinancialProduct {
  type: string;
  name: string;
  description: string;
  allocation_reason: string;
}

export interface PortfolioAsset {
  name: string;
  percent: number;
  representative_instruments: string;
}

export interface PortfolioAllocation {
  assets: PortfolioAsset[];
  rebalancing_strategy: string;
}

export interface PbReport {
  meta: {
    target_date: string;
    generated_at: string;
    ai_provider: string;
  };
  market_overview: {
    summary: string;
    pb_strategy_opinion: PbStrategyOpinion;
    strategy_rationale: string;
    supply_demand_status: SupplyDemandStatus;
    supply_demand_analysis: string;
    intraday_playbook: string;
  };
  news_impact_analysis: NewsImpactAnalysis[];
  recommended_stocks: {
    domestic: StockRecommendation[];
    us: StockRecommendation[];
  };
  financial_products: FinancialProduct[];
  portfolio_allocation: PortfolioAllocation;
  disclaimer: string;
  market_data: MarketData;
  source_data_errors: string[];
}

// StockChart에서 국내/미국 종목을 함께 다루기 위한 공통 선택 상태
export interface SelectedStock {
  market: "domestic" | "us";
  ticker: string;
  recommendation: StockRecommendation;
}

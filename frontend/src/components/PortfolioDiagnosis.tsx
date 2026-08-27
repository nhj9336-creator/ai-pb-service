"use client";

import { useState } from "react";
import SectionCard from "./SectionCard";
import StockPriceChart from "./StockPriceChart";
import { ApiError, diagnoseStockHolding } from "@/lib/api";
import { changeColorClass, formatKrw, formatNumber, formatPercent, formatUsd } from "@/lib/format";
import type { DiagnosisStrategy, RiskLevel, StockDiagnosisResponse } from "@/types/report";

const RISK_STYLES: Record<RiskLevel, string> = {
  낮음: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  보통: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  높음: "bg-rose-500/15 text-rose-400 border-rose-500/40",
};

const STRATEGY_META: { key: "day_trading" | "swing" | "long_term"; label: string; horizon: string }[] = [
  { key: "day_trading", label: "단타", horizon: "당일 ~ 수일" },
  { key: "swing", label: "스윙", horizon: "수주 ~ 수개월" },
  { key: "long_term", label: "장기투자", horizon: "수개월 이상" },
];

function StrategyCard({
  label,
  horizon,
  strategy,
}: {
  label: string;
  horizon: string;
  strategy: DiagnosisStrategy;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-surface-elevated p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-foreground">{label}</p>
          <p className="text-[11px] text-muted">{horizon}</p>
        </div>
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${RISK_STYLES[strategy.risk_level]}`}
        >
          리스크 {strategy.risk_level}
        </span>
      </div>
      <p className="text-[13px] leading-relaxed text-foreground/90">{strategy.action}</p>
      <p className="mt-2 text-[13px] leading-relaxed text-muted">
        <span className="font-medium text-foreground/70">위험 대비 기대수익 — </span>
        {strategy.risk_reward_note}
      </p>
    </div>
  );
}

export default function PortfolioDiagnosis() {
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<"domestic" | "us">("domestic");
  const [quantity, setQuantity] = useState("");
  const [avgPrice, setAvgPrice] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StockDiagnosisResponse | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const quantityNum = Number(quantity);
    const avgPriceNum = Number(avgPrice);
    if (!query.trim()) {
      setError("종목명 또는 종목코드를 입력해 주세요.");
      return;
    }
    if (!(quantityNum > 0) || !(avgPriceNum > 0)) {
      setError("보유 수량과 매수 평균단가는 0보다 큰 숫자로 입력해 주세요.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const diagnosis = await diagnoseStockHolding({
        query: query.trim(),
        market,
        quantity: quantityNum,
        avgPrice: avgPriceNum,
      });
      setResult(diagnosis);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "진단 생성에 실패했습니다.");
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const formatPrice = result?.holding.market === "us" ? formatUsd : formatKrw;

  return (
    <SectionCard title="보유 종목 맞춤 PB 진단" icon={<span>🩺</span>}>
      <p className="mb-4 text-xs leading-relaxed text-muted">
        보유 중인 종목의 수량과 평균단가를 입력하면, 위 인터랙티브 차트와 동일한 방식으로 시각화하고
        수익률·투자 호흡별(단타/스윙/장기) 대응 전략을 진단해드립니다. 진단은 오늘의 시장 총평 스탠스와
        모순되지 않도록 연결됩니다.
      </p>

      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">시장</span>
          <select
            value={market}
            onChange={(e) => setMarket(e.target.value as "domestic" | "us")}
            className="rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          >
            <option value="domestic">국내</option>
            <option value="us">미국</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">종목명 / 종목코드</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={market === "domestic" ? "예: 삼성전자 또는 005930" : "예: AAPL"}
            className="w-44 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">보유 수량</span>
          <input
            type="number"
            min="0"
            step="any"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder="10"
            className="w-28 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">매수 평균단가</span>
          <input
            type="number"
            min="0"
            step="any"
            value={avgPrice}
            onChange={(e) => setAvgPrice(e.target.value)}
            placeholder={market === "domestic" ? "70000" : "180.5"}
            className="w-32 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-accent px-5 py-2 text-sm font-semibold text-[#04121c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "진단 중..." : "진단하기"}
        </button>
      </form>

      {error && (
        <div className="mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-5 space-y-4 border-t border-border/60 pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/60 bg-surface-elevated p-3">
            <div>
              <span className="text-sm font-semibold text-foreground">
                {result.holding.name} ({result.holding.ticker})
              </span>
              <span className="ml-2 text-xs text-muted">
                {formatNumber(result.holding.quantity, 0)}주 · 평단 {formatPrice(result.holding.avg_price)}
              </span>
            </div>
            <div className="text-right">
              <div className={`text-base font-semibold ${changeColorClass(result.holding.pnl_amount)}`}>
                {formatPrice(result.holding.pnl_amount)} ({formatPercent(result.holding.pnl_pct)})
              </div>
              <div className="text-xs text-muted">평가금액 {formatPrice(result.holding.position_value)}</div>
            </div>
          </div>

          <StockPriceChart
            name={result.technical.name}
            ticker={result.holding.ticker}
            market={result.holding.market}
            stock={result.technical}
          />

          <div className="rounded-lg border border-accent/30 bg-accent/5 p-3">
            <p className="mb-1 text-xs font-semibold text-accent">수익률 진단</p>
            <p className="text-sm leading-relaxed text-foreground/90">{result.diagnosis.profit_diagnosis}</p>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {STRATEGY_META.map((meta) => (
              <StrategyCard
                key={meta.key}
                label={meta.label}
                horizon={meta.horizon}
                strategy={result.diagnosis.strategies[meta.key]}
              />
            ))}
          </div>

          <div className="rounded-lg border border-border/60 bg-surface-elevated p-3">
            <p className="mb-1 text-xs font-semibold text-foreground/70">시장 총평과의 연결</p>
            <p className="text-[13px] leading-relaxed text-muted">{result.diagnosis.market_consistency_note}</p>
          </div>
        </div>
      )}
    </SectionCard>
  );
}

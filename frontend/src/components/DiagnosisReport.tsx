import type { DiagnosisStrategy, RiskLevel, StockDiagnosisResponse } from "@/types/report";
import { changeColorClass, formatKrw, formatNumber, formatPercent, formatUsd } from "@/lib/format";

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

/** 보유 종목 진단 결과(수익률 배너 + 단타/스윙/장기 전략 카드 + 시장 총평 연결)만 렌더링한다.
 * 차트는 상단 인터랙티브 차트가 대신 보여주므로(StockPriceChart로 스위칭) 여기서는 다루지 않는다. */
export default function DiagnosisReport({ result }: { result: StockDiagnosisResponse }) {
  const formatPrice = result.holding.market === "us" ? formatUsd : formatKrw;

  return (
    <div className="space-y-4">
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
  );
}

import type { SelectedStock, StockRecommendation } from "@/types/report";
import { formatKrw, formatUsd } from "@/lib/format";

interface StockCardProps {
  market: "domestic" | "us";
  recommendation: StockRecommendation;
  isSelected: boolean;
  onSelect: (selection: SelectedStock) => void;
}

function StockCard({ market, recommendation, isSelected, onSelect }: StockCardProps) {
  const formatPrice = market === "domestic" ? formatKrw : formatUsd;
  return (
    <button
      type="button"
      onClick={() => onSelect({ market, ticker: recommendation.ticker, recommendation })}
      className={`w-full rounded-xl border p-4 text-left transition-colors ${
        isSelected
          ? "border-accent bg-accent/10"
          : "border-border bg-surface-elevated hover:border-accent/50"
      }`}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-semibold text-foreground">{recommendation.name}</span>
        <span className="text-xs text-muted">{recommendation.ticker}</span>
      </div>
      <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-foreground/80">{recommendation.reason}</p>

      {(recommendation.breakout_price !== null || recommendation.stop_loss_price !== null) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {recommendation.breakout_price !== null && (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-400">
              ▲ 돌파 대응 {formatPrice(recommendation.breakout_price)}
            </span>
          )}
          {recommendation.stop_loss_price !== null && (
            <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[11px] font-semibold text-rose-400">
              ▼ 손절 기준 {formatPrice(recommendation.stop_loss_price)}
            </span>
          )}
        </div>
      )}

      <div className="mt-3 space-y-1 text-[11px] text-muted">
        <p>
          <span className="font-medium text-foreground/70">매수 관전 포인트</span> {recommendation.buy_point}
        </p>
        <p>
          <span className="font-medium text-foreground/70">투자 리스크</span> {recommendation.risk}
        </p>
      </div>
    </button>
  );
}

interface RecommendedStocksProps {
  domestic: StockRecommendation[];
  us: StockRecommendation[];
  selected: SelectedStock | null;
  onSelect: (selection: SelectedStock) => void;
}

export default function RecommendedStocks({ domestic, us, selected, onSelect }: RecommendedStocksProps) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-muted">
          <span>🇰🇷</span> 국내 유망 종목
        </h3>
        <div className="space-y-3">
          {domestic.map((rec) => (
            <StockCard
              key={rec.ticker}
              market="domestic"
              recommendation={rec}
              isSelected={selected?.market === "domestic" && selected.ticker === rec.ticker}
              onSelect={onSelect}
            />
          ))}
        </div>
      </div>
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-muted">
          <span>🇺🇸</span> 미국 유망 종목
        </h3>
        <div className="space-y-3">
          {us.map((rec) => (
            <StockCard
              key={rec.ticker}
              market="us"
              recommendation={rec}
              isSelected={selected?.market === "us" && selected.ticker === rec.ticker}
              onSelect={onSelect}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

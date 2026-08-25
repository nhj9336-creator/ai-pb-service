import type { SelectedStock, StockRecommendation } from "@/types/report";

interface StockCardProps {
  market: "domestic" | "us";
  recommendation: StockRecommendation;
  isSelected: boolean;
  onSelect: (selection: SelectedStock) => void;
}

function StockCard({ market, recommendation, isSelected, onSelect }: StockCardProps) {
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
      <div className="mt-3 space-y-1 text-[11px] text-muted">
        <p>
          <span className="font-medium text-emerald-400">매수 관전 포인트</span> {recommendation.buy_point}
        </p>
        <p>
          <span className="font-medium text-rose-400">투자 리스크</span> {recommendation.risk}
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

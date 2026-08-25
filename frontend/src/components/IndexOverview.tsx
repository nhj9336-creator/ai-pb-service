import SectionCard from "./SectionCard";
import type { IndexSnapshot, MacroPoint, MarketData } from "@/types/report";
import { changeColorClass, formatCompactAmount, formatNumber, formatPercent } from "@/lib/format";

function IndexRow({ label, snapshot }: { label: string; snapshot: IndexSnapshot | null }) {
  if (!snapshot) {
    return (
      <div className="flex items-center justify-between border-b border-border/60 py-2 last:border-0">
        <span className="text-sm text-muted">{label}</span>
        <span className="text-sm text-muted">데이터 없음</span>
      </div>
    );
  }
  const sd = snapshot.supply_demand;
  return (
    <div className="border-b border-border/60 py-2 last:border-0">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">{label}</span>
        <div className="text-right">
          <div className="text-sm font-semibold text-foreground">{formatNumber(snapshot.close, 2)}</div>
          <div className={`text-xs ${changeColorClass(snapshot.change_pct)}`}>
            {formatPercent(snapshot.change_pct)}
          </div>
        </div>
      </div>
      {sd && (sd.institution_net_buy !== null || sd.foreign_net_buy !== null) && (
        <div className="mt-1 flex justify-end gap-3 text-[11px] text-muted">
          <span>
            기관 <span className={changeColorClass(sd.institution_net_buy)}>{formatCompactAmount(sd.institution_net_buy)}</span>
          </span>
          <span>
            외인 <span className={changeColorClass(sd.foreign_net_buy)}>{formatCompactAmount(sd.foreign_net_buy)}</span>
          </span>
        </div>
      )}
    </div>
  );
}

function MacroRow({ label, series, unit }: { label: string; series: MacroPoint[] | null; unit: string }) {
  if (!series || series.length === 0) {
    return (
      <div className="flex items-center justify-between border-b border-border/60 py-2 last:border-0">
        <span className="text-sm text-muted">{label}</span>
        <span className="text-sm text-muted">데이터 없음</span>
      </div>
    );
  }
  const latest = series[series.length - 1];
  const prev = series.length >= 2 ? series[series.length - 2] : null;
  const delta = prev && latest.value !== null && prev.value !== null ? latest.value - prev.value : null;
  return (
    <div className="flex items-center justify-between border-b border-border/60 py-2 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <div className="text-right">
        <div className="text-sm font-semibold text-foreground">
          {formatNumber(latest.value, 2)}
          {unit}
        </div>
        {delta !== null && (
          <div className={`text-xs ${changeColorClass(delta)}`}>
            전기 대비 {delta > 0 ? "+" : ""}
            {formatNumber(delta, 2)}
            {unit}
          </div>
        )}
      </div>
    </div>
  );
}

export default function IndexOverview({ marketData }: { marketData: MarketData }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <SectionCard title="국내 증시" icon={<span>🇰🇷</span>}>
        <IndexRow label="KOSPI" snapshot={marketData.indices?.KOSPI ?? null} />
        <IndexRow label="KOSDAQ" snapshot={marketData.indices?.KOSDAQ ?? null} />
      </SectionCard>

      <SectionCard title="미국 증시" icon={<span>🇺🇸</span>}>
        <IndexRow label="S&P 500" snapshot={marketData.indices?.SP500 ?? null} />
        <IndexRow label="NASDAQ" snapshot={marketData.indices?.NASDAQ ?? null} />
      </SectionCard>

      <SectionCard title="FRED 거시경제" icon={<span>💵</span>}>
        <MacroRow label="기준금리(FEDFUNDS)" series={marketData.macro?.US_BASE_RATE ?? null} unit="%" />
        <MacroRow label="美 10년물 국채" series={marketData.macro?.US_10Y_TREASURY ?? null} unit="%" />
        <MacroRow label="CPI(전체 항목)" series={marketData.macro?.US_CPI ?? null} unit="" />
      </SectionCard>
    </div>
  );
}

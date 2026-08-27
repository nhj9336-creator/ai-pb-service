"use client";

import { useCallback, useState } from "react";
import SectionCard from "./SectionCard";
import StrategyBadge from "./StrategyBadge";
import SupplyDemandBadge from "./SupplyDemandBadge";
import NewsImpact from "./NewsImpact";
import DartAnnouncements from "./DartAnnouncements";
import type { PbReport } from "@/types/report";

const SUPPLY_DEMAND_ACCENT: Record<string, string> = {
  매집: "border-l-emerald-500",
  이탈: "border-l-rose-500",
  혼조: "border-l-amber-500",
  데이터없음: "border-l-border",
};

export default function CentralReport({ report }: { report: PbReport }) {
  const { market_overview } = report;
  const accent = SUPPLY_DEMAND_ACCENT[market_overview.supply_demand_status] ?? "border-l-border";
  // DART 공시 카드가 뉴스 카드와 정확히 같은 높이가 되도록, 뉴스 카드가 측정한 실제 렌더
  // 높이를 그대로 전달한다.
  const [newsCardHeight, setNewsCardHeight] = useState<number | null>(null);
  const handleNewsHeightChange = useCallback((height: number) => setNewsCardHeight(height), []);
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <SectionCard title="시장 총평" icon={<span>🧭</span>} className="xl:col-span-2">
        <div className="mb-3 flex flex-wrap gap-2">
          <StrategyBadge opinion={market_overview.pb_strategy_opinion} />
          <SupplyDemandBadge status={market_overview.supply_demand_status} />
        </div>
        <p className="text-sm leading-relaxed text-foreground/90">{market_overview.summary}</p>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          <span className="font-medium text-foreground/80">전략 근거 — </span>
          {market_overview.strategy_rationale}
        </p>
        <div className={`mt-4 rounded-lg border border-border/60 border-l-4 ${accent} bg-surface-elevated p-3`}>
          <p className="mb-1 text-xs font-semibold text-muted">수급 브리핑 · 매집/이탈 구간</p>
          <p className="text-sm leading-relaxed text-foreground/90">{market_overview.supply_demand_analysis}</p>
        </div>
        <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
          <p className="mb-1 text-xs font-semibold text-accent">장중 대응 시나리오</p>
          <p className="text-sm leading-relaxed text-foreground/90">{market_overview.intraday_playbook}</p>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3">
            <p className="mb-1 text-xs font-semibold text-emerald-400">Option A · 상방 돌파 시 계좌 대응</p>
            <p className="text-sm leading-relaxed text-foreground/90">{market_overview.account_scenario_bullish}</p>
          </div>
          <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-3">
            <p className="mb-1 text-xs font-semibold text-rose-400">Option B · 하방 이탈 시 계좌 대응</p>
            <p className="text-sm leading-relaxed text-foreground/90">{market_overview.account_scenario_bearish}</p>
          </div>
        </div>
      </SectionCard>

      <NewsImpact items={report.news_impact_analysis} onHeightChange={handleNewsHeightChange} />
      <DartAnnouncements dart={report.market_data.dart_disclosures} matchHeight={newsCardHeight} />
    </div>
  );
}

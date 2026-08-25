import SectionCard from "./SectionCard";
import StrategyBadge from "./StrategyBadge";
import type { DartDisclosure, MarketData, NewsImpactAnalysis, PbReport } from "@/types/report";
import { formatDateLabel } from "@/lib/format";

function DartList({ dart }: { dart: MarketData["dart_disclosures"] }) {
  const items: (DartDisclosure & { code: string })[] = Object.entries(dart ?? {}).flatMap(
    ([code, list]) => (list ?? []).map((item) => ({ ...item, code }))
  );

  if (items.length === 0) {
    return <p className="text-sm text-muted">최근 주요 공시가 없습니다.</p>;
  }

  return (
    <ul className="space-y-2">
      {items.slice(0, 8).map((item) => (
        <li key={`${item.code}-${item.rcept_no}`} className="border-b border-border/60 pb-2 last:border-0">
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-foreground hover:text-accent"
          >
            [{item.corp_name}] {item.report_nm}
          </a>
          <div className="text-xs text-muted">{formatDateLabel(item.rcept_dt)}</div>
        </li>
      ))}
    </ul>
  );
}

function NewsImpactList({ items }: { items: NewsImpactAnalysis[] }) {
  if (!items || items.length === 0) {
    return <p className="text-sm text-muted">분석된 주요 뉴스가 없습니다.</p>;
  }
  return (
    <ul className="space-y-3">
      {items.map((item, idx) => (
        <li key={idx} className="rounded-lg border border-border/60 bg-surface-elevated p-3">
          <p className="text-sm font-medium text-foreground">{item.headline}</p>
          <p className="mt-1 text-xs text-muted">{item.summary}</p>
          <p className="mt-2 text-sm text-foreground/90">{item.impact}</p>
          {item.affected_sectors?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {item.affected_sectors.map((sector) => (
                <span
                  key={sector}
                  className="rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent"
                >
                  {sector}
                </span>
              ))}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function CentralReport({ report }: { report: PbReport }) {
  const { market_overview } = report;
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <SectionCard title="AI PB 시장 총평" icon={<span>🧭</span>} className="xl:col-span-2">
        <div className="mb-3">
          <StrategyBadge opinion={market_overview.pb_strategy_opinion} />
        </div>
        <p className="text-sm leading-relaxed text-foreground/90">{market_overview.summary}</p>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          <span className="font-medium text-foreground/80">전략 근거: </span>
          {market_overview.strategy_rationale}
        </p>
        <div className="mt-4 rounded-lg border border-border/60 bg-surface-elevated p-3">
          <p className="mb-1 text-xs font-semibold text-muted">기관/외인 수급 해석</p>
          <p className="text-sm leading-relaxed text-foreground/90">{market_overview.supply_demand_analysis}</p>
        </div>
      </SectionCard>

      <SectionCard title="뉴스 파급력 분석" icon={<span>📰</span>}>
        <NewsImpactList items={report.news_impact_analysis} />
      </SectionCard>

      <SectionCard title="DART 주요 공시" icon={<span>📑</span>}>
        <DartList dart={report.market_data.dart_disclosures} />
      </SectionCard>
    </div>
  );
}

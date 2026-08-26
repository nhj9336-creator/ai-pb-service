"use client";

import { useState } from "react";
import SectionCard from "./SectionCard";
import type { NewsImpactAnalysis } from "@/types/report";

const DEFAULT_VISIBLE = 3;

export default function NewsImpact({ items }: { items: NewsImpactAnalysis[] }) {
  const [expanded, setExpanded] = useState(false);

  if (!items || items.length === 0) {
    return (
      <SectionCard title="뉴스 파급력 분석" icon={<span>📰</span>}>
        <p className="text-sm text-muted">분석된 주요 뉴스가 없습니다.</p>
      </SectionCard>
    );
  }

  const visible = expanded ? items : items.slice(0, DEFAULT_VISIBLE);

  return (
    <SectionCard title="뉴스 파급력 분석" icon={<span>📰</span>} subtitle={`총 ${items.length}건`}>
      <ul className="space-y-3">
        {visible.map((item, idx) => (
          <li key={idx} className="rounded-lg border border-border/60 bg-surface-elevated p-3">
            <p className="text-sm font-medium text-foreground">{item.headline}</p>
            <p className="mt-1 text-xs text-muted">{item.summary}</p>
            <p className="mt-2 text-sm text-foreground/90">{item.impact}</p>
            {item.affected_sectors?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {item.affected_sectors.map((sector) => (
                  <span key={sector} className="rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
                    {sector}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>

      {items.length > DEFAULT_VISIBLE && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-3 w-full rounded-lg border border-dashed border-border py-1.5 text-[11px] font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
        >
          {expanded ? "뉴스 접기" : `뉴스 더보기 (전체 ${items.length}개 보기)`}
        </button>
      )}
    </SectionCard>
  );
}

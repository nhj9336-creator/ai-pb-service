"use client";

import { useState } from "react";
import SectionCard from "./SectionCard";
import type { DartDisclosure, MarketData } from "@/types/report";
import { formatDateLabel } from "@/lib/format";

const DEFAULT_VISIBLE = 3;
const MAX_VISIBLE = 10;

export default function DartAnnouncements({ dart }: { dart: MarketData["dart_disclosures"] }) {
  const [expanded, setExpanded] = useState(false);

  const items: (DartDisclosure & { code: string })[] = Object.entries(dart ?? {})
    .flatMap(([code, list]) => (list ?? []).map((item) => ({ ...item, code })))
    // 여러 종목의 공시를 한데 모았으므로 최신순으로 재정렬해 가장 최근 공시부터 보여준다.
    .sort((a, b) => (a.rcept_dt < b.rcept_dt ? 1 : a.rcept_dt > b.rcept_dt ? -1 : 0))
    .slice(0, MAX_VISIBLE);

  if (items.length === 0) {
    return (
      <SectionCard title="DART 주요 공시" icon={<span>📑</span>}>
        <p className="text-sm text-muted">최근 주요 공시가 없습니다.</p>
      </SectionCard>
    );
  }

  const visible = expanded ? items : items.slice(0, DEFAULT_VISIBLE);

  return (
    <SectionCard title="DART 주요 공시" icon={<span>📑</span>} subtitle={`총 ${items.length}건`}>
      <ul className="space-y-2">
        {visible.map((item) => (
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

      {items.length > DEFAULT_VISIBLE && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-3 w-full rounded-lg border border-dashed border-border py-1.5 text-[11px] font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
        >
          {expanded ? "공시 접기" : `공시 더보기 (전체 ${items.length}개 보기)`}
        </button>
      )}
    </SectionCard>
  );
}

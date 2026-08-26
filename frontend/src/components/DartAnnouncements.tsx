"use client";

import { useEffect, useRef, useState } from "react";
import SectionCard from "./SectionCard";
import type { DartDisclosure, MarketData } from "@/types/report";
import { formatDateLabel } from "@/lib/format";

// 화면에 노출 가능한 전체 공시 상한(그리드 옆 뉴스 카드와 동일 행 높이로 자동으로 채워지고,
// 넘치는 항목만 "더보기"로 열람 - 고정된 3개 제한 대신 실제 렌더 높이로 넘침 여부를 판단한다).
const MAX_VISIBLE = 30;

export default function DartAnnouncements({ dart }: { dart: MarketData["dart_disclosures"] }) {
  const [expanded, setExpanded] = useState(false);
  const [hasOverflow, setHasOverflow] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const items: (DartDisclosure & { code: string })[] = Object.entries(dart ?? {})
    .flatMap(([code, list]) => (list ?? []).map((item) => ({ ...item, code })))
    // 여러 종목의 공시를 한데 모았으므로 최신순으로 재정렬해 가장 최근 공시부터 보여준다.
    .sort((a, b) => (a.rcept_dt < b.rcept_dt ? 1 : a.rcept_dt > b.rcept_dt ? -1 : 0))
    .slice(0, MAX_VISIBLE);

  useEffect(() => {
    const el = listRef.current;
    if (!el || expanded) return;

    const checkOverflow = () => setHasOverflow(el.scrollHeight > el.clientHeight + 1);
    checkOverflow();

    window.addEventListener("resize", checkOverflow);
    return () => window.removeEventListener("resize", checkOverflow);
  }, [items, expanded]);

  if (items.length === 0) {
    return (
      <SectionCard title="DART 주요 공시" icon={<span>📑</span>}>
        <p className="text-sm text-muted">최근 주요 공시가 없습니다.</p>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="DART 주요 공시"
      icon={<span>📑</span>}
      subtitle={`총 ${items.length}건`}
      className="flex h-full flex-col"
    >
      {/* min-h-0 + overflow-hidden 조합으로 옆 뉴스 카드와 같은 행 높이(그리드 stretch)만큼만
          자연스럽게 채우고, 넘치는 항목은 더보기 클릭 전까지 시각적으로만 숨긴다. */}
      <div ref={listRef} className={`relative min-h-0 flex-1 ${expanded ? "" : "overflow-hidden"}`}>
        <ul className="space-y-2">
          {items.map((item) => (
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
        {!expanded && hasOverflow && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-surface to-transparent" />
        )}
      </div>

      {(hasOverflow || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-3 w-full shrink-0 rounded-lg border border-dashed border-border py-1.5 text-[11px] font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
        >
          {expanded ? "공시 접기" : `공시 더보기 (전체 ${items.length}개 보기)`}
        </button>
      )}
    </SectionCard>
  );
}

"use client";

import { useState } from "react";
import type { SelectedStock, StockRecommendation } from "@/types/report";
import { formatKrw, formatUsd } from "@/lib/format";

const DEFAULT_VISIBLE_COUNT = 3;

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

      <div className="mt-3 space-y-2.5">
        <div>
          <p className="mb-1 text-xs font-semibold text-foreground/70">매수 관전 포인트</p>
          <p className="text-[13px] leading-relaxed text-muted">{recommendation.buy_point}</p>
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-foreground/70">투자 리스크</p>
          <p className="text-[13px] leading-relaxed text-muted">{recommendation.risk}</p>
        </div>
      </div>
    </button>
  );
}

interface StockGroupProps {
  title: string;
  flag: string;
  market: "domestic" | "us";
  recommendations: StockRecommendation[];
  selected: SelectedStock | null;
  onSelect: (selection: SelectedStock) => void;
}

function StockGroup({ title, flag, market, recommendations, selected, onSelect }: StockGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const visible = recommendations.slice(0, DEFAULT_VISIBLE_COUNT);
  const rest = recommendations.slice(DEFAULT_VISIBLE_COUNT);

  return (
    <div>
      <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-muted">
        <span>{flag}</span> {title}
      </h3>
      <div className="space-y-3">
        {visible.map((rec) => (
          <StockCard
            key={rec.ticker}
            market={market}
            recommendation={rec}
            isSelected={selected?.market === market && selected.ticker === rec.ticker}
            onSelect={onSelect}
          />
        ))}
      </div>

      {rest.length > 0 && (
        <>
          <div
            className={`grid transition-[grid-template-rows] duration-300 ease-in-out ${
              expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
            }`}
          >
            <div className="space-y-3 overflow-hidden pt-3">
              {rest.map((rec) => (
                <StockCard
                  key={rec.ticker}
                  market={market}
                  recommendation={rec}
                  isSelected={selected?.market === market && selected.ticker === rec.ticker}
                  onSelect={onSelect}
                />
              ))}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-3 w-full rounded-lg border border-dashed border-border py-2 text-xs font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
          >
            {expanded ? "접기" : `추천 종목 더보기 (전체 ${recommendations.length}개 보기)`}
          </button>
        </>
      )}
    </div>
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
      <StockGroup
        title="국내 유망 종목"
        flag="🇰🇷"
        market="domestic"
        recommendations={domestic}
        selected={selected}
        onSelect={onSelect}
      />
      <StockGroup
        title="미국 유망 종목"
        flag="🇺🇸"
        market="us"
        recommendations={us}
        selected={selected}
        onSelect={onSelect}
      />
    </div>
  );
}

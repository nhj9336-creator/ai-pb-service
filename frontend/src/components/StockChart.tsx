"use client";

import EntrySignalBadge from "./EntrySignalBadge";
import StockPriceChart, { buildTechnicalNote } from "./StockPriceChart";
import type { SelectedStock, TechnicalStock } from "@/types/report";
import { formatKrw, formatUsd } from "@/lib/format";

interface StockChartProps {
  selection: SelectedStock;
  stock: TechnicalStock | null | undefined;
}

/** 추천 종목 상세 뷰: 공용 차트(StockPriceChart) + 추천 전용 PB 대응 노트(진입 시그널/매수
 * 관전 포인트)를 함께 보여준다. */
export default function StockChart({ selection, stock }: StockChartProps) {
  const formatPrice = selection.market === "domestic" ? formatKrw : formatUsd;

  if (!stock) {
    return (
      <div className="rounded-xl border border-border/60 bg-surface-elevated p-6 text-center text-sm text-muted">
        {selection.recommendation.name}({selection.ticker})의 차트 데이터를 불러올 수 없습니다.
      </div>
    );
  }

  const { recommendation } = selection;

  return (
    <div>
      <StockPriceChart
        name={stock.name}
        ticker={selection.ticker}
        market={selection.market}
        stock={stock}
        headerExtra={<EntrySignalBadge signal={recommendation.entry_signal} className="ml-2" />}
      />

      <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
        <p className="mb-1 text-xs font-semibold text-accent">PB 대응 노트</p>
        <p className="text-sm leading-relaxed text-foreground/90">{buildTechnicalNote(stock)}</p>

        {(recommendation.entry_price_low !== null ||
          recommendation.entry_price_high !== null ||
          recommendation.breakout_price !== null ||
          recommendation.stop_loss_price !== null) && (
          <div className="mt-2 flex flex-wrap gap-2">
            {(recommendation.entry_price_low !== null || recommendation.entry_price_high !== null) && (
              <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[11px] font-semibold text-accent">
                ◎ 진입 범위 {formatPrice(recommendation.entry_price_low)} ~ {formatPrice(recommendation.entry_price_high)}
              </span>
            )}
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

        <div className="mt-3">
          <p className="mb-1 text-xs font-semibold text-foreground/70">진입 시그널 근거</p>
          <p className="text-sm leading-relaxed text-muted">{recommendation.entry_signal_reason}</p>
        </div>
        <div className="mt-3">
          <p className="mb-1 text-xs font-semibold text-foreground/70">매수 관전 포인트</p>
          <p className="text-sm leading-relaxed text-muted">{recommendation.buy_point}</p>
        </div>
      </div>
    </div>
  );
}

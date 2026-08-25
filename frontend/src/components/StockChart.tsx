"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import type { SelectedStock, TechnicalStock } from "@/types/report";
import { formatKrw, formatNumber, formatUsd } from "@/lib/format";

interface StockChartProps {
  selection: SelectedStock;
  stock: TechnicalStock | null | undefined;
}

const MA_LINES: { key: "ma5" | "ma20" | "ma60" | "ma120"; label: string; color: string }[] = [
  { key: "ma5", label: "MA5", color: "#facc15" },
  { key: "ma20", label: "MA20", color: "#38bdf8" },
  { key: "ma60", label: "MA60", color: "#a78bfa" },
  { key: "ma120", label: "MA120", color: "#94a3b8" },
];

function buildTechnicalNote(stock: TechnicalStock): string {
  const close = stock.close[stock.close.length - 1];
  const { pivot, resistance_1, support_1 } = stock.pivot_point;
  if (close === null || pivot === null || resistance_1 === null || support_1 === null) {
    return "기술적 지표를 계산하기에 데이터가 부족합니다.";
  }
  if (close >= resistance_1) {
    return `현재가(${formatNumber(close, 2)})가 1차 저항선(${formatNumber(
      resistance_1,
      2
    )}) 위에서 거래되고 있어 추가 상승 시 2차 저항선 부근에서 분할 매도 관점이 유효합니다.`;
  }
  if (close <= support_1) {
    return `현재가(${formatNumber(close, 2)})가 1차 지지선(${formatNumber(
      support_1,
      2
    )}) 아래로 이탈해 있어, 추세 전환 신호를 확인한 뒤 저가 매수를 고려할 구간입니다.`;
  }
  if (close >= pivot) {
    return `현재가가 피봇(${formatNumber(pivot, 2)})과 1차 저항선(${formatNumber(
      resistance_1,
      2
    )}) 사이에 위치해 단기 상승 우위 구간이며, 저항선 돌파 여부를 관전 포인트로 삼을 만합니다.`;
  }
  return `현재가가 피봇(${formatNumber(pivot, 2)})과 1차 지지선(${formatNumber(
    support_1,
    2
  )}) 사이에 위치해 단기 조정 국면으로, 지지선 이탈 여부를 확인하며 대응하는 것이 유효합니다.`;
}

export default function StockChart({ selection, stock }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const formatPrice = selection.market === "domestic" ? formatKrw : formatUsd;

  useEffect(() => {
    if (!containerRef.current || !stock) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#101625" },
        textColor: "#8891a7",
      },
      grid: {
        vertLines: { color: "#1c2436" },
        horzLines: { color: "#1c2436" },
      },
      rightPriceScale: { borderColor: "#232c40" },
      timeScale: { borderColor: "#232c40" },
      width: containerRef.current.clientWidth,
      height: 380,
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#fb7185",
      downColor: "#60a5fa",
      borderVisible: false,
      wickUpColor: "#fb7185",
      wickDownColor: "#60a5fa",
    });

    const candleData = stock.dates
      .map((date, i) => ({
        time: date as Time,
        open: stock.open[i],
        high: stock.high[i],
        low: stock.low[i],
        close: stock.close[i],
      }))
      .filter((d) => d.open !== null && d.high !== null && d.low !== null && d.close !== null) as {
      time: Time;
      open: number;
      high: number;
      low: number;
      close: number;
    }[];
    candleSeries.setData(candleData);

    for (const ma of MA_LINES) {
      const series = chart.addSeries(LineSeries, {
        color: ma.color,
        lineWidth: 1,
        title: ma.label,
      });
      const lineData = stock.dates
        .map((date, i) => ({ time: date as Time, value: stock[ma.key][i] }))
        .filter((d) => d.value !== null) as { time: Time; value: number }[];
      series.setData(lineData);
    }

    const { pivot_point } = stock;
    const priceLineSpecs: { price: number | null; title: string; color: string }[] = [
      { price: pivot_point.resistance_2, title: "저항선 R2", color: "#fb7185" },
      { price: pivot_point.resistance_1, title: "저항선 R1", color: "#f97316" },
      { price: pivot_point.support_1, title: "지지선 S1", color: "#34d399" },
      { price: pivot_point.support_2, title: "지지선 S2", color: "#22c55e" },
    ];
    for (const spec of priceLineSpecs) {
      if (spec.price === null) continue;
      candleSeries.createPriceLine({
        price: spec.price,
        color: spec.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: spec.title,
      });
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) {
        chart.resize(containerRef.current.clientWidth, 380);
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [stock, selection.market]);

  if (!stock) {
    return (
      <div className="rounded-xl border border-border/60 bg-surface-elevated p-6 text-center text-sm text-muted">
        {selection.recommendation.name}({selection.ticker})의 차트 데이터를 불러올 수 없습니다.
      </div>
    );
  }

  const lastClose = stock.close[stock.close.length - 1];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-sm font-semibold text-foreground">
            {stock.name} ({selection.ticker})
          </span>
          <span className="ml-2 text-sm text-muted">{formatPrice(lastClose)}</span>
        </div>
        <div className="flex flex-wrap gap-3 text-xs text-muted">
          {MA_LINES.map((ma) => (
            <span key={ma.key} className="flex items-center gap-1">
              <span className="inline-block h-0.5 w-3" style={{ backgroundColor: ma.color }} />
              {ma.label}
            </span>
          ))}
        </div>
      </div>
      <div ref={containerRef} className="w-full overflow-hidden rounded-xl border border-border/60" />
      <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
        <p className="mb-1 text-xs font-semibold text-accent">AI 기술적 타점 안내</p>
        <p className="text-sm leading-relaxed text-foreground/90">{buildTechnicalNote(stock)}</p>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          <span className="font-medium text-foreground/70">PB 매수 관전 포인트: </span>
          {selection.recommendation.buy_point}
        </p>
      </div>
    </div>
  );
}

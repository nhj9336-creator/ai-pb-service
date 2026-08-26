"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import type { SelectedStock, TechnicalStock, TrendLine } from "@/types/report";
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

type RangeMode = "1y" | "2y" | "all";
const RANGE_BARS: Record<RangeMode, number | null> = { "1y": 252, "2y": 504, all: null };
const RANGE_LABELS: Record<RangeMode, string> = { "1y": "1년", "2y": "2년", all: "전체" };

const MAIN_PANE_HEIGHT = 320;
const VOLUME_PANE_HEIGHT = 110;

function sliceArr<T>(arr: T[], n: number | null): T[] {
  return n === null ? arr : arr.slice(-n);
}

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

function MethodologyModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold text-foreground">지지/저항선 · 추세선 산출 근거</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-border px-2 py-0.5 text-xs text-muted hover:border-accent/50 hover:text-accent"
          >
            닫기
          </button>
        </div>

        <div className="space-y-4 text-sm leading-relaxed text-foreground/90">
          <section>
            <p className="mb-1 font-semibold text-accent">1. Pivot Point (P, R1, S1)</p>
            <p className="text-muted">
              전일 고가(H)·저가(L)·종가(C)만으로 당일 지지·저항 구간을 추정하는 전통적인 Floor
              Trader&apos;s Pivot 공식입니다. 차트에는 가장 핵심적인 3개 선(P, R1, S1)만 표시됩니다.
            </p>
            <ul className="mt-1.5 space-y-0.5 rounded-lg border border-border/60 bg-surface-elevated p-2.5 font-mono text-xs text-foreground/80">
              <li>P (피봇) = (H + L + C) / 3</li>
              <li>R1 (1차 저항) = 2P − L</li>
              <li>S1 (1차 지지) = 2P − H</li>
            </ul>
            <ul className="mt-2 space-y-1.5 text-xs text-muted">
              <li>
                <span className="font-semibold text-foreground/80">P(피봇) 부근:</span> 전일 매수·매도
                세력의 균형점입니다. 현재가가 P 위면 단기 매수 우위, 아래면 매도 우위로 해석합니다.
              </li>
              <li>
                <span className="font-semibold text-emerald-400">R1(저항) 돌파 시:</span> 매수세가
                전일 고점 이상으로 강해졌다는 뜻입니다. 다만 거래량이 뒷받침되지 않으면 상단에서
                되돌림(가짜 돌파)이 나올 확률이 높으니, 아래 거래량 항목과 함께 확인해야 합니다.
              </li>
              <li>
                <span className="font-semibold text-rose-400">S1(지지) 이탈 시:</span> 매도세가
                전일 저점 이하까지 우위를 점했다는 뜻으로, 추세 전환 가능성을 열어두고 손절/비중
                축소를 검토해야 하는 신호로 해석합니다.
              </li>
            </ul>
          </section>

          <section>
            <p className="mb-1 font-semibold text-accent">2. 이동평균선(MA5 / 20 / 60 / 120)</p>
            <p className="text-muted">
              각각 단기(5일)·단중기(20일)·중기(60일)·장기(120일) 추세를 나타냅니다. 짧은
              이평선이 긴 이평선 위에 순서대로 놓이면(MA5&gt;MA20&gt;MA60&gt;MA120) &quot;정배열&quot;로
              상승추세, 반대 순서면 &quot;역배열&quot;로 하락추세로 해석합니다. 정배열 상태에서의
              눌림목(단기 조정)은 상대적으로 안전한 매수 구간으로, 역배열 상태에서의 반등은
              추세 전환이 아닌 &quot;일시적 되돌림&quot;으로 보수적으로 접근하는 것이 일반적입니다.
            </p>
          </section>

          <section>
            <p className="mb-1 font-semibold text-accent">3. 대각선 추세선 (저항/지지 추세대)</p>
            <p className="text-muted">
              표준 프랙탈(fractal) 스윙 포인트 방식을 사용합니다. 특정 봉의 고가가 좌우 각 2개
              봉보다 높으면 &quot;스윙 고점&quot;, 저가가 좌우보다 낮으면 &quot;스윙 저점&quot;으로
              인식합니다. 최근 60거래일 내 첫 스윙 고점과 마지막 스윙 고점을 직선으로 이은 것이
              저항 추세선, 스윙 저점들을 이은 것이 지지 추세선입니다. 스윙 포인트가 2개 미만이면
              추세선을 표시하지 않습니다(근거 부족 시 임의로 선을 만들지 않음).
            </p>
            <p className="mt-1.5 text-xs text-muted">
              가격이 저항 추세선에 닿고&nbsp;<span className="font-semibold text-foreground/80">거래량 없이</span>&nbsp;
              반락하면 상단 매물 압박이 여전하다는 뜻이고, 반대로&nbsp;
              <span className="font-semibold text-foreground/80">거래량을 실은 채</span>&nbsp;
              돌파하면 새로운 매수 주체 유입으로 해석해 추세 지속 가능성을 높게 봅니다. 지지
              추세선에서도 동일한 논리가 반대 방향으로 적용됩니다.
            </p>
          </section>

          <p className="border-t border-border/60 pt-3 text-xs text-muted">
            위 지표는 모두 실제 시세 데이터로부터 결정적(deterministic)으로 계산되며, 금융공학·기술적
            분석에서 통용되는 표준 공식만 사용합니다.
          </p>
        </div>
      </div>
    </div>
  );
}

export default function StockChart({ selection, stock }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  // 2024-01부터의 전체 히스토리를 기본으로 보여준다("최근 1년만 보인다"는 혼선을 방지).
  const [rangeMode, setRangeMode] = useState<RangeMode>("all");
  const [showMethodology, setShowMethodology] = useState(false);
  const [showLevels, setShowLevels] = useState(true);
  const [showTrendlines, setShowTrendlines] = useState(true);

  const formatPrice = selection.market === "domestic" ? formatKrw : formatUsd;

  const sliced = useMemo(() => {
    if (!stock) return null;
    const n = RANGE_BARS[rangeMode];
    return {
      dates: sliceArr(stock.dates, n),
      open: sliceArr(stock.open, n),
      high: sliceArr(stock.high, n),
      low: sliceArr(stock.low, n),
      close: sliceArr(stock.close, n),
      volume: sliceArr(stock.volume, n),
      ma5: sliceArr(stock.ma5, n),
      ma20: sliceArr(stock.ma20, n),
      ma60: sliceArr(stock.ma60, n),
      ma120: sliceArr(stock.ma120, n),
    };
  }, [stock, rangeMode]);

  useEffect(() => {
    if (!containerRef.current || !stock || !sliced) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#101625" },
        textColor: "#8891a7",
        panes: { separatorColor: "#232c40" },
      },
      grid: {
        vertLines: { color: "#1c2436" },
        horzLines: { color: "#1c2436" },
      },
      rightPriceScale: { borderColor: "#232c40" },
      timeScale: { borderColor: "#232c40" },
      width: containerRef.current.clientWidth,
      height: MAIN_PANE_HEIGHT + VOLUME_PANE_HEIGHT,
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#fb7185",
      downColor: "#60a5fa",
      borderVisible: false,
      wickUpColor: "#fb7185",
      wickDownColor: "#60a5fa",
    });

    const candleData = sliced.dates
      .map((date, i) => ({
        time: date as Time,
        open: sliced.open[i],
        high: sliced.high[i],
        low: sliced.low[i],
        close: sliced.close[i],
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
      const lineData = sliced.dates
        .map((date, i) => ({ time: date as Time, value: sliced[ma.key][i] }))
        .filter((d) => d.value !== null) as { time: Time; value: number }[];
      series.setData(lineData);
    }

    // 핵심 지지/저항선만 표시(피봇 P, 1차 저항 R1, 1차 지지 S1) - 나머지(R2/S2)는 산만함을
    // 줄이기 위해 차트에서 제외한다(수치 자체는 PB 대응 노트/breakout·stop_loss에서 계속 활용).
    const { pivot_point, trend_channel } = stock;
    if (showLevels) {
      const priceLineSpecs: { price: number | null; title: string; color: string }[] = [
        { price: pivot_point.resistance_1, title: "저항선 R1", color: "#f97316" },
        { price: pivot_point.pivot, title: "피봇 P", color: "#94a3b8" },
        { price: pivot_point.support_1, title: "지지선 S1", color: "#34d399" },
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
    }

    // 대각선 추세선(고점-고점/저점-저점) 오버레이
    if (showTrendlines) {
      const trendLineSpecs: { line: TrendLine | null; color: string; title: string }[] = [
        { line: trend_channel?.resistance_trendline ?? null, color: "#fb923c", title: "저항 추세선" },
        { line: trend_channel?.support_trendline ?? null, color: "#4ade80", title: "지지 추세선" },
      ];
      for (const spec of trendLineSpecs) {
        const line = spec.line;
        if (!line || line.start_value === null || line.end_value === null) continue;
        const series = chart.addSeries(LineSeries, {
          color: spec.color,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          title: spec.title,
          lastValueVisible: false,
          priceLineVisible: false,
        });
        series.setData([
          { time: line.start_date as Time, value: line.start_value },
          { time: line.end_date as Time, value: line.end_value },
        ]);
      }
    }

    // 거래량 서브차트 (별도 pane)
    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        color: "#38bdf8",
      },
      1
    );
    const volumeData = sliced.dates
      .map((date, i) => {
        const v = sliced.volume[i];
        if (v === null) return null;
        const isUp = sliced.close[i] !== null && sliced.open[i] !== null && sliced.close[i]! >= sliced.open[i]!;
        return { time: date as Time, value: v, color: isUp ? "#fb718580" : "#60a5fa80" };
      })
      .filter((d): d is { time: Time; value: number; color: string } => d !== null);
    volumeSeries.setData(volumeData);

    const panes = chart.panes();
    if (panes[0]) panes[0].setHeight(MAIN_PANE_HEIGHT);
    if (panes[1]) panes[1].setHeight(VOLUME_PANE_HEIGHT);

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (containerRef.current) {
        chart.resize(containerRef.current.clientWidth, MAIN_PANE_HEIGHT + VOLUME_PANE_HEIGHT);
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [stock, sliced, selection.market, showLevels, showTrendlines]);

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
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          {MA_LINES.map((ma) => (
            <span key={ma.key} className="flex items-center gap-1">
              <span className="inline-block h-0.5 w-3" style={{ backgroundColor: ma.color }} />
              {ma.label}
            </span>
          ))}
          <button
            type="button"
            onClick={() => setShowLevels((v) => !v)}
            className={`rounded-full border px-2 py-0.5 font-medium transition-colors ${
              showLevels
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
                : "border-border text-muted hover:border-accent/50 hover:text-accent"
            }`}
          >
            지지/저항선 {showLevels ? "끄기" : "켜기"}
          </button>
          <button
            type="button"
            onClick={() => setShowTrendlines((v) => !v)}
            className={`rounded-full border px-2 py-0.5 font-medium transition-colors ${
              showTrendlines
                ? "border-orange-500/40 bg-orange-500/10 text-orange-400"
                : "border-border text-muted hover:border-accent/50 hover:text-accent"
            }`}
          >
            추세선 {showTrendlines ? "끄기" : "켜기"}
          </button>
          <button
            type="button"
            onClick={() => setShowMethodology(true)}
            className="rounded-full border border-border px-2 py-0.5 font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
          >
            ⓘ 산출 근거 안내
          </button>
        </div>
      </div>

      <div ref={containerRef} className="w-full overflow-hidden rounded-xl border border-border/60" />

      <div className="mt-2 flex justify-end gap-1">
        {(Object.keys(RANGE_LABELS) as RangeMode[]).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => setRangeMode(mode)}
            className={`rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors ${
              rangeMode === mode
                ? "border-accent bg-accent/10 text-accent"
                : "border-border text-muted hover:border-accent/50 hover:text-accent"
            }`}
          >
            {RANGE_LABELS[mode]}
          </button>
        ))}
      </div>

      <div className="mt-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
        <p className="mb-1 text-xs font-semibold text-accent">PB 대응 노트</p>
        <p className="text-sm leading-relaxed text-foreground/90">{buildTechnicalNote(stock)}</p>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          <span className="font-medium text-foreground/70">매수 관전 포인트 — </span>
          {selection.recommendation.buy_point}
        </p>
        {(selection.recommendation.breakout_price !== null || selection.recommendation.stop_loss_price !== null) && (
          <div className="mt-2 flex flex-wrap gap-2">
            {selection.recommendation.breakout_price !== null && (
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-400">
                ▲ 돌파 대응 {formatPrice(selection.recommendation.breakout_price)}
              </span>
            )}
            {selection.recommendation.stop_loss_price !== null && (
              <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-[11px] font-semibold text-rose-400">
                ▼ 손절 기준 {formatPrice(selection.recommendation.stop_loss_price)}
              </span>
            )}
          </div>
        )}
      </div>

      {showMethodology && <MethodologyModal onClose={() => setShowMethodology(false)} />}
    </div>
  );
}

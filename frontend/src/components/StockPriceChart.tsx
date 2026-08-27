"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  createChart,
  ColorType,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import type { TechnicalStock } from "@/types/report";
import { formatKrw, formatNumber, formatUsd } from "@/lib/format";

// MA는 참고용 보조선이므로 핵심 지지/저항/추세선 대비 옅은 톤(알파 채널 축소)으로 표시해
// 산만함을 줄인다.
const MA_LINES: { key: "ma5" | "ma20" | "ma60" | "ma120"; label: string; color: string }[] = [
  { key: "ma5", label: "MA5", color: "#facc15b3" },
  { key: "ma20", label: "MA20", color: "#38bdf8b3" },
  { key: "ma60", label: "MA60", color: "#a78bfab3" },
  { key: "ma120", label: "MA120", color: "#94a3b8b3" },
];

// 지지/저항선·추세선은 캔들 위를 직접 가리지 않도록 이름표를 차트 안에 그리지 않고(우측
// 축 가격 숫자만 표시), 대신 이 메타데이터로 아래 범례에서 색상-이름을 매칭해 보여준다.
const PIVOT_LINE_META: { key: "resistance_1" | "pivot" | "support_1"; label: string; color: string }[] = [
  { key: "resistance_1", label: "저항선 R1", color: "#f97316" },
  { key: "pivot", label: "피봇 P", color: "#94a3b8" },
  { key: "support_1", label: "지지선 S1", color: "#34d399" },
];

const TREND_LINE_META: { key: "resistance_trendline" | "support_trendline"; label: string; color: string }[] = [
  { key: "resistance_trendline", label: "저항 추세선", color: "#fb923c" },
  { key: "support_trendline", label: "지지 추세선", color: "#4ade80" },
];

type RangeMode = "1y" | "2y" | "all";
const RANGE_BARS: Record<RangeMode, number | null> = { "1y": 252, "2y": 504, all: null };
const RANGE_LABELS: Record<RangeMode, string> = { "1y": "1년", "2y": "2년", all: "전체" };

// "전체" 모드의 초기 진입 시 확대해서 보여줄 최근 구간(개월). 과거 전체 히스토리는 여전히
// 로드되어 있어 좌측으로 스크롤/드래그하면 이전 구간도 바로 이어서 확인할 수 있다.
const DEFAULT_ZOOM_MONTHS = 4;

const MAIN_PANE_HEIGHT = 320;
const VOLUME_PANE_HEIGHT = 110;

function sliceArr<T>(arr: T[], n: number | null): T[] {
  return n === null ? arr : arr.slice(-n);
}

/** "전체" 모드 진입 시 기본으로 보여줄 시야 범위: 최근 DEFAULT_ZOOM_MONTHS개월~현재.
 * 그보다 이전 데이터는 로드는 되어 있지만(좌측 스크롤로 확인 가능) 초기 확대 범위에서는 제외한다. */
function computeDefaultVisibleRange(dates: string[]): { from: Time; to: Time } | null {
  if (dates.length === 0) return null;
  const lastDateStr = dates[dates.length - 1];
  const cutoff = new Date(lastDateStr);
  cutoff.setMonth(cutoff.getMonth() - DEFAULT_ZOOM_MONTHS);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  const fromDateStr = dates.find((d) => d >= cutoffStr) ?? dates[0];
  return { from: fromDateStr as Time, to: lastDateStr as Time };
}

/** 현재가와 피봇/지지·저항선의 위치 관계를 1문장으로 요약한다(순수 기술적 지표 해석,
 * 추천/보유 여부와 무관하게 결정론적으로 계산됨). */
export function buildTechnicalNote(stock: TechnicalStock): string {
  // 장중에는 close(OHLCV의 마지막 확정 종가)가 전일자로 남아있을 수 있으므로, 실시간에
  // 가까운 current_price가 있으면 그 값을 기준으로 판단한다.
  const close = stock.current_price ?? stock.close[stock.close.length - 1];
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
            <p className="mb-1 font-semibold text-accent">1. Fibonacci Pivot Point (P, R1, S1)</p>
            <p className="text-muted">
              최근 5거래일(주간 단위) 고가(H)·저가(L)와 최근 종가(C)로 스윙 구간의 지지·저항을
              추정하는 피보나치 피봇 공식입니다. 전일 하루치 변동폭만 쓰는 일간 피봇보다 선 간격이
              넓어 촘촘하게 몰리지 않습니다. 차트에는 가장 핵심적인 3개 선(P, R1, S1)만 표시됩니다.
            </p>
            <ul className="mt-1.5 space-y-0.5 rounded-lg border border-border/60 bg-surface-elevated p-2.5 font-mono text-xs text-foreground/80">
              <li>P (피봇) = (H + L + C) / 3</li>
              <li>R1 (1차 저항) = P + 0.382 × (H − L)</li>
              <li>S1 (1차 지지) = P − 0.382 × (H − L)</li>
            </ul>
            <ul className="mt-2 space-y-1.5 text-xs text-muted">
              <li>
                <span className="font-semibold text-foreground/80">P(피봇) 부근:</span> 최근 한 주
                매수·매도 세력의 균형점입니다. 현재가가 P 위면 단기 매수 우위, 아래면 매도 우위로
                해석합니다.
              </li>
              <li>
                <span className="font-semibold text-emerald-400">R1(저항) 돌파 시:</span> 매수세가
                최근 주간 고점권 이상으로 강해졌다는 뜻입니다. 다만 거래량이 뒷받침되지 않으면
                상단에서 되돌림(가짜 돌파)이 나올 확률이 높으니, 아래 거래량 항목과 함께 확인해야
                합니다.
              </li>
              <li>
                <span className="font-semibold text-rose-400">S1(지지) 이탈 시:</span> 매도세가
                최근 주간 저점권 이하까지 우위를 점했다는 뜻으로, 추세 전환 가능성을 열어두고
                손절/비중 축소를 검토해야 하는 신호로 해석합니다.
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

export interface StockPriceChartProps {
  name: string;
  ticker: string;
  market: "domestic" | "us";
  stock: TechnicalStock;
  /** 헤더의 실시간 배지 옆에 추가로 표시할 배지(예: 추천 종목의 진입 시그널). */
  headerExtra?: ReactNode;
}

/** 추천 종목 상세 차트와 보유 종목 진단 차트가 공유하는 순수 차트 컴포넌트.
 * 캔들/이평선/거래량/지지·저항선/추세선/구간 탭/산출 근거 모달까지 포함하며,
 * 추천·보유 여부에 따라 달라지는 내용(매수 관전 포인트, 진단 결과 등)은 포함하지 않는다. */
export default function StockPriceChart({ name, ticker, market, stock, headerExtra }: StockPriceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const trendSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  // 2024-01부터의 전체 히스토리를 기본으로 보여준다("최근 1년만 보인다"는 혼선을 방지).
  const [rangeMode, setRangeMode] = useState<RangeMode>("all");
  const [showMethodology, setShowMethodology] = useState(false);
  const [showLevels, setShowLevels] = useState(true);
  const [showTrendlines, setShowTrendlines] = useState(true);

  const formatPrice = market === "domestic" ? formatKrw : formatUsd;

  const sliced = useMemo(() => {
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

  // 1) 차트 뼈대(캔들/이평선/거래량) 생성 - 종목·구간이 바뀔 때만 새로 만든다.
  // 지지/저항선·추세선 토글은 별도 effect에서 처리해 여기서는 손대지 않으므로, 토글 시
  // 차트가 재생성되지 않고 사용자가 보고 있던 시야 범위(Zoom/Pan)가 그대로 유지된다.
  useEffect(() => {
    if (!containerRef.current) return;

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
      timeScale: {
        borderColor: "#232c40",
        // 최신 봉 오른쪽으로 빈 공간이 무한히 스크롤되지 않도록 오른쪽 경계를 데이터 끝에 고정한다.
        fixRightEdge: true,
      },
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
      // 마지막 종가에서 가로로 가로지르는 점선(기본값)을 끄고, 지지/저항선이 더 선명하게
      // 보이도록 한다. 현재가는 상단 헤더에 이미 텍스트로 표시된다.
      priceLineVisible: false,
    });
    candleSeriesRef.current = candleSeries;

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
        // MA는 보조 지표이므로 지지/저항선과 겹쳐 산만해지지 않도록 우측 Y축의 마지막 값
        // 라벨(색깔 박스)과 자체 기준선(점선)을 모두 끈다 - title도 비워 어떤 경로로도
        // 라벨 텍스트가 노출되지 않게 한다(상단 범례로 색상은 이미 구분 가능).
        title: "",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const lineData = sliced.dates
        .map((date, i) => ({ time: date as Time, value: sliced[ma.key][i] }))
        .filter((d) => d.value !== null) as { time: Time; value: number }[];
      series.setData(lineData);
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

    // "전체" 모드는 2024-01부터의 전체 히스토리를 로드하되, 초기 확대 범위는 최근
    // DEFAULT_ZOOM_MONTHS개월로 좁혀 불필요하게 먼 과거까지 보이지 않게 한다(좌측 스크롤로
    // 언제든 과거 구간을 이어서 볼 수 있다). 1년/2년 탭은 이미 그만큼만 로드했으므로 기존대로
    // 전체를 fitContent로 보여준다.
    const defaultRange = rangeMode === "all" ? computeDefaultVisibleRange(sliced.dates) : null;
    if (defaultRange) {
      chart.timeScale().setVisibleRange(defaultRange);
    } else {
      chart.timeScale().fitContent();
    }

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
      candleSeriesRef.current = null;
      priceLinesRef.current = [];
      trendSeriesRef.current = [];
    };
  }, [stock, sliced, market, rangeMode]);

  // 2) 지지/저항선(피봇 P/R1/S1) 토글 - 차트를 새로 만들지 않고 가격선만 추가/제거하므로
  // 사용자가 보고 있던 Zoom/Pan 상태가 그대로 유지된다.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;

    for (const line of priceLinesRef.current) {
      candleSeries.removePriceLine(line);
    }
    priceLinesRef.current = [];

    if (!showLevels) return;

    // 핵심 지지/저항선만 표시(피봇 P, 1차 저항 R1, 1차 지지 S1) - 나머지(R2/S2)는 산만함을
    // 줄이기 위해 차트에서 제외한다(수치 자체는 PB 대응 노트/breakout·stop_loss에서 계속 활용).
    const { pivot_point } = stock;
    for (const meta of PIVOT_LINE_META) {
      const price = pivot_point[meta.key];
      if (price === null) continue;
      const line = candleSeries.createPriceLine({
        price,
        color: meta.color,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        // title을 채우면 lightweight-charts가 그 텍스트를 캔들 위(차트 판) 왼쪽에 직접
        // 그려 넣어 봉을 가린다 - axisLabelVisible만으로 가격 숫자를 오른쪽 축에 깔끔하게
        // 표시하고, 어떤 선인지는 아래 범례(색상)로 구분한다.
        axisLabelVisible: true,
        title: "",
      });
      priceLinesRef.current.push(line);
    }
    // sliced/rangeMode/market은 effect 1이 차트를 재생성하는 트리거와 동일하다 - 차트가
    // 재생성되면(candleSeriesRef.current가 새 인스턴스로 바뀌면) 여기서도 함께 재실행되어
    // 새 차트에 가격선을 다시 그려야 한다(그렇지 않으면 구간 탭 전환 시 선이 사라진다).
    // showLevels만 바뀔 때는 이 값들이 그대로이므로 차트는 재생성되지 않는다.
  }, [stock, sliced, market, rangeMode, showLevels]);

  // 3) 대각선 추세선(고점-고점/저점-저점) 토글 - 마찬가지로 차트를 새로 만들지 않는다.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    for (const series of trendSeriesRef.current) {
      chart.removeSeries(series);
    }
    trendSeriesRef.current = [];

    if (!showTrendlines) return;

    const { trend_channel } = stock;
    for (const meta of TREND_LINE_META) {
      const line = trend_channel?.[meta.key] ?? null;
      if (!line || line.start_value === null || line.end_value === null) continue;
      const series = chart.addSeries(LineSeries, {
        color: meta.color,
        lineWidth: 3,
        lineStyle: LineStyle.Solid,
        title: meta.label,
        // 대각선이 도달한 "현재(가장 오른쪽) 값"이 캔들 가격대와 거의 겹치는 경우가 많아,
        // 축 라벨(lastValueVisible)까지 켜면 우측 Y축 부근에서 캔들/가격을 가린다. 상단
        // 범례로 이미 색상-이름을 구분할 수 있으므로 축 라벨과 자체 기준선(점선) 모두 끈다.
        lastValueVisible: false,
        priceLineVisible: false,
      });
      series.setData([
        { time: line.start_date as Time, value: line.start_value },
        { time: line.end_date as Time, value: line.end_value },
      ]);
      trendSeriesRef.current.push(series);
    }
    // sliced/rangeMode/market을 포함하는 이유는 effect 2와 동일(차트 재생성 시 새 차트에
    // 추세선을 다시 그리기 위함).
  }, [stock, sliced, market, rangeMode, showTrendlines]);

  // 장중에는 close(마지막 확정 종가)가 전일자로 남아있을 수 있으므로, 실시간에 가까운
  // current_price가 있으면 그 값을 헤더에 우선 표시한다.
  const displayPrice = stock.current_price ?? stock.close[stock.close.length - 1];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-sm font-semibold text-foreground">
            {name} ({ticker})
          </span>
          <span className="ml-2 text-sm text-muted">{formatPrice(displayPrice)}</span>
          {stock.current_price_is_realtime && (
            <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
              </span>
              실시간
            </span>
          )}
          {headerExtra}
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

      {(showLevels || showTrendlines) && (
        <div className="mb-2 flex flex-wrap items-center gap-3 text-[11px] text-muted">
          {showLevels &&
            PIVOT_LINE_META.map((meta) => (
              <span key={meta.key} className="flex items-center gap-1">
                <span className="inline-block h-0.5 w-3" style={{ backgroundColor: meta.color }} />
                {meta.label}
              </span>
            ))}
          {showTrendlines &&
            TREND_LINE_META.map((meta) => (
              <span key={meta.key} className="flex items-center gap-1">
                <span className="inline-block h-0.5 w-3" style={{ backgroundColor: meta.color }} />
                {meta.label}
              </span>
            ))}
        </div>
      )}

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

      {showMethodology && <MethodologyModal onClose={() => setShowMethodology(false)} />}
    </div>
  );
}

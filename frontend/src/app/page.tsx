"use client";

import { useEffect, useState } from "react";
import DateController from "@/components/DateController";
import IndexOverview from "@/components/IndexOverview";
import CentralReport from "@/components/CentralReport";
import RecommendedStocks from "@/components/RecommendedStocks";
import StockChart from "@/components/StockChart";
import PortfolioAllocation from "@/components/PortfolioAllocation";
import SectionCard from "@/components/SectionCard";
import { ApiError, fetchLatestReport, generateReportNow } from "@/lib/api";
import { formatDateLabel, formatTimestamp, parseDateInputValue, toDateInputValue } from "@/lib/format";
import type { PbReport, SelectedStock } from "@/types/report";

export default function Home() {
  const [report, setReport] = useState<PbReport | null>(null);
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [selectedStock, setSelectedStock] = useState<SelectedStock | null>(null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchLatestReport()
      .then((data) => {
        setReport(data);
        setSelectedDate(parseDateInputValue(data.meta.target_date));
        setSelectedStock(null);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setError("아직 생성된 리포트가 없습니다. 날짜를 선택하고 [조회하기]를 눌러 리포트를 생성하세요.");
        } else {
          setError(err instanceof Error ? err.message : "리포트를 불러오지 못했습니다.");
        }
      })
      .finally(() => setInitialLoading(false));
  }, []);

  const handleQuery = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await generateReportNow({ targetDate: toDateInputValue(selectedDate) });
      setReport(data);
      setSelectedStock(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "리포트 생성에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  // 사용자가 명시적으로 종목을 고르지 않았다면 국내 1순위 추천 종목을 기본 선택으로 파생한다.
  const defaultDomestic = report?.recommended_stocks.domestic[0];
  const activeSelection: SelectedStock | null =
    selectedStock ??
    (defaultDomestic
      ? { market: "domestic", ticker: defaultDomestic.ticker, recommendation: defaultDomestic }
      : null);

  const selectedTechnical =
    report && activeSelection
      ? report.market_data.technical[activeSelection.market][activeSelection.ticker]
      : null;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">PB 프라이빗 전략 데스크</h1>
          <p className="text-sm text-muted">Senior PB 실시간 시장 브리핑 · 기준일 {formatDateLabel(toDateInputValue(selectedDate))}</p>
        </div>
        {report &&
          (() => {
            const isIntraday = report.meta.data_freshness === "intraday";
            const label = report.meta.data_freshness_label ?? (isIntraday ? "장중 실시간 분석" : "장마감 데이터 분석");
            return (
              <div
                className={`inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${
                  isIntraday
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                    : "border-border bg-surface-elevated text-muted"
                }`}
              >
                <span className="relative flex h-2 w-2">
                  {isIntraday && (
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  )}
                  <span className={`relative inline-flex h-2 w-2 rounded-full ${isIntraday ? "bg-emerald-500" : "bg-muted"}`} />
                </span>
                {label} 기준 시각 {formatTimestamp(report.meta.generated_at)}
              </div>
            );
          })()}
      </header>

      <DateController
        selectedDate={selectedDate}
        onSelectedDateChange={setSelectedDate}
        onQuery={handleQuery}
        loading={loading}
      />

      {error && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          {error}
        </div>
      )}

      {report?.source_data_errors && report.source_data_errors.length > 0 && (
        <details className="rounded-xl border border-border bg-surface px-4 py-3 text-xs text-muted">
          <summary className="cursor-pointer select-none text-sm text-foreground/80">
            일부 데이터 소스 수집 실패 ({report.source_data_errors.length}건)
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-4">
            {report.source_data_errors.map((e, idx) => (
              <li key={idx}>{e}</li>
            ))}
          </ul>
        </details>
      )}

      {initialLoading && (
        <p className="text-sm text-muted">
          리포트를 불러오는 중... (백엔드가 잠들어 있었다면 깨어나는 데 최대 1분 정도 걸릴 수 있습니다)
        </p>
      )}

      {report && (
        <>
          <IndexOverview marketData={report.market_data} />

          <CentralReport report={report} />

          <SectionCard title="추천 종목 & 인터랙티브 차트" icon={<span>📈</span>}>
            <RecommendedStocks
              domestic={report.recommended_stocks.domestic}
              us={report.recommended_stocks.us}
              selected={activeSelection}
              onSelect={setSelectedStock}
            />
            {activeSelection && (
              <div className="mt-6 border-t border-border/60 pt-6">
                <StockChart selection={activeSelection} stock={selectedTechnical} />
              </div>
            )}
          </SectionCard>

          <PortfolioAllocation allocation={report.portfolio_allocation} products={report.financial_products} />

          <footer className="rounded-xl border border-border/60 bg-surface px-4 py-3 text-xs text-muted">
            {report.disclaimer}
          </footer>
        </>
      )}
    </div>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import DateController from "@/components/DateController";
import IndexOverview from "@/components/IndexOverview";
import CentralReport from "@/components/CentralReport";
import RecommendedStocks from "@/components/RecommendedStocks";
import StockChart from "@/components/StockChart";
import StockPriceChart from "@/components/StockPriceChart";
import DiagnosisReport from "@/components/DiagnosisReport";
import PortfolioDiagnosis from "@/components/PortfolioDiagnosis";
import PortfolioAllocation from "@/components/PortfolioAllocation";
import SectionCard from "@/components/SectionCard";
import { ApiError, fetchLatestReport, generateReportNow } from "@/lib/api";
import { formatDateLabel, formatTimestamp, parseDateInputValue, toDateInputValue } from "@/lib/format";
import type { PbReport, SelectedStock, StockDiagnosisResponse } from "@/types/report";

export default function Home() {
  const [report, setReport] = useState<PbReport | null>(null);
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [selectedStock, setSelectedStock] = useState<SelectedStock | null>(null);
  // 보유 종목 진단 결과 - 값이 있으면 상단 인터랙티브 차트가 추천 종목 대신 이 종목으로
  // 전환되고, 그 아래에 진단 리포트가 노출된다.
  const [diagnosisResult, setDiagnosisResult] = useState<StockDiagnosisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const chartSectionRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fetchLatestReport()
      .then((data) => {
        setReport(data);
        setSelectedDate(parseDateInputValue(data.meta.target_date));
        setSelectedStock(null);
        setDiagnosisResult(null);
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
      setDiagnosisResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "리포트 생성에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  // 추천 종목 카드를 새로 고르면 진단 뷰에서 다시 추천 종목 뷰로 돌아간다.
  const handleSelectStock = (selection: SelectedStock) => {
    setDiagnosisResult(null);
    setSelectedStock(selection);
  };

  // 진단이 완료되면 그 결과로 상단 차트를 전환하고, 차트 섹션으로 스크롤해 바로 보이게 한다.
  const handleDiagnosed = (result: StockDiagnosisResponse) => {
    setDiagnosisResult(result);
    chartSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
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

          <div ref={chartSectionRef}>
            <SectionCard title="추천 종목 & 인터랙티브 차트" icon={<span>📈</span>}>
              <RecommendedStocks
                domestic={report.recommended_stocks.domestic}
                us={report.recommended_stocks.us}
                selected={activeSelection}
                onSelect={handleSelectStock}
              />
              {diagnosisResult ? (
                <div className="mt-6 border-t border-border/60 pt-6">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accent">
                      🩺 보유 종목 진단 뷰
                    </span>
                    <button
                      type="button"
                      onClick={() => setDiagnosisResult(null)}
                      className="rounded-full border border-border px-2.5 py-1 text-xs font-medium text-muted transition-colors hover:border-accent/50 hover:text-accent"
                    >
                      추천 종목 차트로 돌아가기
                    </button>
                  </div>
                  <StockPriceChart
                    name={diagnosisResult.technical.name}
                    ticker={diagnosisResult.holding.ticker}
                    market={diagnosisResult.holding.market}
                    stock={diagnosisResult.technical}
                  />
                  <div className="mt-3">
                    <DiagnosisReport result={diagnosisResult} />
                  </div>
                </div>
              ) : (
                activeSelection && (
                  <div className="mt-6 border-t border-border/60 pt-6">
                    <StockChart selection={activeSelection} stock={selectedTechnical} />
                  </div>
                )
              )}
            </SectionCard>
          </div>

          <PortfolioDiagnosis onDiagnosed={handleDiagnosed} />

          <PortfolioAllocation allocation={report.portfolio_allocation} products={report.financial_products} />

          <footer className="rounded-xl border border-border/60 bg-surface px-4 py-3 text-xs text-muted">
            {report.disclaimer}
          </footer>
        </>
      )}
    </div>
  );
}

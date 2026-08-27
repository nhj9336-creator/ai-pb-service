"use client";

import { useState } from "react";
import SectionCard from "./SectionCard";
import { ApiError, diagnoseStockHolding } from "@/lib/api";
import type { StockDiagnosisResponse } from "@/types/report";

interface PortfolioDiagnosisProps {
  /** 진단 성공 시 결과를 전달한다 - 상단 인터랙티브 차트를 이 종목으로 스위칭하는 데 쓰인다. */
  onDiagnosed: (result: StockDiagnosisResponse) => void;
}

export default function PortfolioDiagnosis({ onDiagnosed }: PortfolioDiagnosisProps) {
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<"domestic" | "us">("domestic");
  const [quantity, setQuantity] = useState("");
  const [avgPrice, setAvgPrice] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const quantityNum = Number(quantity);
    const avgPriceNum = Number(avgPrice);
    if (!query.trim()) {
      setError("종목명 또는 종목코드를 입력해 주세요.");
      return;
    }
    if (!(quantityNum > 0) || !(avgPriceNum > 0)) {
      setError("보유 수량과 매수 평균단가는 0보다 큰 숫자로 입력해 주세요.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const result = await diagnoseStockHolding({
        query: query.trim(),
        market,
        quantity: quantityNum,
        avgPrice: avgPriceNum,
      });
      onDiagnosed(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "진단 생성에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <SectionCard title="보유 종목 맞춤 PB 진단" icon={<span>🩺</span>}>
      <p className="mb-4 text-xs leading-relaxed text-muted">
        보유 중인 종목의 수량과 평균단가를 입력하면, 위 인터랙티브 차트가 해당 종목으로 전환되어
        지지/저항선·이평선·피봇을 즉시 보여주고, 그 아래에 수익률·투자 호흡별(단타/스윙/장기) 대응
        전략을 진단해드립니다. 진단은 오늘의 시장 총평 스탠스와 모순되지 않도록 연결됩니다.
      </p>

      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">시장</span>
          <select
            value={market}
            onChange={(e) => setMarket(e.target.value as "domestic" | "us")}
            className="rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          >
            <option value="domestic">국내</option>
            <option value="us">미국</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">종목명 / 종목코드</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={market === "domestic" ? "예: 삼성전자 또는 005930" : "예: AAPL"}
            className="w-44 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">보유 수량</span>
          <input
            type="number"
            min="0"
            step="any"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder="10"
            className="w-28 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">매수 평균단가</span>
          <input
            type="number"
            min="0"
            step="any"
            value={avgPrice}
            onChange={(e) => setAvgPrice(e.target.value)}
            placeholder={market === "domestic" ? "70000" : "180.5"}
            className="w-32 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-accent px-5 py-2 text-sm font-semibold text-[#04121c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "진단 중..." : "진단하기"}
        </button>
      </form>

      {error && (
        <div className="mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          {error}
        </div>
      )}
    </SectionCard>
  );
}

"use client";

import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import SectionCard from "./SectionCard";
import type { FinancialProduct, PortfolioAllocation as PortfolioAllocationType } from "@/types/report";

const PALETTE = ["#38bdf8", "#fb7185", "#facc15", "#34d399", "#a78bfa", "#f97316"];

function ProductList({ products }: { products: FinancialProduct[] }) {
  if (!products || products.length === 0) {
    return <p className="text-sm text-muted">추천 금융 상품이 없습니다.</p>;
  }
  return (
    <ul className="space-y-3">
      {products.map((p, idx) => (
        <li key={idx} className="rounded-lg border border-border/60 bg-surface-elevated p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-foreground">{p.name}</span>
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent">{p.type}</span>
          </div>
          <p className="mt-1 text-xs text-muted">{p.description}</p>
          <p className="mt-1 text-xs text-foreground/80">{p.allocation_reason}</p>
        </li>
      ))}
    </ul>
  );
}

export default function PortfolioAllocation({
  allocation,
  products,
}: {
  allocation: PortfolioAllocationType;
  products: FinancialProduct[];
}) {
  const chartData = allocation.assets.map((a) => ({ name: a.name, value: a.percent }));

  return (
    <SectionCard title="PB Portfolio & Asset Allocation" icon={<span>💰</span>}>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 lg:items-stretch">
        {/* 좌측: 자산배분 도넛 차트 + 리밸런싱 전략 */}
        <div className="flex flex-col">
          <div style={{ width: "100%", height: 260 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie
                  data={chartData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={90}
                  paddingAngle={2}
                  label={({ name, value }) => `${name} ${value}%`}
                >
                  {chartData.map((_, idx) => (
                    <Cell key={idx} fill={PALETTE[idx % PALETTE.length]} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value, name) => [`${value}%`, name]}
                  contentStyle={{
                    background: "#151d30",
                    border: "1px solid #232c40",
                    borderRadius: 8,
                    color: "#e6e9f2",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12, color: "#8891a7" }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 flex-1 rounded-lg border border-border/60 bg-surface-elevated p-3">
            <p className="mb-1 text-xs font-semibold text-muted">리밸런싱 전략</p>
            <p className="text-sm leading-relaxed text-foreground/90">{allocation.rebalancing_strategy}</p>
          </div>
        </div>

        {/* 우측: 자산군별 세부 리스트 + 맞춤형 금융 상품 */}
        <div className="flex flex-col gap-4">
          <div>
            <p className="mb-2 text-xs font-semibold text-muted">자산군별 비중 · 대표 상품</p>
            <ul className="space-y-2">
              {allocation.assets.map((a, idx) => (
                <li key={a.name} className="rounded-lg border border-border/60 bg-surface-elevated p-2.5">
                  <div className="flex items-center gap-2">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: PALETTE[idx % PALETTE.length] }}
                    />
                    <span className="text-sm font-semibold text-foreground">{a.name}</span>
                    <span className="ml-auto text-sm font-semibold text-foreground">{a.percent}%</span>
                  </div>
                  <p className="mt-1 pl-5 text-xs text-muted">{a.representative_instruments}</p>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex-1">
            <p className="mb-2 text-xs font-semibold text-muted">맞춤형 금융 상품</p>
            <ProductList products={products} />
          </div>
        </div>
      </div>
    </SectionCard>
  );
}

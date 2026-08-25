import type { PbStrategyOpinion } from "@/types/report";

const STYLES: Record<PbStrategyOpinion, string> = {
  매수: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  관망: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  비중축소: "bg-rose-500/15 text-rose-400 border-rose-500/40",
};

export default function StrategyBadge({ opinion }: { opinion: PbStrategyOpinion }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-semibold ${STYLES[opinion]}`}
    >
      PB 전략 의견: {opinion}
    </span>
  );
}

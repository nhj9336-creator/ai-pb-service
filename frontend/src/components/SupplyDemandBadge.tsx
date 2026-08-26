import type { SupplyDemandStatus } from "@/types/report";

const STYLES: Record<SupplyDemandStatus, string> = {
  매집: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  이탈: "bg-rose-500/15 text-rose-400 border-rose-500/40",
  혼조: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  데이터없음: "bg-slate-500/15 text-muted border-border",
};

const ICON: Record<SupplyDemandStatus, string> = {
  매집: "▲",
  이탈: "▼",
  혼조: "◆",
  데이터없음: "–",
};

export default function SupplyDemandBadge({ status }: { status: SupplyDemandStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-sm font-semibold ${STYLES[status]}`}
    >
      <span aria-hidden>{ICON[status]}</span>
      수급 {status}
    </span>
  );
}

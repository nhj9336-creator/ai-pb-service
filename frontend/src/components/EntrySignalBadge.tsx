import type { EntrySignal } from "@/types/report";

const STYLES: Record<EntrySignal, string> = {
  진입유효: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  눌림목대기: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  고점매수주의: "bg-orange-500/15 text-orange-400 border-orange-500/40",
  진입보류: "bg-rose-500/15 text-rose-400 border-rose-500/40",
};

const ICONS: Record<EntrySignal, string> = {
  진입유효: "●",
  눌림목대기: "◐",
  고점매수주의: "▲",
  진입보류: "■",
};

export default function EntrySignalBadge({ signal, className }: { signal: EntrySignal; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${STYLES[signal]} ${className ?? ""}`}
    >
      <span aria-hidden>{ICONS[signal]}</span>
      {signal}
    </span>
  );
}

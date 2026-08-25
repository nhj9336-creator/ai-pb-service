export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatKrw(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${formatNumber(value, 0)}원`;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `$${formatNumber(value, 2)}`;
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value, digits)}%`;
}

export function formatSigned(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value, digits)}`;
}

/** 등락 방향에 따른 텍스트 색상. 국내 관례대로 상승=빨강, 하락=파랑. */
export function changeColorClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-slate-400";
  return value > 0 ? "text-rose-400" : "text-blue-400";
}

export function formatCompactAmount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_0000_0000) return `${sign}${(abs / 1_0000_0000).toFixed(1)}억`;
  if (abs >= 1_0000) return `${sign}${(abs / 1_0000).toFixed(0)}만`;
  return formatNumber(value, 0);
}

export function formatDateLabel(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  // DART API의 rcept_dt처럼 대시 없는 "YYYYMMDD" 형식도 지원한다.
  const normalized = /^\d{8}$/.test(dateStr)
    ? `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`
    : dateStr;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export function toDateInputValue(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** "YYYY-MM-DD"를 UTC 파싱으로 인한 시간대 오프셋 없이 로컬 Date로 변환한다. */
export function parseDateInputValue(value: string): Date {
  const [y, m, d] = value.split("-").map(Number);
  return new Date(y, m - 1, d);
}

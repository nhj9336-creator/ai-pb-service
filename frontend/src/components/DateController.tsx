"use client";

import DatePicker from "react-datepicker";
import { ko } from "date-fns/locale";

interface DateControllerProps {
  selectedDate: Date;
  onSelectedDateChange: (date: Date) => void;
  onQuery: () => void;
  loading: boolean;
}

export default function DateController({
  selectedDate,
  onSelectedDateChange,
  onQuery,
  loading,
}: DateControllerProps) {
  const isToday = new Date().toDateString() === selectedDate.toDateString();

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-surface px-5 py-4 shadow-lg shadow-black/20">
      <span className="text-sm font-medium text-muted">기준일</span>
      <DatePicker
        selected={selectedDate}
        onChange={(date: Date | null) => date && onSelectedDateChange(date)}
        maxDate={new Date()}
        locale={ko}
        dateFormat="yyyy-MM-dd"
        className="w-36 rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
      />
      {!isToday && (
        <button
          type="button"
          onClick={() => onSelectedDateChange(new Date())}
          className="rounded-lg border border-border px-3 py-2 text-xs text-muted transition-colors hover:border-accent hover:text-accent"
        >
          오늘
        </button>
      )}
      <button
        type="button"
        onClick={onQuery}
        disabled={loading}
        className="ml-auto rounded-lg bg-accent px-5 py-2 text-sm font-semibold text-[#04121c] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? "리포트 생성 중..." : "조회하기"}
      </button>
    </div>
  );
}

import { forwardRef, type CSSProperties, type ReactNode } from "react";

interface SectionCardProps {
  title: string;
  icon?: ReactNode;
  subtitle?: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

const SectionCard = forwardRef<HTMLElement, SectionCardProps>(function SectionCard(
  { title, icon, subtitle, children, className, style },
  ref
) {
  return (
    <section
      ref={ref}
      style={style}
      className={`rounded-2xl border border-border bg-surface p-5 shadow-lg shadow-black/20 ${className ?? ""}`}
    >
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-semibold text-foreground">
          {icon}
          {title}
        </h2>
        {subtitle && <span className="text-xs text-muted">{subtitle}</span>}
      </div>
      {children}
    </section>
  );
});

export default SectionCard;

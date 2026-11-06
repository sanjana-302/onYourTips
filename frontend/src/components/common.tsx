import { useEffect, useRef, type ChangeEvent, type MouseEvent, type ReactNode } from "react";
import { useApp } from "../lib/AppContext";
import { hrefFor, type Tab } from "../lib/router";
import { Icon, type IconName } from "./Icon";

export function PageHeader({
  title,
  subtitle,
  detail,
  action,
}: {
  title: string;
  subtitle?: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p className="subtitle">{subtitle}</p> : null}
        {detail ? <p className="header-detail">{detail}</p> : null}
      </div>
      {action}
    </header>
  );
}

export function Chips<T extends string>({
  options,
  selected,
  onToggle,
  label,
  trailing,
}: {
  options: { value: T; label: string; count?: number }[];
  selected: T[];
  onToggle: (value: T) => void;
  label: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="chips" role="group" aria-label={label}>
      {trailing}
      {options.map((o) => (
        <button
          key={o.value}
          className="chip"
          aria-pressed={selected.includes(o.value)}
          onClick={() => onToggle(o.value)}
        >
          {selected.includes(o.value) ? <Icon name="check" size={15} strokeWidth={2.6} /> : null}
          {o.label}
          {o.count ? <span className="chip-count">{o.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function SearchBar({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <label className="search">
      <span className="sr-only">Search stories</span>
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        enterKeyHint="search"
        autoCorrect="off"
      />
      {value ? (
        <button
          type="button"
          className="search-clear"
          aria-label="Clear search"
          onClick={() => onChange("")}
        >
          <Icon name="close" size={14} strokeWidth={2.6} />
        </button>
      ) : null}
      <Icon name="search" size={20} />
    </label>
  );
}

/** Bottom sheet with a grab handle, a close button and an optional Reset link. */
export function Sheet({
  title,
  onClose,
  onReset,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  onReset?: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.classList.add("locked");
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.classList.remove("locked");
    };
  }, [onClose]);

  return (
    <div className="sheet-layer" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
        tabIndex={-1}
        onClick={(e: MouseEvent) => e.stopPropagation()}
      >
        <span className="handle" aria-hidden="true" />
        <div className="sheet-head">
          <button className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="close" size={20} />
          </button>
          <h2>{title}</h2>
          {onReset ? (
            <button className="link-btn" onClick={onReset}>
              Reset
            </button>
          ) : (
            <span />
          )}
        </div>
        <div className="sheet-body">{children}</div>
        {footer ? <div className="sheet-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

/** A large tappable row with a check circle, as in a settings list. */
export function OptionRow({
  label,
  detail,
  checked,
  onToggle,
}: {
  label: string;
  detail?: string;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <button className="option" role="checkbox" aria-checked={checked} onClick={onToggle}>
      <span className="option-label">{label}</span>
      {detail ? <span className="option-detail">{detail}</span> : null}
      <span className="option-check" aria-hidden="true">
        {checked ? <Icon name="check" size={14} strokeWidth={3} /> : null}
      </span>
    </button>
  );
}

export function Notice({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="notice card">
      <h2>{title}</h2>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  );
}

export function LoadingRows() {
  return (
    <ul className="stories" aria-busy="true" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <li key={i} className="story skeleton">
          <div className="story-body">
            <span className="sk sk-avatar" />
            <span className="sk sk-title" />
            <span className="sk sk-meta" />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function ErrorNotice({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <Notice
      title="Couldn't load this"
      action={
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      }
    >
      {error.message}
    </Notice>
  );
}

const TABS: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: "today", label: "Today", icon: "today" },
  { tab: "week", label: "Week", icon: "week" },
  { tab: "saved", label: "Saved", icon: "bookmark" },
  { tab: "you", label: "You", icon: "you" },
];

export function TabBar({ active }: { active: Tab }) {
  return (
    <nav className="tabbar" aria-label="Sections">
      {TABS.map((t) => (
        <a
          key={t.tab}
          href={hrefFor(t.tab)}
          className="tab"
          aria-current={t.tab === active ? "page" : undefined}
        >
          <Icon name={t.icon} size={21} filled={t.tab === "saved" && t.tab === active} />
          <span>{t.label}</span>
        </a>
      ))}
    </nav>
  );
}

export function ToastHost() {
  const { toast, dismissToast } = useApp();
  if (!toast) return null;
  return (
    <div className="toast" role="status" key={toast.id}>
      <span>{toast.message}</span>
      {toast.actionLabel && toast.onAction ? (
        <button
          className="toast-action"
          onClick={() => {
            toast.onAction?.();
            dismissToast();
          }}
        >
          {toast.actionLabel}
        </button>
      ) : null}
    </div>
  );
}

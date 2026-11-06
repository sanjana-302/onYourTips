const PATHS = {
  bookmark: "M6 3.5h12a1 1 0 0 1 1 1V21l-7-4.5L5 21V4.5a1 1 0 0 1 1-1Z",
  close: "M6 6l12 12M18 6 6 18",
  refresh: "M20 11a8 8 0 1 0-2.34 5.66M20 4v7h-7",
  back: "M15 5 8 12l7 7",
  chevron: "M9 5l7 7-7 7",
  arrow: "M5 12h14M13 6l6 6-6 6",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  today:
    "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4",
  week: "M4 7h16v13H4zM4 11h16M9 3v4M15 3v4",
  you: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 20.5c1.2-3.6 4-5.5 7.5-5.5s6.3 1.9 7.5 5.5",
  chat: "M4 5h16v11H9l-5 4z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM20 20l-4-4",
  flame:
    "M12 21c3.9 0 6.5-2.6 6.5-6.2 0-3.4-2.2-5.6-3.6-7.4-.4 1.8-1.4 3-2.6 3.4.3-3.2-1-6-3.3-7.8.2 3-1.5 5-3 6.8C4.7 11.4 5.5 13.6 5.5 15c0 3.5 2.6 6 6.5 6Z",
  star: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8Z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  plus: "M12 5v14M5 12h14",
  sliders: "M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6",
  eyeOff:
    "M3 3l18 18M10.6 6.2A9.9 9.9 0 0 1 12 6c5 0 8.5 4.5 9.5 6-.5.8-1.6 2.3-3.2 3.6M6.4 7.6C4.6 8.9 3.2 10.7 2.5 12c1 1.5 4.5 6 9.5 6 1.5 0 2.9-.4 4.1-1M9.9 9.9a3 3 0 0 0 4.2 4.2",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  filled = false,
  size = 22,
  title,
  strokeWidth = 1.9,
}: {
  name: IconName;
  filled?: boolean;
  size?: number;
  title?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title ? <title>{title}</title> : null}
      <path d={PATHS[name]} />
    </svg>
  );
}

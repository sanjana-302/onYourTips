import { useApp } from "../lib/AppContext";

// Each kind of source gets a fixed colour from the palette so you learn to recognise them.
const KIND_STYLE: Record<string, { bg: string; fg: string; label?: string }> = {
  hackernews: { bg: "var(--orange)", fg: "#fff", label: "HN" },
  github: { bg: "var(--violet)", fg: "var(--beige)", label: "GH" },
  reddit: { bg: "var(--peach)", fg: "var(--on-pastel)", label: "r/" },
  arxiv: { bg: "var(--lav)", fg: "var(--on-pastel)", label: "arX" },
  hf_papers: { bg: "var(--peach)", fg: "var(--on-pastel)", label: "HF" },
  devto: { bg: "var(--green)", fg: "var(--on-pastel)", label: "DEV" },
  rss: { bg: "var(--beige)", fg: "var(--on-pastel)" },
};

function initials(name: string): string {
  const words = name.replace(/[·|].*$/, "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2);
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function SourceAvatar({ sourceId, size = 34 }: { sourceId: string; size?: number }) {
  const { sourceNames, sourceKinds } = useApp();
  const kind = sourceKinds.get(sourceId) ?? "rss";
  const style = KIND_STYLE[kind] ?? KIND_STYLE.rss;
  const name = sourceNames.get(sourceId) ?? sourceId;
  const label = style.label ?? initials(name);
  return (
    <span
      className="avatar"
      style={{
        width: size,
        height: size,
        background: style.bg,
        color: style.fg,
        fontSize: label.length > 2 ? size * 0.3 : size * 0.36,
      }}
      title={name}
      aria-hidden="true"
    >
      {label}
    </span>
  );
}

/** Overlapping avatars, like a group of people: one per source that picked up the story. */
export function AvatarStack({ sourceIds, size = 34 }: { sourceIds: string[]; size?: number }) {
  const unique = [...new Set(sourceIds)].slice(0, 3);
  return (
    <span className="avatar-stack">
      {unique.map((id) => (
        <SourceAvatar key={id} sourceId={id} size={size} />
      ))}
    </span>
  );
}

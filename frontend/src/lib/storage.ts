// Per-device conveniences only (dismissed tips, visited tabs). Storage can be unavailable
// (private mode, blocked), so every access is guarded and the app works without it.
export function getFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(`oyt:${key}`) === "1";
  } catch {
    return false;
  }
}

export function setFlag(key: string): void {
  try {
    window.localStorage.setItem(`oyt:${key}`, "1");
  } catch {
    /* ignore */
  }
}

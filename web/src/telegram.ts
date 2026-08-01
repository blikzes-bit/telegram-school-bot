/**
 * Minimal typings + helpers for the Telegram Mini App SDK.
 *
 * We only ever read ``initData`` (the signed string) — never ``initDataUnsafe``
 * — and the theme parameters. ``initData`` is sent to the backend, which is the
 * only place it is trusted after signature verification.
 */
export interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  themeParams: Record<string, string>;
  ready: () => void;
  expand: () => void;
  onEvent: (event: string, cb: () => void) => void;
  offEvent: (event: string, cb: () => void) => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function getWebApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp;
}

/** The signed initData string, or "" when opened outside Telegram. */
export function getInitData(): string {
  return getWebApp()?.initData ?? "";
}

/**
 * Apply Telegram theme parameters as CSS variables and the light/dark class so
 * the UI matches the surrounding Telegram client. Safe to call when not running
 * inside Telegram (falls back to the OS ``prefers-color-scheme``).
 */
export function applyTelegramTheme(): void {
  const app = getWebApp();
  const root = document.documentElement;

  if (app) {
    app.ready();
    app.expand();
    root.dataset.theme = app.colorScheme;
    for (const [key, value] of Object.entries(app.themeParams ?? {})) {
      root.style.setProperty(`--tg-${key.replace(/_/g, "-")}`, value);
    }
  } else {
    const prefersDark = window.matchMedia?.(
      "(prefers-color-scheme: dark)",
    ).matches;
    root.dataset.theme = prefersDark ? "dark" : "light";
  }
}

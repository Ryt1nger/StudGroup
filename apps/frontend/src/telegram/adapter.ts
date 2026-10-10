/**
 * The only surface the application uses to talk to Telegram. Components never touch
 * `window.Telegram` directly (enforced by ESLint).
 */
export type ColorScheme = 'light' | 'dark';

export interface SafeArea {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface TelegramAdapter {
  /** True when running inside a real Telegram WebApp (not the dev mock). */
  readonly isTelegram: boolean;
  readonly isMock: boolean;
  /** Raw, signed `initData` string. Sent to the backend as-is. Empty when unavailable. */
  readonly initData: string;
  /** Untrusted navigation hint only (never used for authentication or authorization). */
  readonly startParam: string | null;
  readonly platform: string;
  /** True when a native Telegram BackButton can be used (Bot API 6.1+). */
  readonly supportsBackButton: boolean;
  readonly colorScheme: ColorScheme;
  /** Optional dev-only appearance control; never changes Telegram or authentication. */
  setColorScheme?(scheme: ColorScheme): void;
  /** Tells Telegram the app is ready and expands the viewport. */
  ready(): void;
  onThemeChange(listener: (scheme: ColorScheme) => void): () => void;
  safeArea(): SafeArea;
  onViewportChange(listener: () => void): () => void;
  /** Shows the native back button; returns an unsubscribe/hide function. Null when unsupported. */
  showBackButton(onClick: () => void): (() => void) | null;
  /** Opens a t.me link through Telegram. Caller must have validated the URL. */
  openTelegramLink(url: string): void;
  /** Opens a validated external HTTPS link through Telegram or the browser fallback. */
  openExternalLink(url: string): void;
  haptic(kind: 'success' | 'error' | 'light'): void;
}

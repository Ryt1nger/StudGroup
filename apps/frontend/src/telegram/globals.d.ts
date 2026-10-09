/** Minimal typing of the official Telegram WebApp bridge fields used by realAdapter.ts. */
interface TelegramWebAppBackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
  isVisible: boolean;
}
interface TelegramWebAppInset {
  top: number;
  bottom: number;
  left: number;
  right: number;
}
interface TelegramWebApp {
  initData: string;
  initDataUnsafe: { start_param?: string };
  platform: string;
  colorScheme: 'light' | 'dark';
  version: string;
  isVersionAtLeast(version: string): boolean;
  ready(): void;
  expand(): void;
  onEvent(event: string, cb: () => void): void;
  offEvent(event: string, cb: () => void): void;
  BackButton: TelegramWebAppBackButton;
  openTelegramLink(url: string): void;
  openLink?(url: string, options?: { try_instant_view?: boolean }): void;
  safeAreaInset?: TelegramWebAppInset;
  contentSafeAreaInset?: TelegramWebAppInset;
  HapticFeedback?: {
    notificationOccurred(type: 'error' | 'success' | 'warning'): void;
    impactOccurred(style: 'light' | 'medium' | 'heavy'): void;
  };
}
interface Window {
  Telegram?: { WebApp?: TelegramWebApp };
}

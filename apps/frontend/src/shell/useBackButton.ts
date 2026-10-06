import { useEffect } from 'react';
import { useTelegram } from '../telegram/TelegramProvider';

/**
 * Wires the native Telegram BackButton. Returns true when Telegram provides it, false when the
 * UI must render its own back control (ordinary browser / mock / old Telegram versions).
 */
export function useBackButton(onBack: () => void): boolean {
  const { adapter } = useTelegram();
  const native = adapter?.supportsBackButton === true;
  useEffect(() => {
    if (!native || !adapter) return;
    const dispose = adapter.showBackButton(onBack);
    return () => {
      dispose?.();
    };
  }, [adapter, native, onBack]);
  return native;
}

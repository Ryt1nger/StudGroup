import { useCallback, useEffect, useState } from 'react';

export interface Display {
  text: 's' | 'm' | 'l';
  density: 'normal' | 'compact';
  motion: boolean;
}

const DEFAULT: Display = { text: 'm', density: 'normal', motion: true };
// Only an appearance preference, like the theme: never learning data or credentials.
const COOKIE = 'studgroup_display';

function read(): Display {
  try {
    const raw = document.cookie.split('; ').find((part) => part.startsWith(`${COOKIE}=`))?.slice(COOKIE.length + 1);
    if (!raw) return DEFAULT;
    const parsed = JSON.parse(decodeURIComponent(raw)) as Partial<Display>;
    return {
      text: parsed.text === 's' || parsed.text === 'l' ? parsed.text : 'm',
      density: parsed.density === 'compact' ? 'compact' : 'normal',
      motion: parsed.motion !== false,
    };
  } catch {
    return DEFAULT;
  }
}

function apply(display: Display) {
  const root = document.documentElement;
  root.dataset.text = display.text;
  root.dataset.density = display.density;
  root.dataset.motion = display.motion ? 'on' : 'off';
}

/** Text size, list density and animations: kept in a cookie and applied as attributes on <html>. */
export function useDisplay(): [Display, (patch: Partial<Display>) => void] {
  const [display, setDisplay] = useState<Display>(read);
  useEffect(() => apply(display), [display]);
  const update = useCallback((patch: Partial<Display>) => {
    setDisplay((current) => {
      const next = { ...current, ...patch };
      try {
        document.cookie = `${COOKIE}=${encodeURIComponent(JSON.stringify(next))}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
      } catch {
        /* The setting still applies for this visit when persistent cookies are blocked. */
      }
      return next;
    });
  }, []);
  return [display, update];
}

/** Applies the saved display settings once at start, without waiting for the profile to open. */
export function applySavedDisplay() {
  apply(read());
}

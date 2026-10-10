import type { ReactNode } from "react";
import { useInView } from "./hooks";

/** Маркер: подчёркивание-выделение «прорисовывается», когда фраза появляется на экране. */
export function Mark({ children, when }: { children: ReactNode; when?: boolean }) {
  const [ref, on] = useInView<HTMLSpanElement>(0.6);
  return (
    <span ref={ref} className={`mk ${on || when ? "on" : ""}`}>
      {children}
    </span>
  );
}

export const TgIcon = () => (
  <svg className="tg-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path fill="currentColor" d="M21.9 4.3 18.7 19.4c-.2 1-.9 1.3-1.7.8l-4.8-3.5-2.3 2.2c-.3.3-.5.5-1 .5l.3-4.9 8.9-8c.4-.3-.1-.5-.6-.2L6.5 13 1.8 11.5c-1-.3-1-1 .2-1.5L20.3 2.9c.9-.3 1.6.2 1.6 1.4Z" />
  </svg>
);

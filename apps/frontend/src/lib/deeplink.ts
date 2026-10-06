const PATTERN = /^hw_([0-9a-f]{32})$/;

/** Parses the untrusted `start_param` navigation hint into a homework UUID, or null. */
export function homeworkIdFromStartParam(param: string | null | undefined): string | null {
  const match = param ? PATTERN.exec(param) : null;
  const hex = match?.[1];
  if (!hex) return null;
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

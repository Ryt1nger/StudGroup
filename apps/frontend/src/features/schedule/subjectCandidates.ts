const fold = (value: string) => value.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е');

export function subjectCandidates(fragment: string, names: readonly string[]): string[] {
  const parts = fold(fragment).split(/\.{3}|…/);
  if (parts.length < 2) return [];
  const [prefix, suffix] = [parts[0] ?? '', parts.at(-1) ?? ''];
  if (!prefix && !suffix) return [];
  return names.filter((name) => {
    const candidate = fold(name);
    return !candidate.includes('...') && !candidate.includes('…')
      && candidate.startsWith(prefix) && candidate.endsWith(suffix)
      && candidate !== fold(fragment);
  });
}

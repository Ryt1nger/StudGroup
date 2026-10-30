import type { MaterialLink } from '@studgroup/shared-types';

/** Photos that are the task itself: the card shows them instead of a text description. */
export function photoMaterials(items?: MaterialLink[]): MaterialLink[] {
  return (items ?? []).filter((item) => item.kind === 'photo' && item.replaces_description === true);
}

/** Everything the materials list still shows when photos are rendered as the card body. */
export function remainingMaterials(items?: MaterialLink[]): MaterialLink[] | undefined {
  if (items === undefined) return undefined;
  const shown = new Set(photoMaterials(items).map((item) => item.id));
  return items.filter((item) => !shown.has(item.id));
}

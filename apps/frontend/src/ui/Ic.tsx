import { ICONS, type IconName } from './iconData';

/** One icon of the design: a real <svg> element whose static markup never carries user text. */
export function Ic({ name, size, className }: { name: IconName; size?: number; className?: string }) {
  const icon = ICONS[name]!;
  return (
    <svg
      {...icon.attrs}
      width={size}
      height={size}
      className={className}
      aria-hidden="true"
      focusable="false"
      dangerouslySetInnerHTML={{ __html: icon.inner }}
    />
  );
}

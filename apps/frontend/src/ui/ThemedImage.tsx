import type { ThemedAsset } from '../assets';
import { useTheme } from '../telegram/TelegramProvider';

interface Props {
  asset: ThemedAsset;
  alt?: string;
  className?: string;
  width?: number;
  height?: number;
}

/** Picks the light/dark raster that matches the active theme. Decorative by default. */
export function ThemedImage({ asset, alt = '', className, width, height }: Props) {
  const theme = useTheme();
  return (
    <img
      src={asset[theme]}
      alt={alt}
      className={className}
      width={width}
      height={height}
      decoding="async"
      aria-hidden={alt === '' ? true : undefined}
    />
  );
}

type LogoSize = 'small' | 'medium' | 'large';
/** `mono` matches the design doc's uploads/logo.svg: every path one flat tint. */
type LogoVariant = 'color' | 'light' | 'dark' | 'mono';

interface LogoProps {
  size?: LogoSize;
  variant?: LogoVariant;
}

function getColor(variant: LogoVariant) {
  switch (variant) {
    case 'light':
    case 'dark':
    case 'mono':
      return 'currentColor';
    case 'color':
      return null;
    default:
      return null;
  }
}

function getWidth(size: LogoSize): number {
  switch (size) {
    case 'small':
      return 36;
    case 'medium':
      return 64;
    case 'large':
      return 128;
    default:
      return 120;
  }
}

export default function Logo({ size = 'medium', variant = 'color' }: LogoProps) {
  const logoWidth = getWidth(size);
  const logoColor = getColor(variant);

  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="175 90 375 320" width={logoWidth}>
      <g stroke="#1A3617" strokeWidth="0" strokeLinejoin="round" strokeLinecap="round">
        {/* O Base / C Shape */}
        <path
          d="M 280 150 A 100 100 0 1 1 280 350 A 100 100 0 1 1 280 150 Z M 280 190 A 60 60 0 1 0 280 310 A 60 60 0 1 0 280 190 Z"
          fill={logoColor ?? '#360185'}
          stroke="#1A3617"
        />

        {/* Radar Arc 1 */}
        <path
          d="M 406.15 161.67 A 154 154 0 0 1 406.15 338.33 L 373.38 315.39 A 114 114 0 0 0 373.38 184.61 Z"
          fill={logoColor ?? '#8F0177'}
        />

        {/* Radar Arc 2 */}
        <path
          d="M 450.38 130.70 A 208 208 0 0 1 450.38 369.30 L 417.62 346.36 A 168 168 0 0 0 417.62 153.64 Z"
          fill={logoColor ?? '#DE1A58'}
        />

        {/* Radar Arc 3 */}
        <path
          d="M 494.62 99.72 A 262 262 0 0 1 494.62 400.28 L 461.85 377.33 A 222 222 0 0 0 461.85 122.67 Z"
          fill={logoColor ?? '#F4B342'}
        />
      </g>
    </svg>
  );
}

import type { SpinQuality } from '../../types/shot';
import { t } from '../../i18n';
import './MetricCard.css';

export interface MetricCardProps {
  value: string | number;
  unit?: string;
  label: string;
  subtext?: string;
  variant?: 'default' | 'emphasis';
  size?: 'standard' | 'hero';
  /**
   * Instrument-panel layouts (design doc 6a / 7a) lead with the label and put
   * the number underneath; the original card leads with the number.
   */
  labelPosition?: 'below' | 'above';
  /** Modeled value. Shown as an ≈ mark; measured values have no mark. */
  estimated?: boolean;
  /** Camera/radar preview. Shown as a flask mark; not a confidence word. */
  experimental?: boolean;
  confidence?: SpinQuality | null;
  /** Override confidence copy while preserving its dot level. */
  confidenceLabel?: string;
  /** Renders the card as a button. Used by 6a's tap-a-tile-to-promote-it grid. */
  onClick?: () => void;
  /** Marks an interactive card as the currently promoted one (`aria-pressed`). */
  selected?: boolean;
}

function visuallyHiddenLabel(text: string, className: string) {
  return <span className={className}>{text}</span>;
}

export function EstimatedMark() {
  return (
    <span className="metric-card__estimated" title={t('metric.estimated')}>
      <svg viewBox="0 0 16 10" aria-hidden="true">
        <path d="M1 3.1c2.2-1.6 4.4 1.6 6.6 0s4.4-1.6 6.6 0" />
        <path d="M1 7.4c2.2-1.6 4.4 1.6 6.6 0s4.4-1.6 6.6 0" />
      </svg>
      {visuallyHiddenLabel(t('metric.estimated'), 'metric-card__estimated-label')}
    </span>
  );
}

export function ExperimentalMark() {
  return (
    <span className="metric-card__experimental" title={t('metric.experimental')}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M6 1.5h4" />
        <path d="M7 1.5v3.2M9 1.5v3.2" />
        <path d="M5.5 4.7h5L13 12.2a2.4 2.4 0 0 1-2.2 2.3H5.2A2.4 2.4 0 0 1 3 12.2L5.5 4.7z" />
        <path d="M4.3 10.6h7.4" />
      </svg>
      {visuallyHiddenLabel(t('metric.experimental'), 'metric-card__experimental-label')}
    </span>
  );
}

export function MetricCard({
  value,
  unit,
  label,
  subtext,
  variant = 'default',
  size = 'standard',
  labelPosition = 'below',
  confidence,
  confidenceLabel,
  onClick,
  selected,
  estimated,
  experimental,
}: MetricCardProps) {
  const classes = ['metric-card', `metric-card--${variant}`, `metric-card--label-${labelPosition}`];
  if (size === 'hero') {
    classes.push('metric-card--hero');
  }
  if (onClick) {
    classes.push('metric-card--interactive');
  }
  if (selected) {
    classes.push('metric-card--selected');
  }

  const isExperimental = experimental === true || confidence === 'experimental';
  const measuredConfidence = confidence && confidence !== 'experimental' ? confidence : null;

  const label_ = (
    <span className="metric-card__label">
      {label}
      {estimated ? <EstimatedMark /> : null}
      {isExperimental ? <ExperimentalMark /> : null}
    </span>
  );
  const meta = (
    <>
      {subtext ? <span className="metric-card__subtext metric-card__confidence-label">{subtext}</span> : null}
      {measuredConfidence ? (
        <div className={`metric-card__confidence metric-card__confidence--${measuredConfidence}`}>
          <span className="metric-card__confidence-dots">
            <span className="dot filled" />
            <span
              className={`dot ${measuredConfidence === 'medium' || measuredConfidence === 'high' ? 'filled' : ''}`}
            />
            <span className={`dot ${measuredConfidence === 'high' ? 'filled' : ''}`} />
          </span>
          {isExperimental ? null : (
            <span className="metric-card__confidence-label">{confidenceLabel ?? measuredConfidence}</span>
          )}
        </div>
      ) : null}
    </>
  );
  const body = (
    <>
      {labelPosition === 'above' ? label_ : null}
      <div className="metric-card__value-row">
        <span className="metric-card__value">{value}</span>
        {unit ? <span className="metric-card__unit">{unit}</span> : null}
      </div>
      {labelPosition === 'below' ? label_ : null}
      {labelPosition === 'above' ? <div className="metric-card__meta">{meta}</div> : meta}
    </>
  );

  if (onClick) {
    return (
      <button type="button" className={classes.join(' ')} aria-pressed={selected ?? false} onClick={onClick}>
        {body}
      </button>
    );
  }

  return <div className={classes.join(' ')}>{body}</div>;
}

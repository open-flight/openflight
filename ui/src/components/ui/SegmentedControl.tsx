import './SegmentedControl.css';

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: ReadonlyArray<{ id: T; label: string }>;
  onChange: (id: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="segmented-control" role="group" aria-label={ariaLabel}>
      {options.map((option) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            className={`segmented-control__button${active ? ' segmented-control__button--active' : ''}`}
            aria-pressed={active}
            onClick={() => onChange(option.id)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

import { useState, type CSSProperties } from 'react';
import { clubGroupLabel } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { PanelAction } from './PanelAction';
import { initialPickerSection, pickerGridRows, type PickerSection } from './pickerSections';

interface PickerOverlayProps {
  title: string;
  selectedId: string;
  sections: ReadonlyArray<PickerSection>;
  onSelect: (id: string) => void;
  onClose: () => void;
  /** Word-length labels (training implements) use a slightly smaller type size. */
  wide?: boolean;
}

/**
 * Full-screen picker from design doc 6a (`clubsOpen6`): a titled sheet of
 * hairline-bordered option buttons grouped by tab. Four columns span the
 * overlay; row height is capped so irons stay on screen.
 */
export function PickerOverlay({ title, selectedId, sections, onSelect, onClose, wide = false }: PickerOverlayProps) {
  const { t } = useI18n();
  const [sectionName, setSectionName] = useState(() => initialPickerSection(sections, selectedId));
  const activeSection = sections.find((section) => section.name === sectionName) ?? sections[0];
  const options = activeSection?.options ?? [];
  const rows = pickerGridRows(sections);

  return (
    <div
      className={`picker-overlay${wide ? ' picker-overlay--wide' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="picker-overlay__header">
        <span className="picker-overlay__title">{title}</span>
        <button
          type="button"
          className="picker-overlay__close"
          onClick={onClose}
          aria-label={t('picker.close', { title })}
        >
          ✕
        </button>
      </div>
      {sections.length > 1 ? (
        <div className="picker-overlay__tabs" role="group" aria-label={t('picker.groups')}>
          {sections.map((section) => {
            const active = section.name === sectionName;
            return (
              <PanelAction
                key={section.name}
                variant={active ? 'primary' : 'secondary'}
                aria-pressed={active}
                onClick={() => setSectionName(section.name)}
              >
                {clubGroupLabel(section.name)}
              </PanelAction>
            );
          })}
        </div>
      ) : null}
      <div className="picker-overlay__body">
        <div className="picker-overlay__grid" style={{ '--picker-rows': String(rows) } as CSSProperties}>
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`picker-overlay__option${option.id === selectedId ? ' picker-overlay__option--selected' : ''}`}
              aria-pressed={option.id === selectedId}
              onClick={() => onSelect(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

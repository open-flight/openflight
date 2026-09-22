import { useState } from 'react';
import { PanelAction } from './PanelAction';
import { useI18n } from '../../i18n/useI18n';

const PROFILE_NAME_MAX = 40;

const LETTER_ROWS = [
  ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
  ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'],
  ['Z', 'X', 'C', 'V', 'B', 'N', 'M'],
] as const;

const NUMBER_ROWS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['-', "'", '.', '_'],
] as const;

function appendName(name: string, chunk: string): string {
  const room = PROFILE_NAME_MAX - name.length;
  if (room <= 0) return name;
  return name + chunk.slice(0, room);
}

interface ProfileNameDialogProps {
  /** Add and rename differ only in copy and initial value, so one dialog serves both. */
  mode: 'add' | 'rename';
  name: string;
  onChange: (name: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ProfileNameDialog({ mode, name, onChange, onConfirm, onCancel }: ProfileNameDialogProps) {
  const { t } = useI18n();
  const [shifted, setShifted] = useState(true);
  const [symbols, setSymbols] = useState(false);
  const canConfirm = Boolean(name.trim());
  const title = mode === 'add' ? t('menu.addProfile') : t('menu.renameProfile');
  const rows = symbols ? NUMBER_ROWS : LETTER_ROWS;

  const insertChar = (raw: string) => {
    const isLetter = /^[a-z]$/i.test(raw);
    const chunk = isLetter ? (shifted ? raw.toUpperCase() : raw.toLowerCase()) : raw;
    onChange(appendName(name, chunk));
    if (isLetter && shifted) setShifted(false);
  };

  const insertSpace = () => {
    onChange(appendName(name, ' '));
    setShifted(true);
  };

  return (
    <div className="profile-name-modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="profile-name-modal__header">
        <span id="profile-name-title" className="profile-name-modal__title">
          {title}
        </span>
        <button
          type="button"
          className="profile-name-modal__close"
          aria-label={t('profiles.closeDialog')}
          onClick={onCancel}
        >
          ✕
        </button>
      </div>
      <input
        className="profile-name-modal__input"
        type="text"
        inputMode="none"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        autoFocus
        maxLength={PROFILE_NAME_MAX}
        placeholder={t('profiles.namePlaceholder')}
        value={name}
        aria-labelledby="profile-name-title"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && canConfirm) onConfirm();
        }}
      />
      <div className="profile-name-modal__keyboard" role="group" aria-label={t('keyboard.aria')}>
        {rows.map((row) => (
          <div className="profile-name-modal__key-row" key={row.join('')}>
            {row.map((key) => (
              <button key={key} type="button" className="profile-name-modal__key" onClick={() => insertChar(key)}>
                {key}
              </button>
            ))}
          </div>
        ))}
        <div className="profile-name-modal__key-row">
          {symbols ? (
            <button
              type="button"
              className="profile-name-modal__key profile-name-modal__key--mod"
              aria-label={t('keyboard.letters')}
              onClick={() => setSymbols(false)}
            >
              ABC
            </button>
          ) : (
            <>
              <button
                type="button"
                className={`profile-name-modal__key profile-name-modal__key--mod${shifted ? ' profile-name-modal__key--active' : ''}`}
                aria-label={t('keyboard.shift')}
                aria-pressed={shifted}
                onClick={() => setShifted((on) => !on)}
              >
                ⇧
              </button>
              <button
                type="button"
                className="profile-name-modal__key profile-name-modal__key--mod"
                aria-label={t('keyboard.numbers')}
                onClick={() => setSymbols(true)}
              >
                123
              </button>
            </>
          )}
          <button
            type="button"
            className="profile-name-modal__key profile-name-modal__key--wide"
            aria-label={t('keyboard.space')}
            onClick={insertSpace}
          />
          <button
            type="button"
            className="profile-name-modal__key profile-name-modal__key--mod"
            aria-label={t('keyboard.backspace')}
            onClick={() => onChange(name.slice(0, -1))}
          >
            ⌫
          </button>
        </div>
      </div>
      <div className="profile-name-modal__actions">
        <PanelAction disabled={!canConfirm} onClick={onConfirm}>
          {title}
        </PanelAction>
        <PanelAction variant="secondary" onClick={onCancel}>
          {t('shutdown.cancel')}
        </PanelAction>
      </div>
    </div>
  );
}

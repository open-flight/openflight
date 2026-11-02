import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n';

const LETTER_ROWS = [
  ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
  ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'],
  ['Z', 'X', 'C', 'V', 'B', 'N', 'M'],
] as const;

const NUMBER_ROWS = [
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['-', "'", '.', '_'],
] as const;

/** Append `chunk` without growing past `maxLength`. */
function appendCapped(value: string, chunk: string, maxLength: number): string {
  const room = maxLength - value.length;
  if (room <= 0) return value;
  return value + chunk.slice(0, room);
}

interface OnScreenKeyboardProps {
  value: string;
  maxLength: number;
  onChange: (value: string) => void;
  /** Start on the number row, e.g. for a loft in degrees. */
  numeric?: boolean;
}

/**
 * The kiosk has no system keyboard, so text entry goes through these keys.
 * Shift starts on and releases after one letter, and space re-arms it, so
 * names come out capitalised. Remount (change `key`) to reset the mode.
 */
export function OnScreenKeyboard({ value, maxLength, onChange, numeric = false }: OnScreenKeyboardProps) {
  const { t } = useI18n();
  const [shifted, setShifted] = useState(!numeric);
  const [symbols, setSymbols] = useState(numeric);
  const rows = symbols ? NUMBER_ROWS : LETTER_ROWS;

  const insertChar = (raw: string) => {
    const isLetter = /^[a-z]$/i.test(raw);
    const chunk = isLetter ? (shifted ? raw.toUpperCase() : raw.toLowerCase()) : raw;
    onChange(appendCapped(value, chunk, maxLength));
    if (isLetter && shifted) setShifted(false);
  };

  const insertSpace = () => {
    onChange(appendCapped(value, ' ', maxLength));
    setShifted(true);
  };

  return (
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
          onClick={() => onChange(value.slice(0, -1))}
        >
          ⌫
        </button>
      </div>
    </div>
  );
}

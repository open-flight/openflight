import { PanelAction } from './PanelAction';
import { OnScreenKeyboard } from './OnScreenKeyboard';
import { useI18n } from '../../i18n/useI18n';

const PROFILE_NAME_MAX = 40;

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
  const canConfirm = Boolean(name.trim());
  const title = mode === 'add' ? t('menu.addProfile') : t('menu.renameProfile');

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
      <OnScreenKeyboard value={name} maxLength={PROFILE_NAME_MAX} onChange={onChange} />
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

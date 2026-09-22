import { PanelAction } from './PanelAction';
import { useI18n } from '../../i18n/useI18n';

interface ClearSessionDialogProps {
  profileName: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ClearSessionDialog({ profileName, onConfirm, onCancel }: ClearSessionDialogProps) {
  const { t } = useI18n();

  return (
    <div className="clear-session-modal">
      <button
        type="button"
        className="clear-session-modal__scrim"
        aria-label={t('clearSession.close')}
        onClick={onCancel}
      />
      <div
        className="clear-session-modal__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="clear-session-title"
        aria-describedby="clear-session-detail"
      >
        <span id="clear-session-title" className="clear-session-modal__title">
          {t('clearSession.confirm', { name: profileName })}
        </span>
        <p id="clear-session-detail" className="clear-session-dialog__detail">
          {t('clearSession.detail', { name: profileName })}
        </p>
        <div className="clear-session-modal__actions">
          <PanelAction variant="danger" autoFocus onClick={onConfirm}>
            {t('app.clearSession')}
          </PanelAction>
          <PanelAction variant="secondary" onClick={onCancel}>
            {t('shutdown.cancel')}
          </PanelAction>
        </div>
      </div>
    </div>
  );
}

import { ProgressIndicator } from './ProgressIndicator';
import { useI18n } from '../i18n/useI18n';

export type ShutdownState = 'confirm' | 'pending' | 'error';

interface ShutdownDialogProps {
  state: ShutdownState;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ShutdownDialog({ state, onConfirm, onCancel }: ShutdownDialogProps) {
  const { t } = useI18n();

  if (state === 'pending') {
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={t('shutdown.pendingAria')}
        >
          <ProgressIndicator variant="dialog" title={t('shutdown.pendingTitle')} detail={t('shutdown.pendingDetail')} />
        </div>
      </div>
    );
  }

  const hasError = state === 'error';
  return (
    <div className="shutdown-overlay">
      <div className="shutdown-dialog" role="dialog" aria-modal="true" aria-labelledby="shutdown-dialog-title">
        <p id="shutdown-dialog-title">{hasError ? t('shutdown.error') : t('shutdown.confirm')}</p>
        {hasError ? <span className="shutdown-dialog__error">{t('shutdown.errorDetail')}</span> : null}
        <div className="shutdown-dialog__buttons">
          <button className="shutdown-dialog__confirm" onClick={onConfirm} autoFocus>
            {hasError ? t('shutdown.tryAgain') : t('shutdown.shutDown')}
          </button>
          <button className="shutdown-dialog__cancel" onClick={onCancel}>
            {t('shutdown.cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}

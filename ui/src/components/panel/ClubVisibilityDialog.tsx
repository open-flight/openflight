import { useRef, useState } from 'react';
import { PanelAction } from './PanelAction';
import { CLUBS_BY_TYPE } from '../../data/clubs';
import { clubGroupLabel } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useDragScroll } from '../../hooks/useDragScroll';

interface ClubVisibilityDialogProps {
  /** Name shown in the dialog title, e.g. "Cormac's clubs". */
  profileName: string;
  /** Currently enabled club ids, or undefined/empty for "all clubs". */
  enabledClubIds: string[] | undefined;
  onSave: (clubIds: string[]) => void;
  onCancel: () => void;
}

const ALL_CLUB_IDS = Object.values(CLUBS_BY_TYPE)
  .flat()
  .map((club) => club.id);

export function ClubVisibilityDialog({
  profileName,
  enabledClubIds,
  onSave,
  onCancel,
}: ClubVisibilityDialogProps) {
  const { t } = useI18n();
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(bodyRef);
  const [checked, setChecked] = useState<Set<string>>(
    () => new Set(enabledClubIds && enabledClubIds.length > 0 ? enabledClubIds : ALL_CLUB_IDS)
  );
  const title = t('clubVisibility.title', { name: profileName });

  const toggleClub = (clubId: string) => {
    setChecked((current) => {
      const next = new Set(current);
      if (next.has(clubId)) {
        next.delete(clubId);
      } else {
        next.add(clubId);
      }
      return next;
    });
  };

  const selectAll = () => setChecked(new Set(ALL_CLUB_IDS));

  const handleSave = () => onSave(ALL_CLUB_IDS.filter((id) => checked.has(id)));

  return (
    <div className="club-visibility-modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="club-visibility-modal__header">
        <span id="club-visibility-title" className="club-visibility-modal__title">
          {title}
        </span>
        <button
          type="button"
          className="club-visibility-modal__close"
          aria-label={t('profiles.closeDialog')}
          onClick={onCancel}
        >
          ✕
        </button>
      </div>
      <div
        className="club-visibility-modal__body"
        role="group"
        aria-labelledby="club-visibility-title"
        ref={bodyRef}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
        <div className="club-visibility-modal__select-all">
          <button type="button" className="club-visibility-modal__select-all-btn" onClick={selectAll}>
            {t('clubVisibility.selectAll')}
          </button>
        </div>
        {Object.entries(CLUBS_BY_TYPE).map(([groupName, clubs]) => (
          <div className="club-visibility-modal__group" key={groupName}>
            <span className="club-visibility-modal__group-name">{clubGroupLabel(groupName)}</span>
            <div className="club-visibility-modal__group-clubs">
              {clubs.map((club) => {
                const selected = checked.has(club.id);
                return (
                  <button
                    key={club.id}
                    type="button"
                    className={`club-visibility-modal__club${selected ? ' club-visibility-modal__club--selected' : ''}`}
                    aria-pressed={selected}
                    onClick={() => toggleClub(club.id)}
                  >
                    {club.label}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <div className="club-visibility-modal__actions">
        <PanelAction disabled={checked.size === 0} onClick={handleSave}>
          {t('clubVisibility.save')}
        </PanelAction>
        <PanelAction variant="secondary" onClick={onCancel}>
          {t('shutdown.cancel')}
        </PanelAction>
      </div>
    </div>
  );
}

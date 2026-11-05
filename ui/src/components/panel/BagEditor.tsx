import { useRef, useState } from 'react';
import { getClubName } from '../../data/clubs';
import {
  BAG_ANGLE_RANGES,
  BAG_HANDS,
  MAX_BAG_CLUBS,
  clubShortLabel,
  customClubMakeModel,
  customClubSpecs,
  formatDegrees,
  type BagHand,
  type BagSpecLabels,
  type CustomClub,
  type CustomClubPayload,
} from '../../data/bag';
import { useDragScroll } from '../../hooks/useDragScroll';
import type { MessageKey } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { OnScreenKeyboard } from './OnScreenKeyboard';
import { PanelAction } from './PanelAction';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';
import {
  BAG_FORM_FIELDS,
  draftErrors,
  draftFromCustomClub,
  fieldMaxLength,
  isAngleField,
  newBagClubDraft,
  payloadFromDraft,
  type BagClubDraft,
  type BagFormField,
} from './bagDraft';

const FIELD_LABEL_KEYS: Record<BagFormField, MessageKey> = {
  brand: 'bag.brand',
  model: 'bag.model',
  loft_deg: 'bag.loft',
  bounce_deg: 'bag.bounce',
  lie_deg: 'bag.lie',
  shaft: 'bag.shaft',
  notes: 'bag.notes',
};

const HAND_BUTTON_KEYS: Record<BagHand, MessageKey> = {
  right: 'bag.handRight',
  left: 'bag.handLeft',
};

/** A new club defaults to the most common one to carry twice while testing. */
const DEFAULT_NEW_CLUB = 'sw';

interface BagEditorProps {
  profileName: string;
  /** This profile's clubs. */
  bag: CustomClub[];
  /** Each club type's nominal loft: what a blank loft inherits. */
  defaultLofts: Record<string, number>;
  /** The server's reason for rejecting the last save or removal, if any. */
  error: string | null;
  /** Add (no `id`) or update one club. The server broadcasts the result as `clubs`. */
  onSave: (club: CustomClubPayload) => void;
  onRemove: (clubId: string) => void;
  onClose: () => void;
}

/**
 * Full-screen bag manager for one profile: the clubs it carries, each with
 * its own brand, model and specs. Every add, edit and removal saves at once,
 * so there is never an unsaved bag to lose.
 */
export function BagEditor({ profileName, bag, defaultLofts, error, onSave, onRemove, onClose }: BagEditorProps) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<BagClubDraft | null>(null);

  if (draft) {
    return (
      <BagClubForm
        draft={draft}
        defaultLoft={defaultLofts[draft.club] ?? null}
        onChange={setDraft}
        onCancel={() => setDraft(null)}
        onSave={(club) => {
          onSave(club);
          setDraft(null);
        }}
      />
    );
  }

  const title = t('bag.titleNamed', { name: profileName });
  const full = bag.length >= MAX_BAG_CLUBS;

  return (
    <div className="profile-name-modal bag-editor" role="dialog" aria-modal="true" aria-label={title}>
      <div className="profile-name-modal__header">
        <span className="profile-name-modal__title">{title}</span>
        <button
          type="button"
          className="profile-name-modal__close"
          aria-label={t('profiles.closeDialog')}
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      {error ? (
        <p className="bag-editor__error" role="alert">
          {t('bag.saveFailed', { reason: error })}
        </p>
      ) : null}
      <BagClubList
        bag={bag}
        onEdit={(club) => setDraft(draftFromCustomClub(club))}
        onRemove={(club) => onRemove(club.id)}
      />
      <div className="profile-name-modal__actions">
        <PanelAction disabled={full} onClick={() => setDraft(newBagClubDraft(DEFAULT_NEW_CLUB))}>
          {full ? t('bag.full', { max: MAX_BAG_CLUBS }) : t('bag.add')}
        </PanelAction>
        <PanelAction variant="secondary" onClick={onClose}>
          {t('bag.done')}
        </PanelAction>
      </div>
    </div>
  );
}

interface BagClubListProps {
  bag: CustomClub[];
  onEdit: (club: CustomClub) => void;
  onRemove: (club: CustomClub) => void;
}

function BagClubList({ bag, onEdit, onRemove }: BagClubListProps) {
  const { t } = useI18n();
  const listRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(listRef);
  const specLabels: BagSpecLabels = {
    loft_deg: t('bag.loftShort'),
    bounce_deg: t('bag.bounceShort'),
    lie_deg: t('bag.lieShort'),
    right: t('bag.rightHanded'),
    left: t('bag.leftHanded'),
  };

  if (bag.length === 0) {
    return (
      <div className="panel__body--empty bag-editor__empty">
        <span className="panel__empty-title">{t('bag.empty')}</span>
        <span className="panel__empty-detail">{t('bag.emptyDetail')}</span>
      </div>
    );
  }

  return (
    <div
      className="bag-editor__list"
      role="list"
      ref={listRef}
      onPointerDown={dragScroll.onPointerDown}
      onPointerMove={dragScroll.onPointerMove}
      onPointerUp={dragScroll.onPointerUp}
      onPointerCancel={dragScroll.onPointerCancel}
      onClickCapture={dragScroll.onClickCapture}
    >
      {bag.map((club) => {
        const name = getClubName(club.club);
        const makeModel = customClubMakeModel(club);
        const specs = customClubSpecs(club, specLabels);
        return (
          <div className="bag-editor__row" role="listitem" key={club.id}>
            <button
              type="button"
              className="bag-editor__row-main"
              aria-label={t('bag.editNamed', {
                name: [name, formatDegrees(club.loft_deg), makeModel].filter(Boolean).join(' '),
              })}
              onClick={() => onEdit(club)}
            >
              <span className="bag-editor__club">{clubShortLabel(club.club)}</span>
              <span className="bag-editor__text">
                <span className="bag-editor__name">{makeModel || name}</span>
                {specs ? <span className="bag-editor__specs">{specs}</span> : null}
              </span>
            </button>
            <button
              type="button"
              className="bag-editor__remove"
              aria-label={t('bag.removeNamed', { name: makeModel || name })}
              onClick={() => onRemove(club)}
            >
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
}

interface BagClubFormProps {
  draft: BagClubDraft;
  /** The club type's nominal loft, shown while the loft field is blank. */
  defaultLoft: number | null;
  onChange: (draft: BagClubDraft) => void;
  onSave: (club: CustomClubPayload) => void;
  onCancel: () => void;
}

function BagClubForm({ draft, defaultLoft, onChange, onSave, onCancel }: BagClubFormProps) {
  const { t } = useI18n();
  const [focused, setFocused] = useState<BagFormField>('brand');
  const [typePickerOpen, setTypePickerOpen] = useState(false);
  const errors = draftErrors(draft);
  const valid = Object.keys(errors).length === 0;
  const title = draft.id ? t('bag.edit') : t('bag.add');

  // A blank loft inherits the type's; say what it will be rather than show nothing.
  const placeholder = (field: BagFormField): string =>
    field === 'loft_deg' && defaultLoft !== null ? t('bag.defaultLoft', { loft: formatDegrees(defaultLoft) }) : '';

  const errorText = (field: BagFormField): string => {
    if (!isAngleField(field) || !errors[field]) return '';
    const { min, max } = BAG_ANGLE_RANGES[field];
    return errors[field] === 'number' ? t('bag.errorNumber') : t('bag.errorRange', { min, max });
  };

  return (
    <div className="profile-name-modal bag-editor" role="dialog" aria-modal="true" aria-label={title}>
      <div className="profile-name-modal__header">
        <span className="profile-name-modal__title">{title}</span>
        <button
          type="button"
          className="profile-name-modal__close"
          aria-label={t('profiles.closeDialog')}
          onClick={onCancel}
        >
          ✕
        </button>
      </div>
      <div className="bag-editor__fields">
        <button
          type="button"
          className="bag-editor__field bag-editor__field--club"
          aria-label={t('bag.chooseClub', { name: getClubName(draft.club) })}
          onClick={() => setTypePickerOpen(true)}
        >
          <span className="bag-editor__field-label">{t('bag.club')}</span>
          <span className="bag-editor__field-value">{getClubName(draft.club)}</span>
        </button>
        <div className="bag-editor__field bag-editor__field--hand" role="group" aria-labelledby="bag-hand-label">
          <span id="bag-hand-label" className="bag-editor__field-label">
            {t('bag.hand')}
          </span>
          <span className="bag-editor__hand-options">
            {BAG_HANDS.map((hand) => {
              const selected = draft.hand === hand;
              return (
                <button
                  key={hand}
                  type="button"
                  className={`bag-editor__hand${selected ? ' bag-editor__hand--selected' : ''}`}
                  aria-pressed={selected}
                  // Tapping the chosen hand again clears it: hand is optional.
                  onClick={() => onChange({ ...draft, hand: selected ? '' : hand })}
                >
                  {t(HAND_BUTTON_KEYS[hand])}
                </button>
              );
            })}
          </span>
        </div>
        {BAG_FORM_FIELDS.map((field) => {
          const error = errorText(field);
          const selected = field === focused;
          return (
            <button
              key={field}
              type="button"
              className={`bag-editor__field${field === 'notes' ? ' bag-editor__field--wide' : ''}${selected ? ' bag-editor__field--focused' : ''}${error ? ' bag-editor__field--invalid' : ''}`}
              aria-pressed={selected}
              aria-invalid={error ? true : undefined}
              onClick={() => setFocused(field)}
            >
              <span className="bag-editor__field-label">
                {t(FIELD_LABEL_KEYS[field])}
                {isAngleField(field) ? ' (°)' : ''}
              </span>
              {draft[field] ? (
                <span className="bag-editor__field-value">{draft[field]}</span>
              ) : (
                <span className="bag-editor__field-value bag-editor__field-value--placeholder">
                  {placeholder(field) || ' '}
                </span>
              )}
              {error ? <span className="bag-editor__field-error">{error}</span> : null}
            </button>
          );
        })}
      </div>
      <OnScreenKeyboard
        key={focused}
        value={draft[focused]}
        maxLength={fieldMaxLength(focused)}
        numeric={isAngleField(focused)}
        onChange={(value) => onChange({ ...draft, [focused]: value })}
      />
      <div className="profile-name-modal__actions">
        <PanelAction
          disabled={!valid}
          onClick={() => {
            const club = payloadFromDraft(draft);
            if (club) onSave(club);
          }}
        >
          {t('bag.save')}
        </PanelAction>
        <PanelAction variant="secondary" onClick={onCancel}>
          {t('shutdown.cancel')}
        </PanelAction>
      </div>
      {typePickerOpen ? (
        <PickerOverlay
          title={t('app.selectClub')}
          selectedId={draft.club}
          sections={clubSections()}
          onSelect={(club) => {
            onChange({ ...draft, club });
            setTypePickerOpen(false);
          }}
          onClose={() => setTypePickerOpen(false)}
        />
      ) : null}
    </div>
  );
}

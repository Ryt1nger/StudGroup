import { useState } from 'react';
import { useNavigate } from 'react-router';
import { isApiRequestError } from '../../api/errors';
import { useDeleteCard, useRestoreCard } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { ConfirmModal } from '../../ui/Blocks';
import { useFeedback } from '../../ui/Feedback';
import { Ic } from '../../ui/Ic';
import { Float } from '../../shell/AppRoot';

/** The trash button of a card header, with its confirmation: headman and deputy only (the server checks). */
export function DeleteCardButton({ kind, id, revision, backTo }: { kind: 'homework' | 'event'; id: string; revision: number; backTo: string }) {
  const [asking, setAsking] = useState(false);
  const navigate = useNavigate();
  const { toast, undo } = useFeedback();
  const restore = useRestoreCard();
  const remove = useDeleteCard(kind, id);
  return (
    <>
      <button type="button" className="icon-btn danger" aria-label={ru.edit.remove.button} disabled={remove.isPending} onClick={() => setAsking(true)}>
        <Ic name="trash" size={18} />
      </button>
      {asking ? (
        <Float>
          <ConfirmModal
            icon="trash"
            title={ru.edit.remove.title}
            body={ru.edit.remove.body}
            confirm={remove.isPending ? ru.edit.remove.deleting : ru.edit.remove.confirm}
            cancel={ru.edit.remove.keep}
            onCancel={() => setAsking(false)}
            onConfirm={() =>
              remove.mutate(revision, {
                onSuccess: (removal) => {
                  setAsking(false);
                  undo('Карточка удалена', () => {
                    restore(removal.undo_id).then(
                      () => toast('Карточка возвращена'),
                      () => toast('Не удалось вернуть карточку'),
                    );
                  });
                  void navigate(backTo, { replace: true });
                },
                onError: (error) => {
                  setAsking(false);
                  toast(isApiRequestError(error) && error.code === 'revision_conflict' ? ru.edit.remove.conflict : ru.edit.remove.failed);
                },
              })
            }
          />
        </Float>
      ) : null}
    </>
  );
}

import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { ru } from '../../i18n/ru';
import { isAllowedTelegramUrl } from '../../lib/telegramUrl';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Button } from '../../ui/Button';
import { Paperclip, Pencil } from '../../ui/icons';
import styles from './CardEditor.module.css';

/**
 * "Добавить файл": the file stays in Telegram, so this opens the bot, which waits for it and attaches
 * it to the card after the headman confirms. The deep link comes from the server (it knows the bot).
 */
function AddFileButton({ fetchLink }: { fetchLink: () => Promise<string> }) {
  const { adapter } = useTelegram();
  const open = useMutation({
    mutationFn: fetchLink,
    onSuccess: (url) => {
      if (isAllowedTelegramUrl(url)) adapter?.openTelegramLink(url);
    },
  });
  return (
    <>
      <Button
        variant="secondary"
        icon={<Paperclip size={18} />}
        busy={open.isPending}
        onClick={() => open.mutate()}
      >
        {ru.edit.addFile}
      </Button>
      {open.isError ? (
        <p role="alert" className={styles.note}>
          {ru.edit.errors.fileLink}
        </p>
      ) : null}
    </>
  );
}

/** Buttons under a card for editors: "Редактировать" opens the edit page, the file goes via the bot. */
export function EditActions({
  allowed,
  fetchFileLink,
}: {
  allowed: boolean;
  fetchFileLink: () => Promise<string>;
}) {
  const navigate = useNavigate();
  if (!allowed) return null;
  return (
    <div className={styles.buttons}>
      <Button variant="secondary" icon={<Pencil size={18} />} onClick={() => void navigate('edit')}>
        {ru.edit.open}
      </Button>
      <AddFileButton fetchLink={fetchFileLink} />
    </div>
  );
}

// Весь текст лендинга — здесь. Строки с пометкой TODO(owner) взяты из макета
// и НЕ подтверждены каноническими документами (docs/product/module-0-domain-rules.md §15
// утверждает 599 ₽ / 30 дней за группу). Решение владельца 2026-10-10: оставить как в макете,
// заменить перед публикацией.

// Бот. Username можно переопределить переменной VITE_BOT_USERNAME при сборке (Render → Environment).
export const BOT_USERNAME = (import.meta.env.VITE_BOT_USERNAME as string | undefined)?.replace(/^@/, "") || "studgroup_rf_bot";

/** Глубокая ссылка: открывает бота и сразу выполняет команду. Темы описаны в apps/backend/src/studgroup/bot_info.py. */
export type BotTopic = "tariffs" | "howto" | "connect" | "faq" | "support";
export const botLink = (topic: BotTopic) => `https://t.me/${BOT_USERNAME}?start=${topic}`;

export const CONNECT_URL = botLink("connect");

export const nav = [
  { label: "Возможности", href: "#features" },
  { label: "Как это работает", href: "#how" },
  { label: "Тарифы", href: "#pricing" },
  { label: "Отзывы", href: "#reviews" },
  { label: "FAQ", href: "#faq" },
];

// TODO(owner): в макете в hero «300+», в блоке вузов «200+» — приведено к одному значению; цифра не подтверждена.
export const GROUPS_COUNT = "200+";
// TODO(owner): цена из макета; в docs утверждено 599 ₽ за 30 дней на группу.
export const GROUP_PRICE = "499";

export const features = [
  { icon: "feat-docs", title: "Больше не теряйте задания", text: "Все ДЗ и дедлайны из сообщений группы в одном месте" },
  { icon: "feat-schedule", title: "Актуальное расписание", text: "Мгновенные изменения и отмены пар" },
  { icon: "feat-announce", title: "Важные объявления", text: "Контрольные, зачёты и всё важное" },
  { icon: "feat-files", title: "Материалы группы", text: "Презентации, методички и файлы всегда под рукой" },
] as const;

export const appPoints = [
  "Доступ с любого устройства",
  "Уведомления о дедлайнах",
  "Персональное расписание",
  "Ссылки на исходные сообщения",
  "Работает в вашей учебной группе",
];

export const plans = [
  {
    icon: "plan-user",
    name: "Пользовательский",
    note: "Навсегда",
    price: "0 ₽",
    period: "",
    caption: "Базовый доступ",
    items: ["Просмотр заданий группы", "Актуальное расписание", "Ограниченный архив"],
    cta: "Попробовать бесплатно",
    href: botLink("faq"),
    featured: false,
  },
  {
    icon: "plan-group",
    name: "Базовый групповой",
    note: "Для одной группы",
    price: `${GROUP_PRICE} ₽`,
    period: "/ месяц",
    caption: "Вся учебная группа",
    items: ["Полный функционал", "Неограниченный архив", "Уведомления для всех участников", "Техническая поддержка"],
    cta: "Подключить группу",
    href: botLink("connect"),
    featured: true,
  },
  {
    icon: "plan-crown",
    // TODO(owner): в макете «Infiniti групповой» — вероятно, опечатка.
    name: "Infinity групповой",
    note: "Для больших групп и вузов",
    price: "от 999 ₽",
    period: "/ месяц",
    caption: "Расширенные возможности",
    items: ["Приоритетная поддержка", "Дополнительные интеграции", "Индивидуальные настройки"],
    cta: "Обсудить",
    href: botLink("support"),
    featured: false,
  },
] as const;

export const steps = [
  { icon: "step-telegram", title: "Подключите группу", text: "Через нашего бота в Telegram" },
  { icon: "step-card", title: `Оплатите ${GROUP_PRICE} ₽/месяц`, text: "Удобная оплата через СБП" },
  { icon: "step-robot", title: "Мы анализируем сообщения", text: "Извлекаем задания, расписание, материалы" },
  { icon: "step-chart", title: "Получаете результаты", text: "Весь учебный процесс в удобном интерфейсе" },
  { icon: "step-cap", title: "Учитесь эффективнее", text: "Ничего не пропускайте и экономьте время" },
] as const;

// TODO(owner): вузы и цифры — заглушки из макета; нужны подтверждение и права на логотипы.
export const universities = [
  { logo: "logo-ranhigs", name: "РАНХиГС", city: "Москва" },
  { logo: "logo-msu", name: "МГУ", city: "Москва" },
  { logo: "logo-hse", name: "ВШЭ", city: "Москва" },
  { logo: "logo-dots", name: "и другие", city: "вузы" },
] as const;

// TODO(owner): отзывы — заглушки из макета (вымышленные люди); заменить реальными.
export const reviews = [
  { avatar: "avatar-1", text: "Больше не теряю задания, все дедлайны в одном месте. Очень удобно!", name: "Анна", meta: "1 курс, РАНХиГС" },
  { avatar: "avatar-2", text: "Сразу вижу изменения в расписании, не нужно постоянно читать чат.", name: "Никита", meta: "2 курс, МГУ" },
  { avatar: "avatar-3", text: "Очень выручает, особенно перед контрольными. Всё чётко и понятно.", name: "Екатерина", meta: "1 курс, ВШЭ" },
];

// TODO(owner): в макете только вопросы; ответы — минимальные, взяты из текстов макета, часть требует уточнения.
export const faq = [
  { q: "Как подключить группу?", a: "Подключите группу через нашего бота в Telegram — это занимает несколько минут." },
  { q: "Какие данные анализируются?", a: "StudGroup анализирует сообщения вашей учебной группы, чтобы собрать задания, дедлайны, изменения в расписании и важные объявления." },
  { q: "Можно ли попробовать бесплатно?", a: "Условия пробного доступа уточняются." },
  { q: "Безопасны ли наши данные?", a: "Подробности о хранении и защите данных появятся здесь перед запуском." },
  { q: "Как происходит оплата?", a: `Оплата через СБП, ${GROUP_PRICE} ₽ в месяц за группу.` },
  { q: "Подходит ли для любого вуза?", a: "StudGroup работает в Telegram-группе, поэтому не привязан к конкретному вузу." },
];

export const footerLinks = [
  { label: "Возможности", href: "#features" },
  { label: "Тарифы", href: botLink("tariffs") },
  { label: "Инструкция", href: botLink("howto") },
  { label: "Подключение", href: botLink("connect") },
  { label: "FAQ", href: botLink("faq") },
  { label: "Поддержка", href: botLink("support") },
];

export const faqAllHref = botLink("faq");

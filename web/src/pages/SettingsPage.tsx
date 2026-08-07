import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import {
  useAuditLog,
  useClassSettings,
  useReminderSettings,
  useUpdateClassSettings,
  useUpdateReminderSettings,
} from "../api/hooks";
import { LoadingView, QueryError } from "../components/StateViews";
import type {
  ChatProfile,
  HomeworkEditPolicy,
  ReminderSettings,
} from "../api/types";

const ACTION_LABELS: Record<string, string> = {
  create: "добавил(а)",
  update: "изменил(а)",
  delete: "удалил(а)",
  complete: "отметил(а) выполненным",
  restore: "вернул(а) в список",
};

const ENTITY_LABELS: Record<string, string> = {
  homework: "📝 ДЗ",
  extra: "🎯 Доп. занятие",
  schedule: "📅 Расписание",
  day_override: "🗓 Тип дня",
  lesson_override: "🗓 Изменение урока",
  settings: "⚙️ Настройки",
};

const POLICY_OPTIONS: { value: HomeworkEditPolicy; label: string; hint: string }[] = [
  {
    value: "collaborative",
    label: "Все участники",
    hint: "любой может изменить и удалить любое задание",
  },
  {
    value: "creator_or_admin",
    label: "Автор или админ",
    hint: "изменить может тот, кто добавил задание, или админ чата",
  },
  {
    value: "admin_only",
    label: "Только админы",
    hint: "изменять задания могут только админы чата",
  },
];

/** Class name, timezone and who may change homework. Editing is admin-only in a
 * group — the server decides that (``can_edit``) and re-checks it on save. */
function ClassSection({ chatId }: { chatId: number }) {
  const { data, isPending, isError, error, refetch } = useClassSettings(chatId);
  const update = useUpdateClassSettings(chatId);
  const [title, setTitle] = useState<string | null>(null);

  if (isPending) return <LoadingView label="Загружаем настройки класса…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  const titleValue = title ?? data.title ?? "";
  const titleChanged = title !== null && title !== (data.title ?? "");
  const isDiary = data.profile === "personal";
  const currentProfile = data.profile_options.find((o) => o.name === data.profile);

  return (
    <section className="settings-section">
      <h2 className="section__title">🏫 Класс</h2>

      <label className="field">
        <span className="field__label">Как используешь этот чат</span>
        <select
          className="homework-form__input"
          value={data.profile}
          disabled={!data.can_edit}
          onChange={(e) =>
            update.mutate({ profile: e.target.value as ChatProfile })
          }
        >
          {data.profile_options.map((o) => (
            <option key={o.name} value={o.name}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <p className="field__hint">{currentProfile?.description}</p>
      <p className="field__hint">
        Режим меняет только то, какие разделы видно. Ничего не удаляется —
        переключишь обратно, и всё снова на месте.
      </p>

      <label className="field">
        <span className="field__label">
          {isDiary ? "Название дневника" : "Название класса"}
        </span>
        <input
          className="homework-form__input"
          placeholder={isDiary ? "Например, мой дневник" : "Например, 9-А"}
          value={titleValue}
          maxLength={100}
          disabled={!data.can_edit}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      {titleChanged && (
        <div className="homework-form__row">
          <button
            type="button"
            className="button"
            disabled={update.isPending}
            onClick={() =>
              update.mutate({ title: titleValue }, { onSuccess: () => setTitle(null) })
            }
          >
            Сохранить название
          </button>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => setTitle(null)}
          >
            Отмена
          </button>
        </div>
      )}

      <label className="field">
        <span className="field__label">Часовой пояс</span>
        <select
          className="homework-form__input"
          value={data.timezone}
          disabled={!data.can_edit}
          onChange={(e) => update.mutate({ timezone: e.target.value })}
        >
          {/* A zone typed in from the bot may be outside the short picker —
              keep it selectable so opening this screen never silently changes it. */}
          {!data.timezone_options.some((o) => o.name === data.timezone) && (
            <option value={data.timezone}>{data.timezone}</option>
          )}
          {data.timezone_options.map((o) => (
            <option key={o.name} value={o.name}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <p className="field__hint">
        {data.timezone_label} · сейчас {data.local_time}
      </p>

      {data.features.homework_policy && (
        <>
          <label className="field">
            <span className="field__label">Кто может менять задания</span>
            <select
              className="homework-form__input"
              value={data.hw_edit_policy}
              disabled={!data.can_edit}
              onChange={(e) =>
                update.mutate({ hw_edit_policy: e.target.value as HomeworkEditPolicy })
              }
            >
              {POLICY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <p className="field__hint">
            {POLICY_OPTIONS.find((o) => o.value === data.hw_edit_policy)?.hint}
          </p>
          <p className="field__hint">
            Добавлять задания может любой участник при любом варианте.
          </p>

          <label className="reminder-row__toggle">
            <input
              type="checkbox"
              checked={data.per_student_homework}
              disabled={!data.can_edit}
              onChange={(e) =>
                update.mutate({ per_student_homework: e.target.checked })
              }
            />
            Каждый отмечает выполненное за себя
          </label>
          <p className="field__hint">
            Тогда галочка «сделал» у каждого своя, а ты видишь, сколько человек
            сделали. Напоминания в чат при этом не меняются — их видят все, поэтому
            чья-то одна галочка не должна их отключать.
          </p>
        </>
      )}

      {update.isError && (
        <p className="notice">
          {update.error instanceof ApiError
            ? update.error.message
            : "Не получилось сохранить настройки."}
        </p>
      )}
      {!data.can_edit && (
        <p className="muted">Менять настройки класса могут только администраторы чата.</p>
      )}
    </section>
  );
}

function ReminderRow({
  label,
  timeLabel,
  enabled,
  time,
  canEdit,
  onToggle,
  onTimeChange,
}: {
  label: string;
  timeLabel?: string;
  enabled: boolean;
  time?: string;
  canEdit: boolean;
  onToggle: (v: boolean) => void;
  onTimeChange?: (v: string) => void;
}) {
  return (
    <div className="reminder-row">
      <label className="reminder-row__toggle">
        <input
          type="checkbox"
          checked={enabled}
          disabled={!canEdit}
          onChange={(e) => onToggle(e.target.checked)}
        />
        {label}
      </label>
      {timeLabel && time !== undefined && (
        <input
          className="homework-form__input reminder-row__time"
          type="time"
          value={time}
          disabled={!canEdit}
          onChange={(e) => onTimeChange?.(e.target.value)}
        />
      )}
    </div>
  );
}

/** ``hasSchedule`` comes from the chat's profile: without a school timetable
 * "pack your bag" and "tomorrow's schedule changed" have nothing to talk about,
 * so those rows are not offered at all. */
function RemindersSection({
  chatId,
  hasSchedule,
  hasPayments,
}: {
  chatId: number;
  hasSchedule: boolean;
  hasPayments: boolean;
}) {
  const { data, isPending, isError, error, refetch } = useReminderSettings(chatId);
  const update = useUpdateReminderSettings(chatId);
  const [draft, setDraft] = useState<ReminderSettings | null>(null);

  if (isPending) return <LoadingView label="Загружаем настройки…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  const settings = draft ?? data;

  function patch(partial: Partial<ReminderSettings>) {
    setDraft({ ...settings, ...partial });
  }

  function save() {
    if (!draft) return;
    update.mutate(
      {
        hw_reminder_enabled: draft.hw_reminder_enabled,
        hw_reminder_time: draft.hw_reminder_time,
        schedule_reminder_enabled: draft.schedule_reminder_enabled,
        schedule_reminder_time: draft.schedule_reminder_time,
        hw_duetoday_enabled: draft.hw_duetoday_enabled,
        hw_duetoday_time: draft.hw_duetoday_time,
        changes_reminder_enabled: draft.changes_reminder_enabled,
        extra_reminder_enabled: draft.extra_reminder_enabled,
        payment_reminder_enabled: draft.payment_reminder_enabled,
        payment_reminder_time: draft.payment_reminder_time,
        ...(draft.quiet_start && draft.quiet_end
          ? { quiet_start: draft.quiet_start, quiet_end: draft.quiet_end }
          : { clear_quiet_hours: true }),
      },
      { onSuccess: () => setDraft(null) },
    );
  }

  return (
    <section className="settings-section">
      <h2 className="section__title">⏰ Напоминания</h2>
      <ReminderRow
        label="ДЗ на завтра"
        timeLabel="время"
        enabled={settings.hw_reminder_enabled}
        time={settings.hw_reminder_time}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ hw_reminder_enabled: v })}
        onTimeChange={(v) => patch({ hw_reminder_time: v })}
      />
      {hasSchedule && (
        <ReminderRow
          label="Портфель на завтра"
          timeLabel="время"
          enabled={settings.schedule_reminder_enabled}
          time={settings.schedule_reminder_time}
          canEdit={settings.can_edit}
          onToggle={(v) => patch({ schedule_reminder_enabled: v })}
          onTimeChange={(v) => patch({ schedule_reminder_time: v })}
        />
      )}
      <ReminderRow
        label="ДЗ в день сдачи"
        timeLabel="время"
        enabled={settings.hw_duetoday_enabled}
        time={settings.hw_duetoday_time}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ hw_duetoday_enabled: v })}
        onTimeChange={(v) => patch({ hw_duetoday_time: v })}
      />
      {hasSchedule && (
        <ReminderRow
          label="Изменения расписания на завтра"
          enabled={settings.changes_reminder_enabled}
          canEdit={settings.can_edit}
          onToggle={(v) => patch({ changes_reminder_enabled: v })}
        />
      )}
      <ReminderRow
        label="Доп. занятия"
        enabled={settings.extra_reminder_enabled}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ extra_reminder_enabled: v })}
      />

      {hasPayments && (
        <ReminderRow
          label="Напомнить об оплате"
          timeLabel="время"
          enabled={settings.payment_reminder_enabled}
          time={settings.payment_reminder_time}
          canEdit={settings.can_edit}
          onToggle={(v) => patch({ payment_reminder_enabled: v })}
          onTimeChange={(v) => patch({ payment_reminder_time: v })}
        />
      )}

      <div className="reminder-row">
        <span>Тихие часы</span>
        <input
          className="homework-form__input reminder-row__time"
          type="time"
          value={settings.quiet_start ?? ""}
          disabled={!settings.can_edit}
          onChange={(e) => patch({ quiet_start: e.target.value })}
        />
        <span>—</span>
        <input
          className="homework-form__input reminder-row__time"
          type="time"
          value={settings.quiet_end ?? ""}
          disabled={!settings.can_edit}
          onChange={(e) => patch({ quiet_end: e.target.value })}
        />
      </div>

      {settings.can_edit && draft && (
        <div className="homework-form__row">
          <button type="button" className="button" onClick={save} disabled={update.isPending}>
            Сохранить
          </button>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => setDraft(null)}
          >
            Отмена
          </button>
        </div>
      )}
      {!settings.can_edit && (
        <p className="muted">Изменять напоминания могут только администраторы чата.</p>
      )}
    </section>
  );
}

function HistorySection({ chatId, canView }: { chatId: number; canView: boolean }) {
  const [page, setPage] = useState(1);
  const [entityType, setEntityType] = useState<string | undefined>(undefined);
  const { data, isPending, isError, error, refetch } = useAuditLog(chatId, page, entityType, canView);

  if (!canView) {
    return (
      <section className="settings-section">
        <h2 className="section__title">📜 История</h2>
        <p className="muted">История изменений доступна только администраторам чата.</p>
      </section>
    );
  }

  return (
    <section className="settings-section">
      <h2 className="section__title">📜 История</h2>
      <select
        className="homework-form__input"
        value={entityType ?? ""}
        onChange={(e) => {
          setEntityType(e.target.value || undefined);
          setPage(1);
        }}
      >
        <option value="">Все типы</option>
        {Object.entries(ENTITY_LABELS).map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>

      {isPending && <LoadingView label="Загружаем историю…" />}
      {isError && <QueryError error={error} onRetry={() => refetch()} />}
      {!isPending && !isError && data.items.length === 0 && (
        <p className="muted">Записей пока нет.</p>
      )}
      {!isPending && !isError && data.items.length > 0 && (
        <ul className="audit-list">
          {data.items.map((entry) => (
            <li key={entry.id} className="audit-row">
              <span className="audit-row__meta">
                {entry.actor_name} {ACTION_LABELS[entry.action] ?? entry.action}{" "}
                {ENTITY_LABELS[entry.entity_type] ?? entry.entity_type}
              </span>
              {entry.summary && <span className="audit-row__summary">{entry.summary}</span>}
              <span className="audit-row__time">{entry.created_at}</span>
            </li>
          ))}
        </ul>
      )}
      {!isPending && !isError && data.total > data.page_size && (
        <div className="homework-form__row">
          <button
            type="button"
            className="button button--secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Назад
          </button>
          <button
            type="button"
            className="button button--secondary"
            disabled={page * data.page_size >= data.total}
            onClick={() => setPage((p) => p + 1)}
          >
            Дальше
          </button>
        </div>
      )}
    </section>
  );
}

function ExportSection({ chatId, canExport }: { chatId: number; canExport: boolean }) {
  if (!canExport) {
    return (
      <section className="settings-section">
        <h2 className="section__title">💾 Данные и резервная копия</h2>
        <p className="muted">Экспорт доступен только администраторам чата.</p>
      </section>
    );
  }

  return (
    <section className="settings-section">
      <h2 className="section__title">💾 Данные и резервная копия</h2>
      <div className="export-links">
        <a className="button button--secondary" href={api.exportUrl(chatId, "backup.json")}>
          📦 JSON-резервная копия
        </a>
        <a className="button button--secondary" href={api.exportUrl(chatId, "schedule.csv")}>
          📊 Расписание в CSV
        </a>
        <a className="button button--secondary" href={api.exportUrl(chatId, "calendar.ics")}>
          📅 Календарь (ICS)
        </a>
        <a className="button button--secondary" href={api.exportUrl(chatId, "audit.json")}>
          📜 Экспорт истории
        </a>
      </div>
    </section>
  );
}

export function SettingsPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const { data: reminderSettings } = useReminderSettings(id);
  const { data: classSettings } = useClassSettings(id);
  const isAdmin = reminderSettings?.can_edit ?? false;
  // Until the profile is known, assume the widest set so no row flickers away.
  const hasSchedule = classSettings?.features.school_schedule ?? true;
  const hasPayments = classSettings?.features.payments ?? false;

  return (
    <main className="page">
      <h1 className="page__title">Настройки</h1>
      <ClassSection chatId={id} />
      {/* Members are a group concept: a personal diary has nobody to invite.
          Kept as a link rather than a sixth tab — the bar stays readable. */}
      {classSettings && classSettings.profile !== "personal" && (
        <Link className="card card--link" to={`/classes/${id}/members`}>
          <span className="card__title">👥 Участники и доступ</span>
          <span className="card__meta">
            кто видит класс, кто вносит данные, приглашения
          </span>
        </Link>
      )}
      <RemindersSection chatId={id} hasSchedule={hasSchedule} hasPayments={hasPayments} />
      <HistorySection chatId={id} canView={isAdmin} />
      <ExportSection chatId={id} canExport={isAdmin} />
    </main>
  );
}

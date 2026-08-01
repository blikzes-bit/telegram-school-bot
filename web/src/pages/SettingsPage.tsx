import { useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api/client";
import { useAuditLog, useReminderSettings, useUpdateReminderSettings } from "../api/hooks";
import { LoadingView, QueryError } from "../components/StateViews";
import type { ReminderSettings } from "../api/types";

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

function RemindersSection({ chatId }: { chatId: number }) {
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
      <ReminderRow
        label="Портфель на завтра"
        timeLabel="время"
        enabled={settings.schedule_reminder_enabled}
        time={settings.schedule_reminder_time}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ schedule_reminder_enabled: v })}
        onTimeChange={(v) => patch({ schedule_reminder_time: v })}
      />
      <ReminderRow
        label="ДЗ в день сдачи"
        timeLabel="время"
        enabled={settings.hw_duetoday_enabled}
        time={settings.hw_duetoday_time}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ hw_duetoday_enabled: v })}
        onTimeChange={(v) => patch({ hw_duetoday_time: v })}
      />
      <ReminderRow
        label="Изменения расписания на завтра"
        enabled={settings.changes_reminder_enabled}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ changes_reminder_enabled: v })}
      />
      <ReminderRow
        label="Доп. занятия"
        enabled={settings.extra_reminder_enabled}
        canEdit={settings.can_edit}
        onToggle={(v) => patch({ extra_reminder_enabled: v })}
      />

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
  const isAdmin = reminderSettings?.can_edit ?? false;

  return (
    <main className="page">
      <h1 className="page__title">Настройки</h1>
      <RemindersSection chatId={id} />
      <HistorySection chatId={id} canView={isAdmin} />
      <ExportSection chatId={id} canExport={isAdmin} />
    </main>
  );
}

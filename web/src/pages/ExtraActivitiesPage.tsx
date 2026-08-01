import { useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useCreateExtra, useDeleteExtra, useExtra, useUpdateExtra } from "../api/hooks";
import { ExtraRow } from "../components/Rows";
import { EmptyView, LoadingView, QueryError } from "../components/StateViews";
import type { ExtraActivity } from "../api/types";
import { addDaysISO, todayISO } from "../utils/date";

const WEEKDAY_OPTIONS = [
  { value: 0, label: "Понедельник" },
  { value: 1, label: "Вторник" },
  { value: 2, label: "Среда" },
  { value: 3, label: "Четверг" },
  { value: 4, label: "Пятница" },
  { value: 5, label: "Суббота" },
  { value: 6, label: "Воскресенье" },
];

/** Adding requires admin rights in a group chat (unrestricted in a private
 * chat) — mirrors the bot's rule exactly; the server re-checks this on every
 * request regardless of what the UI shows. */
function AddForm({
  onSubmit,
  submitting,
  error,
  onCancel,
}: {
  onSubmit: (input: {
    title: string;
    kind: "weekly" | "once";
    day_of_week?: number;
    activity_date?: string;
    start_time: string;
    end_time?: string;
    location?: string;
  }) => void;
  submitting: boolean;
  error: unknown;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<"weekly" | "once">("weekly");
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [activityDate, setActivityDate] = useState(todayISO());
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [location, setLocation] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({
      title,
      kind,
      day_of_week: kind === "weekly" ? dayOfWeek : undefined,
      activity_date: kind === "once" ? activityDate : undefined,
      start_time: startTime,
      end_time: endTime || undefined,
      location: location || undefined,
    });
  }

  return (
    <form className="homework-form" onSubmit={submit}>
      <input
        className="homework-form__input"
        placeholder="Название (например, Английский)"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
      />
      <select
        className="homework-form__input"
        value={kind}
        onChange={(e) => setKind(e.target.value as "weekly" | "once")}
      >
        <option value="weekly">Еженедельно</option>
        <option value="once">Разово</option>
      </select>
      {kind === "weekly" ? (
        <select
          className="homework-form__input"
          value={dayOfWeek}
          onChange={(e) => setDayOfWeek(Number(e.target.value))}
        >
          {WEEKDAY_OPTIONS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="homework-form__input"
          type="date"
          value={activityDate}
          onChange={(e) => setActivityDate(e.target.value)}
          required
        />
      )}
      <input
        className="homework-form__input"
        type="time"
        value={startTime}
        onChange={(e) => setStartTime(e.target.value)}
        required
      />
      <input
        className="homework-form__input"
        type="time"
        value={endTime}
        onChange={(e) => setEndTime(e.target.value)}
        placeholder="Окончание (необязательно)"
      />
      <input
        className="homework-form__input"
        placeholder="Место (необязательно)"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
      />
      {error ? (
        <p className="notice">
          {error instanceof ApiError ? error.message : "Не удалось добавить занятие."}
        </p>
      ) : null}
      <div className="homework-form__row">
        <button type="submit" className="button" disabled={submitting}>
          Добавить
        </button>
        <button type="button" className="button button--secondary" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

function EditForm({
  activity,
  onSubmit,
  submitting,
  onCancel,
}: {
  activity: ExtraActivity;
  onSubmit: (input: { title: string; start_time: string; end_time?: string; location?: string }) => void;
  submitting: boolean;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(activity.title);
  const [startTime, setStartTime] = useState(activity.start_time);
  const [endTime, setEndTime] = useState(activity.end_time ?? "");
  const [location, setLocation] = useState(activity.location ?? "");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({ title, start_time: startTime, end_time: endTime || undefined, location: location || undefined });
  }

  return (
    <form className="homework-form" onSubmit={submit}>
      <input
        className="homework-form__input"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
      />
      <input
        className="homework-form__input"
        type="time"
        value={startTime}
        onChange={(e) => setStartTime(e.target.value)}
        required
      />
      <input
        className="homework-form__input"
        type="time"
        value={endTime}
        onChange={(e) => setEndTime(e.target.value)}
      />
      <input
        className="homework-form__input"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
      />
      <div className="homework-form__row">
        <button type="submit" className="button" disabled={submitting}>
          Сохранить
        </button>
        <button type="button" className="button button--secondary" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

export function ExtraActivitiesPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const from = todayISO();
  const to = addDaysISO(from, 27);
  const { data, isPending, isError, error, refetch } = useExtra(id, from, to);
  const createExtra = useCreateExtra(id);
  const updateExtra = useUpdateExtra(id);
  const deleteExtra = useDeleteExtra(id);
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

  if (isPending) return <LoadingView label="Загружаем занятия…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  return (
    <main className="page">
      <div className="page__head">
        <h1 className="page__title">Доп. занятия</h1>
        <button type="button" className="button" onClick={() => setShowAddForm((v) => !v)}>
          {showAddForm ? "Отмена" : "+ Добавить"}
        </button>
      </div>

      {showAddForm && (
        <AddForm
          submitting={createExtra.isPending}
          error={createExtra.error}
          onCancel={() => setShowAddForm(false)}
          onSubmit={(input) =>
            createExtra.mutate(input, { onSuccess: () => setShowAddForm(false) })
          }
        />
      )}

      {data.length === 0 ? (
        <EmptyView message="Дополнительных занятий пока нет." />
      ) : (
        <ul className="extra-list" aria-label="Список доп. занятий">
          {data.map((a) =>
            editingId === a.id ? (
              <li key={a.id}>
                <EditForm
                  activity={a}
                  submitting={updateExtra.isPending}
                  onCancel={() => setEditingId(null)}
                  onSubmit={(input) =>
                    updateExtra.mutate(
                      { activityId: a.id, input },
                      { onSuccess: () => setEditingId(null) },
                    )
                  }
                />
              </li>
            ) : (
              <ExtraRow
                key={a.id}
                activity={a}
                onEdit={() => setEditingId(a.id)}
                onDelete={() => {
                  if (window.confirm(`Удалить «${a.title}»?`)) {
                    deleteExtra.mutate(a.id);
                  }
                }}
              />
            ),
          )}
        </ul>
      )}
    </main>
  );
}

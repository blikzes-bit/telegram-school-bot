import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useCreateHomework,
  useDeleteHomework,
  useHomework,
  useSetHomeworkCompleted,
  useUpdateHomework,
} from "../api/hooks";
import { ApiError } from "../api/client";
import { HomeworkRow } from "../components/Rows";
import { EmptyView, LoadingView, QueryError } from "../components/StateViews";
import type { Homework, HomeworkStatus } from "../api/types";

const FILTERS: { value: HomeworkStatus; label: string }[] = [
  { value: "active", label: "Актуальные" },
  { value: "overdue", label: "Просроченные" },
  { value: "completed", label: "Выполненные" },
];

const EMPTY_MESSAGE: Record<HomeworkStatus, string> = {
  active: "Пока ничего не задано. Нажми «＋ Добавить», чтобы записать задание.",
  overdue: "Просроченных заданий нет — всё сдано в срок.",
  completed: "Выполненных заданий пока нет.",
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** The add/edit form. One component for both so the fields, validation and
 * wording can never drift apart between "новое" and "изменить". */
function HomeworkForm({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial?: Homework;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (values: { subject_name: string; due_date: string; description: string }) => void;
  onCancel: () => void;
}) {
  const [subject, setSubject] = useState(initial?.subject_name ?? "");
  const [dueDate, setDueDate] = useState(initial?.due_date ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");

  return (
    <form
      className="homework-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ subject_name: subject, due_date: dueDate, description });
      }}
    >
      <label className="field">
        <span className="field__label">Предмет</span>
        <input
          className="homework-form__input"
          placeholder="Например, математика"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          required
        />
      </label>
      <label className="field">
        <span className="field__label">Когда сдавать</span>
        <input
          className="homework-form__input"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          required
        />
      </label>
      <label className="field">
        <span className="field__label">Что нужно сделать</span>
        <textarea
          className="homework-form__input"
          placeholder="Например, упражнение 12 на странице 40"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          required
        />
      </label>
      {Boolean(error) && (
        <p className="notice">{errorText(error, "Не получилось сохранить задание.")}</p>
      )}
      <div className="homework-form__row">
        <button type="submit" className="button" disabled={pending}>
          {pending ? "Сохраняем…" : submitLabel}
        </button>
        <button type="button" className="button button--secondary" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

/** Homework list with active/overdue/completed filters, adding, editing,
 * deleting and toggling completion. Mutations write straight to the database
 * the bot reads from, so a change made here shows up in the bot's /today and
 * /homework next time either surface is opened — there is no separate sync
 * step. Whether *this* user may change an entry is decided by the server
 * (``can_edit`` on each item); the buttons below only follow that answer. */
export function HomeworkPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const [status, setStatus] = useState<HomeworkStatus>("active");
  const { data, isPending, isError, error, refetch } = useHomework(id, status);
  const createHomework = useCreateHomework(id);
  const updateHomework = useUpdateHomework(id);
  const deleteHomework = useDeleteHomework(id);
  const setCompleted = useSetHomeworkCompleted(id);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Homework | null>(null);

  function closeForms() {
    setShowForm(false);
    setEditing(null);
    createHomework.reset();
    updateHomework.reset();
  }

  return (
    <main className="page">
      <div className="page__head">
        <h1 className="page__title">Домашние задания</h1>
        <button
          type="button"
          className="button"
          onClick={() => {
            setEditing(null);
            setShowForm((v) => !v);
          }}
        >
          {showForm ? "Отмена" : "＋ Добавить"}
        </button>
      </div>

      {showForm && (
        <HomeworkForm
          submitLabel="Добавить"
          pending={createHomework.isPending}
          error={createHomework.isError ? createHomework.error : null}
          onCancel={closeForms}
          onSubmit={(values) =>
            createHomework.mutate(values, { onSuccess: closeForms })
          }
        />
      )}

      {editing && (
        <HomeworkForm
          key={editing.id}
          initial={editing}
          submitLabel="Сохранить"
          pending={updateHomework.isPending}
          error={updateHomework.isError ? updateHomework.error : null}
          onCancel={closeForms}
          onSubmit={(values) =>
            updateHomework.mutate(
              { homeworkId: editing.id, input: values },
              { onSuccess: closeForms },
            )
          }
        />
      )}

      <div className="segmented" role="tablist" aria-label="Фильтр домашних заданий">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            role="tab"
            aria-selected={status === f.value}
            className={`segmented__item${status === f.value ? " segmented__item--active" : ""}`}
            onClick={() => setStatus(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {deleteHomework.isError && (
        <p className="notice">
          {errorText(deleteHomework.error, "Не получилось удалить задание.")}
        </p>
      )}
      {setCompleted.isError && (
        <p className="notice">
          {errorText(setCompleted.error, "Не получилось изменить отметку.")}
        </p>
      )}

      {isPending && <LoadingView label="Загружаем задания…" />}
      {isError && <QueryError error={error} onRetry={() => refetch()} />}
      {!isPending && !isError && data.length === 0 && (
        <EmptyView message={EMPTY_MESSAGE[status]} />
      )}
      {!isPending && !isError && data.length > 0 && (
        <ul className="homework-list">
          {data.map((hw) => (
            <HomeworkRow
              key={hw.id}
              homework={hw}
              toggling={setCompleted.isPending && setCompleted.variables?.homeworkId === hw.id}
              onToggle={(isCompleted) =>
                setCompleted.mutate({ homeworkId: hw.id, isCompleted })
              }
              onEdit={() => {
                setShowForm(false);
                updateHomework.reset();
                setEditing(hw);
              }}
              onDelete={() => deleteHomework.mutate(hw.id)}
              deleting={deleteHomework.isPending && deleteHomework.variables === hw.id}
            />
          ))}
        </ul>
      )}
    </main>
  );
}

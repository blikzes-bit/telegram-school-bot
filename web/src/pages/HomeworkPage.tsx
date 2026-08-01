import { useState } from "react";
import { useParams } from "react-router-dom";

import { useCreateHomework, useHomework, useSetHomeworkCompleted } from "../api/hooks";
import { ApiError } from "../api/client";
import { HomeworkRow } from "../components/Rows";
import { EmptyView, LoadingView, QueryError } from "../components/StateViews";
import type { HomeworkStatus } from "../api/types";

const FILTERS: { value: HomeworkStatus; label: string }[] = [
  { value: "active", label: "Актуальные" },
  { value: "overdue", label: "Просроченные" },
  { value: "completed", label: "Выполненные" },
];

/** Homework list with active/overdue/completed filters, adding, and toggling
 * completion. Mutations write straight to the database the bot reads from,
 * so a change made here shows up in the bot's /today and /homework next time
 * either surface is opened — there is no separate sync step. */
export function HomeworkPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const [status, setStatus] = useState<HomeworkStatus>("active");
  const { data, isPending, isError, error, refetch } = useHomework(id, status);
  const createHomework = useCreateHomework(id);
  const setCompleted = useSetHomeworkCompleted(id);
  const [showForm, setShowForm] = useState(false);
  const [subject, setSubject] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [description, setDescription] = useState("");

  function submitNewHomework(e: React.FormEvent) {
    e.preventDefault();
    createHomework.mutate(
      { subject_name: subject, due_date: dueDate, description },
      {
        onSuccess: () => {
          setSubject("");
          setDueDate("");
          setDescription("");
          setShowForm(false);
        },
      },
    );
  }

  return (
    <main className="page">
      <div className="page__head">
        <h1 className="page__title">Домашние задания</h1>
        <button type="button" className="button" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Отмена" : "+ Добавить"}
        </button>
      </div>

      {showForm && (
        <form className="homework-form" onSubmit={submitNewHomework}>
          <input
            className="homework-form__input"
            placeholder="Предмет"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
          />
          <input
            className="homework-form__input"
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
            required
          />
          <textarea
            className="homework-form__input"
            placeholder="Что нужно сделать"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
          />
          {createHomework.isError && (
            <p className="notice">
              {createHomework.error instanceof ApiError
                ? createHomework.error.message
                : "Не удалось добавить задание."}
            </p>
          )}
          <button type="submit" className="button" disabled={createHomework.isPending}>
            Добавить
          </button>
        </form>
      )}

      <div
        className="segmented"
        role="tablist"
        aria-label="Фильтр домашних заданий"
      >
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

      {isPending && <LoadingView label="Загружаем задания…" />}
      {isError && <QueryError error={error} onRetry={() => refetch()} />}
      {!isPending && !isError && data.length === 0 && (
        <EmptyView message="Заданий в этой категории нет." />
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
            />
          ))}
        </ul>
      )}
    </main>
  );
}

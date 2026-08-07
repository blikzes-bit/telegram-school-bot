import { useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  useClearDateOverrides,
  useDateOverrides,
  useSaveLessonSlots,
  useSaveScheduleDay,
  useSchedule,
  useScheduleTemplate,
  useSetDayOverride,
  useSetLessonOverride,
} from "../api/hooks";
import { ExtraRow, LessonRow } from "../components/Rows";
import { LoadingView, QueryError } from "../components/StateViews";
import { addDaysISO, formatDateFull, todayISO, weekdayName } from "../utils/date";
import type { DayType, LessonSlot, ScheduleDayLesson } from "../api/types";

type Mode = "week" | "template" | "date";

const MODES: { value: Mode; label: string }[] = [
  { value: "week", label: "Неделя" },
  { value: "template", label: "Обычное" },
  { value: "date", label: "На дату" },
];

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** The effective schedule for the next seven days — what actually happens. */
function WeekView({ chatId }: { chatId: number }) {
  const from = todayISO();
  const to = addDaysISO(from, 6);
  const { data, isPending, isError, error, refetch } = useSchedule(chatId, from, to);

  if (isPending) return <LoadingView label="Загружаем расписание…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  return (
    <>
      {data.days.map((day) => (
        <section key={day.date} className="day-block" aria-label={day.date}>
          <h2 className="day-block__title">
            {formatDateFull(day.date)}
            {day.week_type !== "all" ? ` · Неделя ${day.week_type}` : ""}
          </h2>
          {day.day_note && <p className="notice">{day.day_note}</p>}
          {day.lessons.length === 0 ? (
            <p className="muted">Уроков нет ({weekdayName(day.weekday)}).</p>
          ) : (
            <ul className="lesson-list">
              {day.lessons.map((l) => (
                <LessonRow key={l.lesson_number} lesson={l} />
              ))}
            </ul>
          )}
          {day.extra.length > 0 && (
            <ul className="extra-list">
              {day.extra.map((a) => (
                <ExtraRow key={a.id} activity={a} />
              ))}
            </ul>
          )}
        </section>
      ))}
    </>
  );
}

/** Bell times: one row per lesson, saved as a whole set. */
function SlotsEditor({
  chatId,
  slots,
  canEdit,
}: {
  chatId: number;
  slots: LessonSlot[];
  canEdit: boolean;
}) {
  const save = useSaveLessonSlots(chatId);
  const [draft, setDraft] = useState<LessonSlot[] | null>(null);
  const rows = draft ?? slots;

  function patch(index: number, field: "start_time" | "end_time", value: string) {
    setDraft(rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  }

  return (
    <section className="settings-section">
      <h2 className="section__title">🔔 Время уроков</h2>
      {rows.map((slot, index) => (
        <div key={slot.lesson_number} className="reminder-row">
          <span className="reminder-row__toggle">Урок {slot.lesson_number}</span>
          <input
            className="homework-form__input reminder-row__time"
            type="time"
            value={slot.start_time}
            disabled={!canEdit}
            onChange={(e) => patch(index, "start_time", e.target.value)}
          />
          <span>—</span>
          <input
            className="homework-form__input reminder-row__time"
            type="time"
            value={slot.end_time}
            disabled={!canEdit}
            onChange={(e) => patch(index, "end_time", e.target.value)}
          />
        </div>
      ))}

      {canEdit && (
        <div className="homework-form__row">
          <button
            type="button"
            className="button button--secondary"
            onClick={() =>
              setDraft([
                ...rows,
                {
                  lesson_number: rows.length + 1,
                  start_time: rows.length ? rows[rows.length - 1].end_time : "08:00",
                  end_time: "",
                },
              ])
            }
          >
            ＋ Урок
          </button>
          {rows.length > 1 && (
            <button
              type="button"
              className="button button--secondary"
              onClick={() => setDraft(rows.slice(0, -1))}
            >
              − Убрать последний
            </button>
          )}
        </div>
      )}

      {save.isError && (
        <p className="notice">
          {errorText(save.error, "Не получилось сохранить время уроков.")}
        </p>
      )}

      {canEdit && draft && (
        <div className="homework-form__row">
          <button
            type="button"
            className="button"
            disabled={save.isPending}
            onClick={() => save.mutate(rows, { onSuccess: () => setDraft(null) })}
          >
            Сохранить время
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
      {canEdit && rows.some((r) => !r.end_time) && (
        <p className="field__hint">Заполни время окончания у каждого урока.</p>
      )}
    </section>
  );
}

/** Subjects for one weekday of the template. */
function DayEditor({
  chatId,
  weekday,
  lessons,
  weekType,
  canEdit,
}: {
  chatId: number;
  weekday: number;
  lessons: ScheduleDayLesson[];
  weekType: string;
  canEdit: boolean;
}) {
  const save = useSaveScheduleDay(chatId);
  const [draft, setDraft] = useState<ScheduleDayLesson[] | null>(null);
  const rows = draft ?? lessons;

  return (
    <section className="settings-section">
      <h2 className="section__title">{weekdayName(weekday)}</h2>
      {rows.length === 0 && (
        <p className="muted">Сначала задай время уроков выше.</p>
      )}
      {rows.map((lesson, index) => (
        <label key={lesson.lesson_number} className="field">
          <span className="field__label">Урок {lesson.lesson_number}</span>
          <input
            className="homework-form__input"
            placeholder="Пусто — урока нет"
            value={lesson.subject_name ?? ""}
            disabled={!canEdit}
            onChange={(e) =>
              setDraft(
                rows.map((row, i) =>
                  i === index ? { ...row, subject_name: e.target.value } : row,
                ),
              )
            }
          />
        </label>
      ))}
      {save.isError && (
        <p className="notice">{errorText(save.error, "Не получилось сохранить день.")}</p>
      )}
      {canEdit && draft && (
        <div className="homework-form__row">
          <button
            type="button"
            className="button"
            disabled={save.isPending}
            onClick={() =>
              save.mutate(
                { weekday, lessons: rows, weekType },
                { onSuccess: () => setDraft(null) },
              )
            }
          >
            Сохранить {weekdayName(weekday).toLowerCase()}
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
    </section>
  );
}

/** The weekly template: bell times, then one editor per weekday. */
function TemplateView({ chatId }: { chatId: number }) {
  const [weekType, setWeekType] = useState<string | undefined>(undefined);
  const { data, isPending, isError, error, refetch } = useScheduleTemplate(chatId, weekType);

  if (isPending) return <LoadingView label="Загружаем расписание…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  return (
    <>
      {data.week_mode && (
        <div className="segmented" role="tablist" aria-label="Неделя A или B">
          {data.week_types.map((wt) => (
            <button
              key={wt}
              type="button"
              role="tab"
              aria-selected={data.week_type === wt}
              className={`segmented__item${data.week_type === wt ? " segmented__item--active" : ""}`}
              onClick={() => setWeekType(wt)}
            >
              Неделя {wt}
            </button>
          ))}
        </div>
      )}

      <SlotsEditor chatId={chatId} slots={data.slots} canEdit={data.can_edit} />

      {/* Saturday and Sunday are included: a school week that runs six days is
          normal in plenty of places, and an empty day costs one line. */}
      {data.days.map((day) => (
        <DayEditor
          key={`${data.week_type}-${day.weekday}`}
          chatId={chatId}
          weekday={day.weekday}
          lessons={day.lessons}
          weekType={data.week_type}
          canEdit={data.can_edit}
        />
      ))}

      {!data.can_edit && (
        <p className="muted">Менять расписание может только владелец класса.</p>
      )}
    </>
  );
}

/** One date: a whole-day setting and per-lesson changes. */
function DateView({ chatId }: { chatId: number }) {
  const [date, setDate] = useState(todayISO());
  const { data, isPending, isError, error, refetch } = useDateOverrides(chatId, date);
  const setDay = useSetDayOverride(chatId, date);
  const clearAll = useClearDateOverrides(chatId, date);
  const setLesson = useSetLessonOverride(chatId, date);
  const [note, setNote] = useState("");
  const [lessonNumber, setLessonNumber] = useState("1");
  const [subject, setSubject] = useState("");
  const [startTime, setStartTime] = useState("");

  return (
    <>
      <label className="field">
        <span className="field__label">Какой день меняем</span>
        <input
          className="homework-form__input"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </label>

      {isPending && <LoadingView label="Загружаем день…" />}
      {isError && <QueryError error={error} onRetry={() => refetch()} />}

      {!isPending && !isError && (
        <>
          <section className="settings-section">
            <h2 className="section__title">Весь день</h2>
            {data.day ? (
              <p>
                Сейчас: <b>{data.day.day_type_label}</b>
                {data.day.note ? ` — ${data.day.note}` : ""}
              </p>
            ) : (
              <p className="muted">Обычный день по расписанию.</p>
            )}

            {data.can_edit && (
              <>
                <label className="field">
                  <span className="field__label">Примечание (не обязательно)</span>
                  <input
                    className="homework-form__input"
                    value={note}
                    maxLength={300}
                    placeholder="Например, День города"
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
                <div className="homework__actions">
                  {data.day_type_options.map((option) => (
                    <button
                      key={option.name}
                      type="button"
                      className="row-action"
                      disabled={setDay.isPending}
                      onClick={() =>
                        setDay.mutate({
                          day_type: option.name as DayType,
                          note: note.trim() || undefined,
                        })
                      }
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                {data.day && (
                  <div className="homework__actions">
                    <button
                      type="button"
                      className="row-action row-action--danger"
                      disabled={setDay.isPending}
                      onClick={() => setDay.mutate({ day_type: null })}
                    >
                      Сделать день обычным
                    </button>
                  </div>
                )}
              </>
            )}
          </section>

          <section className="settings-section">
            <h2 className="section__title">Отдельные уроки</h2>
            {data.lessons.length === 0 ? (
              <p className="muted">Изменений по урокам нет.</p>
            ) : (
              <ul className="card-list">
                {data.lessons.map((lesson) => (
                  <li key={lesson.lesson_number} className="audit-row">
                    <span>
                      Урок {lesson.lesson_number}:{" "}
                      {lesson.action === "cancel"
                        ? "отменён"
                        : `${lesson.subject_name ?? "замена"}${
                            lesson.start_time ? ` в ${lesson.start_time}` : ""
                          }`}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {data.can_edit && (
              <>
                <div className="homework-form__row">
                  <label className="field">
                    <span className="field__label">Урок №</span>
                    <input
                      className="homework-form__input"
                      type="number"
                      min={1}
                      max={10}
                      value={lessonNumber}
                      onChange={(e) => setLessonNumber(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Новое время</span>
                    <input
                      className="homework-form__input"
                      type="time"
                      value={startTime}
                      onChange={(e) => setStartTime(e.target.value)}
                    />
                  </label>
                </div>
                <label className="field">
                  <span className="field__label">Новый предмет</span>
                  <input
                    className="homework-form__input"
                    placeholder="Например, Астрономия"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                  />
                </label>
                <div className="homework-form__row">
                  <button
                    type="button"
                    className="button"
                    disabled={setLesson.isPending || (!subject.trim() && !startTime)}
                    onClick={() =>
                      setLesson.mutate({
                        lessonNumber: Number(lessonNumber) || 1,
                        input: {
                          action: "set",
                          subject_name: subject.trim() || undefined,
                          start_time: startTime || undefined,
                        },
                      })
                    }
                  >
                    Заменить
                  </button>
                  <button
                    type="button"
                    className="button button--secondary"
                    disabled={setLesson.isPending}
                    onClick={() =>
                      setLesson.mutate({
                        lessonNumber: Number(lessonNumber) || 1,
                        input: { action: "cancel" },
                      })
                    }
                  >
                    Отменить урок
                  </button>
                </div>
              </>
            )}
          </section>

          {(setDay.isError || setLesson.isError || clearAll.isError) && (
            <p className="notice">
              {errorText(
                setDay.error ?? setLesson.error ?? clearAll.error,
                "Не получилось сохранить изменение.",
              )}
            </p>
          )}

          {data.can_edit && (data.day || data.lessons.length > 0) && (
            <button
              type="button"
              className="button button--secondary"
              disabled={clearAll.isPending}
              onClick={() => clearAll.mutate(undefined)}
            >
              Убрать все изменения этого дня
            </button>
          )}
        </>
      )}
    </>
  );
}

/** Schedule: the week as it will actually happen, the repeating template, and
 * one-off changes for a single date. Three modes rather than one crowded screen,
 * because "what is my Tuesday" and "cancel Tuesday's second lesson" are
 * different questions. */
export function SchedulePage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const [mode, setMode] = useState<Mode>("week");

  return (
    <main className="page">
      <h1 className="page__title">Расписание</h1>

      <div className="segmented" role="tablist" aria-label="Что показать">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            role="tab"
            aria-selected={mode === m.value}
            className={`segmented__item${mode === m.value ? " segmented__item--active" : ""}`}
            onClick={() => setMode(m.value)}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === "week" && <WeekView chatId={id} />}
      {mode === "template" && <TemplateView chatId={id} />}
      {mode === "date" && <DateView chatId={id} />}
    </main>
  );
}

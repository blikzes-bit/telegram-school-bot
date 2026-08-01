import { useParams } from "react-router-dom";

import { useSchedule } from "../api/hooks";
import { ExtraRow, LessonRow } from "../components/Rows";
import { LoadingView, QueryError } from "../components/StateViews";
import { addDaysISO, formatDateFull, todayISO, weekdayName } from "../utils/date";

/** Read-only week view: effective schedule for the next 7 days. */
export function SchedulePage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const from = todayISO();
  const to = addDaysISO(from, 6);
  const { data, isPending, isError, error, refetch } = useSchedule(id, from, to);

  if (isPending) return <LoadingView label="Загружаем расписание…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  return (
    <main className="page">
      <h1 className="page__title">Расписание на неделю</h1>
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
    </main>
  );
}

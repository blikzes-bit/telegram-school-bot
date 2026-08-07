import { useParams } from "react-router-dom";

import { useClassSettings, useToday } from "../api/hooks";
import { ExtraRow, HomeworkRow, LessonRow } from "../components/Rows";
import { LoadingView, QueryError } from "../components/StateViews";
import { formatDateFull, weekdayName } from "../utils/date";

/** The class dashboard — the web mirror of the bot's /today screen. */
export function TodayPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const { data, isPending, isError, error, refetch } = useToday(id);
  const { data: classSettings } = useClassSettings(id);
  // A tutor chat has no school timetable, so the lessons block is not shown at
  // all there. Assume it exists until the profile is known, to avoid a flicker.
  const hasSchedule = classSettings?.features.school_schedule ?? true;

  if (isPending) return <LoadingView label="Загружаем сегодня…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  const weekLabel = data.week_type === "all" ? "" : `Неделя ${data.week_type}`;

  return (
    <main className="page">
      <header className="page__header">
        <h1 className="page__title">{formatDateFull(data.date)}</h1>
        <p className="page__subtitle">
          {weekdayName(data.weekday)}
          {weekLabel ? ` · ${weekLabel}` : ""}
          {data.timezone ? ` · ${data.timezone}` : ""}
        </p>
        {data.day_note && <p className="notice">{data.day_note}</p>}
      </header>

      {hasSchedule && (
        <section aria-labelledby="today-lessons">
          <h2 id="today-lessons" className="section__title">
            Уроки
          </h2>
          {data.lessons.length === 0 ? (
            <p className="muted">На сегодня уроков нет.</p>
          ) : (
            <ul className="lesson-list">
              {data.lessons.map((l) => (
                <LessonRow key={l.lesson_number} lesson={l} />
              ))}
            </ul>
          )}
        </section>
      )}

      <HomeworkGroup title="На сегодня" items={data.homework_today} />
      <HomeworkGroup title="Просроченное" items={data.overdue} />
      <HomeworkGroup title="Ближайшее" items={data.upcoming} />

      <section aria-labelledby="today-extra">
        <h2 id="today-extra" className="section__title">
          Доп. занятия
        </h2>
        {data.extra.length === 0 ? (
          <p className="muted">Сегодня доп. занятий нет.</p>
        ) : (
          <ul className="extra-list">
            {data.extra.map((a) => (
              <ExtraRow key={a.id} activity={a} />
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function HomeworkGroup({
  title,
  items,
}: {
  title: string;
  items: import("../api/types").Homework[];
}) {
  if (items.length === 0) return null;
  return (
    <section>
      <h2 className="section__title">{title}</h2>
      <ul className="homework-list">
        {items.map((hw) => (
          <HomeworkRow key={hw.id} homework={hw} />
        ))}
      </ul>
    </section>
  );
}

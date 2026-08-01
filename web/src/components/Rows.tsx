import type { ExtraActivity, Homework, Lesson } from "../api/types";
import { formatDate } from "../utils/date";

/** One lesson row, annotated with cancel/replace/added markers. */
export function LessonRow({ lesson }: { lesson: Lesson }) {
  const time = lesson.start_time
    ? `${lesson.start_time}${lesson.end_time ? `–${lesson.end_time}` : ""}`
    : "";
  const badges: string[] = [];
  if (lesson.cancelled) badges.push("Отменён");
  if (lesson.added) badges.push("Добавлен");
  if (lesson.time_changed) badges.push("Время изменено");
  if (lesson.subject_changed) badges.push("Замена");

  return (
    <li className={`lesson${lesson.cancelled ? " lesson--cancelled" : ""}`}>
      <span className="lesson__number" aria-hidden="true">
        {lesson.lesson_number}
      </span>
      <span className="lesson__body">
        <span className="lesson__subject">
          {lesson.subject_name ?? "—"}
        </span>
        {time && <span className="lesson__time">{time}</span>}
        {lesson.note && <span className="lesson__note">{lesson.note}</span>}
        {badges.length > 0 && (
          <span className="lesson__badges">
            {badges.map((b) => (
              <span key={b} className="badge">
                {b}
              </span>
            ))}
          </span>
        )}
      </span>
    </li>
  );
}

export function ExtraRow({
  activity,
  onEdit,
  onDelete,
}: {
  activity: ExtraActivity;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const time = activity.start_time
    ? `${activity.start_time}${activity.end_time ? `–${activity.end_time}` : ""}`
    : "";
  return (
    <li className="extra-row">
      <div className="extra-row__head">
        <span className="extra-row__title">{activity.title}</span>
        {activity.can_edit && (onEdit || onDelete) && (
          <span className="extra-row__actions">
            {onEdit && (
              <button type="button" className="extra-row__action" onClick={onEdit}>
                Изменить
              </button>
            )}
            {onDelete && (
              <button
                type="button"
                className="extra-row__action extra-row__action--danger"
                onClick={onDelete}
              >
                Удалить
              </button>
            )}
          </span>
        )}
      </div>
      <span className="extra-row__meta">
        {time}
        {activity.location ? ` · ${activity.location}` : ""}
      </span>
      {activity.note && <span className="extra-row__note">{activity.note}</span>}
    </li>
  );
}

const STATUS_LABEL: Record<string, string> = {
  active: "Актуально",
  overdue: "Просрочено",
  completed: "Выполнено",
};

export function HomeworkRow({
  homework,
  onToggle,
  toggling = false,
}: {
  homework: Homework;
  onToggle?: (isCompleted: boolean) => void;
  toggling?: boolean;
}) {
  return (
    <li className={`homework homework--${homework.status}`}>
      <div className="homework__head">
        <span className="homework__subject">{homework.subject_name}</span>
        <span className={`badge badge--${homework.status}`}>
          {STATUS_LABEL[homework.status] ?? homework.status}
        </span>
      </div>
      <p className="homework__description">{homework.description}</p>
      <div className="homework__foot">
        <span className="homework__due">Сдать: {formatDate(homework.due_date)}</span>
        {onToggle && homework.can_edit && (
          <button
            type="button"
            className="homework__toggle"
            disabled={toggling}
            onClick={() => onToggle(!homework.is_completed)}
          >
            {homework.is_completed ? "Вернуть в список" : "Выполнено"}
          </button>
        )}
      </div>
    </li>
  );
}

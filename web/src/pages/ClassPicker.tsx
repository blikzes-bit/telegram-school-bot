import { Link } from "react-router-dom";

import { useClasses } from "../api/hooks";
import {
  EmptyView,
  LoadingView,
  QueryError,
} from "../components/StateViews";

const ROLE_LABEL: Record<string, string> = {
  admin: "Администратор",
  member: "Участник",
};

/** Lets the user choose which class (chat) to open. */
export function ClassPicker() {
  const { data, isPending, isError, error, refetch } = useClasses();

  if (isPending) return <LoadingView label="Загружаем классы…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;
  if (data.length === 0) {
    return (
      <EmptyView message="Пока нет ни одного класса. Откройте приложение из нужного чата командой /web." />
    );
  }

  return (
    <main className="page">
      <h1 className="page__title">Выберите класс</h1>
      <ul className="card-list" aria-label="Список классов">
        {data.map((cls) => (
          <li key={cls.chat_id}>
            <Link
              className="card card--link"
              to={`/classes/${cls.chat_id}/today`}
            >
              <span className="card__title">
                {cls.title ?? `Класс ${cls.chat_id}`}
              </span>
              <span className="card__meta">
                {ROLE_LABEL[cls.role] ?? cls.role}
                {cls.timezone ? ` · ${cls.timezone}` : ""}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}

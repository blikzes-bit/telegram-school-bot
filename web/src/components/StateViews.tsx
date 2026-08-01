import type { ReactNode } from "react";

import { ApiError } from "../api/client";

/** Centred status block used for loading / empty / error / forbidden states. */
export function StateView({
  icon,
  title,
  message,
  action,
  role = "status",
}: {
  icon?: string;
  title: string;
  message?: string;
  action?: ReactNode;
  role?: "status" | "alert";
}) {
  return (
    <div className="state-view" role={role} aria-live="polite">
      {icon && (
        <div className="state-view__icon" aria-hidden="true">
          {icon}
        </div>
      )}
      <h2 className="state-view__title">{title}</h2>
      {message && <p className="state-view__message">{message}</p>}
      {action && <div className="state-view__action">{action}</div>}
    </div>
  );
}

export function LoadingView({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div className="state-view" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <p className="state-view__message">{label}</p>
    </div>
  );
}

export function EmptyView({ message }: { message: string }) {
  return <StateView icon="📭" title="Пусто" message={message} />;
}

export function ForbiddenView() {
  return (
    <StateView
      role="alert"
      icon="🔒"
      title="Нет доступа"
      message="У вас нет доступа к этому классу. Откройте приложение снова из нужного чата командой /web."
    />
  );
}

export function ConnectionErrorView({ onRetry }: { onRetry?: () => void }) {
  return (
    <StateView
      role="alert"
      icon="📡"
      title="Нет соединения"
      message="Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз."
      action={
        onRetry && (
          <button type="button" className="button" onClick={onRetry}>
            Повторить
          </button>
        )
      }
    />
  );
}

/** Pick the right view for a failed query based on the HTTP status. */
export function QueryError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  if (error instanceof ApiError && error.status === 403) {
    return <ForbiddenView />;
  }
  if (error instanceof ApiError && error.status === 401) {
    return (
      <StateView
        role="alert"
        icon="🔑"
        title="Требуется вход"
        message="Сессия истекла. Откройте приложение снова из чата командой /web."
      />
    );
  }
  return <ConnectionErrorView onRetry={onRetry} />;
}

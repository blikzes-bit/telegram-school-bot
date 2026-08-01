import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Me } from "../api/types";
import { getInitData } from "../telegram";
import { AuthContext } from "../auth/context";
import {
  ConnectionErrorView,
  ForbiddenView,
  LoadingView,
  StateView,
} from "../components/StateViews";

type AuthState =
  | { status: "loading" }
  | { status: "authed"; me: Me }
  | { status: "error"; error: unknown };

/**
 * Exchanges Telegram ``initData`` for a session cookie before rendering the app.
 *
 * The signed initData is posted once; on success the opaque session cookie is
 * set by the backend and every later request rides on it. All the failure modes
 * required by the spec are surfaced explicitly (no-Telegram, 401/403, network).
 */
export function AuthGate() {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    const initData = getInitData();
    if (!initData) {
      setState({ status: "error", error: new ApiError(0, "no-initdata") });
      return;
    }
    setState({ status: "loading" });
    api
      .authTelegram(initData)
      .then((me) => {
        if (!cancelled) setState({ status: "authed", me });
      })
      .catch((error) => {
        if (!cancelled) setState({ status: "error", error });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (state.status === "loading") {
    return <LoadingView label="Проверяем доступ…" />;
  }

  if (state.status === "error") {
    const { error } = state;
    if (error instanceof ApiError && error.status === 403) {
      return <ForbiddenView />;
    }
    if (error instanceof ApiError && error.message === "no-initdata") {
      return (
        <StateView
          role="alert"
          icon="📲"
          title="Откройте через Telegram"
          message="Это приложение открывается из чата класса командой /web. Данные Telegram не найдены."
        />
      );
    }
    if (error instanceof ApiError && error.status === 401) {
      return (
        <StateView
          role="alert"
          icon="🔑"
          title="Не удалось войти"
          message="Данные входа устарели или недействительны. Откройте приложение снова из чата командой /web."
        />
      );
    }
    return <ConnectionErrorView onRetry={() => setAttempt((n) => n + 1)} />;
  }

  return (
    <AuthContext.Provider value={state.me}>
      <Outlet />
    </AuthContext.Provider>
  );
}

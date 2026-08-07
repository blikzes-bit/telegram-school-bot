import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import type { ReactElement } from "react";

import { renderWithProviders } from "../test/utils";
import { classSettingsFixture, todayFixture } from "../test/fixtures";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      authTelegram: vi.fn(),
      logout: vi.fn(),
      me: vi.fn(),
      classes: vi.fn(),
      today: vi.fn(),
      schedule: vi.fn(),
      homework: vi.fn(),
      createHomework: vi.fn(),
      updateHomework: vi.fn(),
      deleteHomework: vi.fn(),
      setHomeworkCompleted: vi.fn(),
      extra: vi.fn(),
      createExtra: vi.fn(),
      updateExtra: vi.fn(),
      deleteExtra: vi.fn(),
      reminderSettings: vi.fn(),
      updateReminderSettings: vi.fn(),
      classSettings: vi.fn(),
      updateClassSettings: vi.fn(),
      auditLog: vi.fn(),
      exportUrl: vi.fn(),
    },
  };
});

import { api, ApiError } from "../api/client";
import { TodayPage } from "../pages/TodayPage";
import { HomeworkPage } from "../pages/HomeworkPage";
import { Route, Routes } from "react-router-dom";

const mockApi = vi.mocked(api);

function renderAt(chatPath: string, element: ReactElement) {
  return renderWithProviders(
    <Routes>
      <Route path="/classes/:chatId/*" element={element} />
    </Routes>,
    { route: chatPath },
  );
}

describe("dashboard states", () => {
  beforeEach(() => {
    // Both screens read the chat profile to decide what to show.
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
  });

  it("renders lessons and homework", async () => {
    mockApi.today.mockResolvedValue(todayFixture);
    renderAt("/classes/-100/today", <TodayPage />);

    expect(await screen.findByText("Уроки")).toBeInTheDocument();
    expect(screen.getAllByText("Математика").length).toBeGreaterThan(0);
    expect(screen.getByText("Стр. 42, номер 5")).toBeInTheDocument();
  });

  it("shows the forbidden view on 403", async () => {
    mockApi.today.mockRejectedValue(new ApiError(403, "нет доступа"));
    renderAt("/classes/-100/today", <TodayPage />);

    expect(await screen.findByText("Нет доступа")).toBeInTheDocument();
  });

  it("shows the connection-error view on network failure", async () => {
    mockApi.today.mockRejectedValue(new ApiError(0, "нет сети"));
    renderAt("/classes/-100/today", <TodayPage />);

    expect(await screen.findByText("Нет соединения")).toBeInTheDocument();
  });

  it("shows an empty state when there is no homework", async () => {
    mockApi.homework.mockResolvedValue([]);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    expect(await screen.findByText("Пусто")).toBeInTheDocument();
  });
});

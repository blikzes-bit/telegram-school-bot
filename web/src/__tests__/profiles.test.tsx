/**
 * The chat profile drives what the app shows.
 *
 * The rule under test: a section a profile does not use is **absent**, not
 * disabled — a tutor chat has no school timetable, so no timetable tab, no
 * lessons block, and no reminders that talk about one.
 */
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import type { ReactElement } from "react";

import { renderWithProviders } from "../test/utils";
import { classSettingsFixture, todayFixture } from "../test/fixtures";
import type { ClassSettings, ReminderSettings } from "../api/types";

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
      exportUrl: vi.fn(() => "/export"),
    },
  };
});

import { api } from "../api/client";
import { ClassLayout } from "../pages/ClassLayout";
import { SettingsPage } from "../pages/SettingsPage";
import { TodayPage } from "../pages/TodayPage";
import { Route, Routes } from "react-router-dom";

const mockApi = vi.mocked(api);

const TUTOR_SETTINGS: ClassSettings = {
  ...classSettingsFixture,
  profile: "tutor",
  profile_label: "👩‍🏫 Занятия с репетитором",
  features: { ...classSettingsFixture.features, school_schedule: false },
};

const REMINDERS: ReminderSettings = {
  hw_reminder_enabled: true,
  hw_reminder_time: "18:00",
  schedule_reminder_enabled: true,
  schedule_reminder_time: "20:00",
  hw_duetoday_enabled: true,
  hw_duetoday_time: "07:30",
  changes_reminder_enabled: true,
  extra_reminder_enabled: true,
  payment_reminder_enabled: true,
  payment_reminder_time: "10:00",
  quiet_start: null,
  quiet_end: null,
  can_edit: true,
};

const EMPTY_AUDIT = { items: [], total: 0, page: 1, page_size: 20 };

function renderAt(chatPath: string, element: ReactElement) {
  return renderWithProviders(
    <Routes>
      <Route path="/classes/:chatId/*" element={element} />
    </Routes>,
    { route: chatPath },
  );
}

describe("profile-driven navigation", () => {
  it("shows the timetable tab for a class", async () => {
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    renderAt("/classes/-100/today", <ClassLayout />);

    expect(await screen.findByText("Расписание")).toBeInTheDocument();
    expect(screen.getByText("Домашка")).toBeInTheDocument();
  });

  it("omits the timetable tab for a tutor chat", async () => {
    mockApi.classSettings.mockResolvedValue(TUTOR_SETTINGS);
    renderAt("/classes/-100/today", <ClassLayout />);

    // "Занятия" proves the bar has rendered with the profile applied.
    expect(await screen.findByText("Занятия")).toBeInTheDocument();
    expect(screen.queryByText("Расписание")).not.toBeInTheDocument();
  });
});

describe("profile-driven screens", () => {
  it("drops the lessons block from Today in a tutor chat", async () => {
    mockApi.today.mockResolvedValue(todayFixture);
    mockApi.classSettings.mockResolvedValue(TUTOR_SETTINGS);
    renderAt("/classes/-100/today", <TodayPage />);

    expect(await screen.findByText("Доп. занятия")).toBeInTheDocument();
    expect(screen.queryByText("Уроки")).not.toBeInTheDocument();
  });

  it("keeps the lessons block for a class", async () => {
    mockApi.today.mockResolvedValue(todayFixture);
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    renderAt("/classes/-100/today", <TodayPage />);

    expect(await screen.findByText("Уроки")).toBeInTheDocument();
  });

  it("hides timetable-only reminders in a tutor chat", async () => {
    mockApi.classSettings.mockResolvedValue(TUTOR_SETTINGS);
    mockApi.reminderSettings.mockResolvedValue(REMINDERS);
    mockApi.auditLog.mockResolvedValue(EMPTY_AUDIT);
    renderAt("/classes/-100/settings", <SettingsPage />);

    expect(await screen.findByText("ДЗ на завтра")).toBeInTheDocument();
    expect(screen.queryByText("Портфель на завтра")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Изменения расписания на завтра"),
    ).not.toBeInTheDocument();
  });

  it("offers those reminders in a class chat", async () => {
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    mockApi.reminderSettings.mockResolvedValue(REMINDERS);
    mockApi.auditLog.mockResolvedValue(EMPTY_AUDIT);
    renderAt("/classes/-100/settings", <SettingsPage />);

    expect(await screen.findByText("Портфель на завтра")).toBeInTheDocument();
  });
});

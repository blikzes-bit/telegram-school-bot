/**
 * The schedule screen's three modes.
 *
 * The point being tested is that the UI keeps "the repeating template" and "a
 * change for one date" apart, and that a member who may not edit is shown no
 * editing controls at all.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import type { DateOverrides, ScheduleTemplate } from "../api/types";

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
      scheduleTemplate: vi.fn(),
      saveLessonSlots: vi.fn(),
      saveScheduleDay: vi.fn(),
      dateOverrides: vi.fn(),
      setDayOverride: vi.fn(),
      clearDateOverrides: vi.fn(),
      setLessonOverride: vi.fn(),
      clearLessonOverride: vi.fn(),
      homework: vi.fn(),
      createHomework: vi.fn(),
      updateHomework: vi.fn(),
      deleteHomework: vi.fn(),
      setHomeworkCompleted: vi.fn(),
      extra: vi.fn(),
      createExtra: vi.fn(),
      updateExtra: vi.fn(),
      deleteExtra: vi.fn(),
      payments: vi.fn(),
      createPayment: vi.fn(),
      updatePayment: vi.fn(),
      setPaymentPaid: vi.fn(),
      deletePayment: vi.fn(),
      reminderSettings: vi.fn(),
      updateReminderSettings: vi.fn(),
      classSettings: vi.fn(),
      updateClassSettings: vi.fn(),
      members: vi.fn(),
      setMemberRole: vi.fn(),
      removeMember: vi.fn(),
      setAccessMode: vi.fn(),
      invites: vi.fn(),
      createInvite: vi.fn(),
      revokeInvite: vi.fn(),
      acceptInvite: vi.fn(),
      auditLog: vi.fn(),
      exportUrl: vi.fn(() => "/export"),
    },
  };
});

import { api } from "../api/client";
import { SchedulePage } from "../pages/SchedulePage";
import { Route, Routes } from "react-router-dom";

const mockApi = vi.mocked(api);

function template(overrides: Partial<ScheduleTemplate> = {}): ScheduleTemplate {
  return {
    week_type: "all",
    week_mode: false,
    week_types: ["all"],
    slots: [
      { lesson_number: 1, start_time: "08:00", end_time: "08:45" },
      { lesson_number: 2, start_time: "09:00", end_time: "09:45" },
    ],
    days: Array.from({ length: 7 }, (_, weekday) => ({
      weekday,
      lessons: [
        { lesson_number: 1, subject_name: weekday === 0 ? "Математика" : null },
        { lesson_number: 2, subject_name: null },
      ],
    })),
    can_edit: true,
    ...overrides,
  };
}

const DAY_TYPES = [
  { name: "free", label: "🟢 Свободный день", description: "уроков нет" },
  { name: "holiday", label: "🎉 Праздник", description: "праздник" },
  { name: "vacation", label: "🏖 Каникулы", description: "каникулы" },
  { name: "remote", label: "💻 Дистанционно", description: "дистанционно" },
];

function overrides(patch: Partial<DateOverrides> = {}): DateOverrides {
  return {
    date: "2024-01-15",
    day: null,
    lessons: [],
    day_type_options: DAY_TYPES,
    can_edit: true,
    ...patch,
  };
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/classes/:chatId/*" element={<SchedulePage />} />
    </Routes>,
    { route: "/classes/-100/schedule" },
  );
}

describe("schedule template editing", () => {
  it("saves the subjects of one weekday", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.scheduleTemplate.mockResolvedValue(template());
    mockApi.saveScheduleDay.mockResolvedValue(template());
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Обычное" }));
    const mondayFirst = await screen.findByDisplayValue("Математика");
    await user.clear(mondayFirst);
    await user.type(mondayFirst, "Химия");
    await user.click(screen.getByRole("button", { name: /Сохранить понедельник/ }));

    await waitFor(() =>
      expect(mockApi.saveScheduleDay).toHaveBeenCalledWith(
        -100,
        0,
        [
          { lesson_number: 1, subject_name: "Химия" },
          { lesson_number: 2, subject_name: null },
        ],
        "all",
      ),
    );
  });

  it("saves bell times as a whole set", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.scheduleTemplate.mockResolvedValue(template());
    mockApi.saveLessonSlots.mockResolvedValue(template());
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Обычное" }));
    const firstStart = await screen.findByDisplayValue("08:00");
    await user.clear(firstStart);
    await user.type(firstStart, "08:30");
    await user.click(screen.getByRole("button", { name: "Сохранить время" }));

    await waitFor(() => expect(mockApi.saveLessonSlots).toHaveBeenCalled());
    const [, slots] = mockApi.saveLessonSlots.mock.calls[0];
    expect(slots).toHaveLength(2);
    expect(slots[0].start_time).toBe("08:30");
  });

  it("offers no editing controls to a member who may not edit", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.scheduleTemplate.mockResolvedValue(template({ can_edit: false }));
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Обычное" }));
    expect(
      await screen.findByText("Менять расписание может только владелец класса."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить время" })).not.toBeInTheDocument();
    expect(await screen.findByDisplayValue("Математика")).toBeDisabled();
  });

  it("shows the A/B switch only when alternation is on", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.scheduleTemplate.mockResolvedValue(
      template({ week_mode: true, week_types: ["A", "B"], week_type: "A" }),
    );
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Обычное" }));
    expect(await screen.findByRole("tab", { name: "Неделя A" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Неделя B" })).toBeInTheDocument();
  });
});

describe("per-date changes", () => {
  it("marks a date as a holiday with a note", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.dateOverrides.mockResolvedValue(overrides());
    mockApi.setDayOverride.mockResolvedValue(overrides());
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "На дату" }));
    const note = await screen.findByPlaceholderText("Например, День города");
    await user.type(note, "День города");
    await user.click(screen.getByRole("button", { name: /Праздник/ }));

    await waitFor(() =>
      expect(mockApi.setDayOverride).toHaveBeenCalledWith(-100, expect.any(String), {
        day_type: "holiday",
        note: "День города",
      }),
    );
  });

  it("cancels one lesson on one date", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.dateOverrides.mockResolvedValue(overrides());
    mockApi.setLessonOverride.mockResolvedValue(overrides());
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "На дату" }));
    await user.click(await screen.findByRole("button", { name: "Отменить урок" }));

    await waitFor(() =>
      expect(mockApi.setLessonOverride).toHaveBeenCalledWith(
        -100, expect.any(String), 1, { action: "cancel" },
      ),
    );
  });

  it("cannot submit a replacement with nothing filled in", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.dateOverrides.mockResolvedValue(overrides());
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "На дату" }));
    expect(await screen.findByRole("button", { name: "Заменить" })).toBeDisabled();
  });

  it("shows a read-only view of existing changes to a plain member", async () => {
    const user = userEvent.setup();
    mockApi.schedule.mockResolvedValue({
      from_date: "2024-01-15", to_date: "2024-01-21", timezone: "Europe/Kyiv", days: [],
    });
    mockApi.dateOverrides.mockResolvedValue(
      overrides({
        can_edit: false,
        day: { day_type: "remote", day_type_label: "💻 Дистанционно", note: null },
        lessons: [
          {
            lesson_number: 2,
            action: "cancel",
            subject_name: null,
            start_time: null,
            end_time: null,
            note: null,
          },
        ],
      }),
    );
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "На дату" }));
    expect(await screen.findByText(/Дистанционно/)).toBeInTheDocument();
    expect(screen.getByText(/Урок 2: отменён/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить урок" })).not.toBeInTheDocument();
  });
});

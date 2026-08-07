/**
 * Editing and deleting homework, and the class-settings section.
 *
 * These assert the *contract with the server*: which request a given tap sends,
 * that a delete needs a confirmation first, and that a read-only user is shown
 * no controls at all (the server decides via ``can_edit`` — the UI only follows).
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";

import { renderWithProviders } from "../test/utils";
import { classSettingsFixture, homeworkFixture } from "../test/fixtures";

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
import { HomeworkPage } from "../pages/HomeworkPage";
import { SettingsPage } from "../pages/SettingsPage";
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

describe("homework editing", () => {
  it("prefills the form and sends only the edited fields", async () => {
    const user = userEvent.setup();
    mockApi.homework.mockResolvedValue([homeworkFixture]);
    mockApi.updateHomework.mockResolvedValue({
      ...homeworkFixture,
      description: "Стр. 43",
    });
    renderAt("/classes/-100/homework", <HomeworkPage />);

    await user.click(await screen.findByRole("button", { name: /Изменить/ }));

    const description = screen.getByDisplayValue("Стр. 42, номер 5");
    await user.clear(description);
    await user.type(description, "Стр. 43");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(mockApi.updateHomework).toHaveBeenCalledWith(-100, 1, {
        subject_name: "Математика",
        due_date: "2024-01-15",
        description: "Стр. 43",
      }),
    );
  });

  it("requires a confirmation before deleting", async () => {
    const user = userEvent.setup();
    mockApi.homework.mockResolvedValue([homeworkFixture]);
    mockApi.deleteHomework.mockResolvedValue(undefined);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    await user.click(await screen.findByRole("button", { name: /Удалить/ }));
    // Nothing is sent on the first tap — only the confirmation appears.
    expect(mockApi.deleteHomework).not.toHaveBeenCalled();
    expect(screen.getByText("Удалить задание навсегда?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Да, удалить" }));
    await waitFor(() => expect(mockApi.deleteHomework).toHaveBeenCalledWith(-100, 1));
  });

  it("cancelling the confirmation deletes nothing", async () => {
    const user = userEvent.setup();
    mockApi.homework.mockResolvedValue([homeworkFixture]);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    await user.click(await screen.findByRole("button", { name: /Удалить/ }));
    await user.click(screen.getByRole("button", { name: "Отмена" }));

    expect(mockApi.deleteHomework).not.toHaveBeenCalled();
    expect(screen.queryByText("Удалить задание навсегда?")).not.toBeInTheDocument();
  });

  it("offers no edit or delete control when the server says can_edit is false", async () => {
    mockApi.homework.mockResolvedValue([{ ...homeworkFixture, can_edit: false }]);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    expect(await screen.findByText("Математика")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Изменить/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить/ })).not.toBeInTheDocument();
  });

  it("shows the server's message when a delete is refused", async () => {
    const user = userEvent.setup();
    const { ApiError } = await import("../api/client");
    mockApi.homework.mockResolvedValue([homeworkFixture]);
    mockApi.deleteHomework.mockRejectedValue(new ApiError(403, "нельзя удалять"));
    renderAt("/classes/-100/homework", <HomeworkPage />);

    await user.click(await screen.findByRole("button", { name: /Удалить/ }));
    await user.click(screen.getByRole("button", { name: "Да, удалить" }));

    expect(await screen.findByText("нельзя удалять")).toBeInTheDocument();
  });
});

describe("class settings", () => {
  it("renames the class and shows the local time", async () => {
    const user = userEvent.setup();
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    mockApi.updateClassSettings.mockResolvedValue({
      ...classSettingsFixture,
      title: "9-А",
    });
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    const name = await screen.findByDisplayValue("5-А класс");
    expect(screen.getByText(/09:30/)).toBeInTheDocument();

    await user.clear(name);
    await user.type(name, "9-А");
    await user.click(screen.getByRole("button", { name: "Сохранить название" }));

    await waitFor(() =>
      expect(mockApi.updateClassSettings).toHaveBeenCalledWith(-100, { title: "9-А" }),
    );
  });

  it("sends the picked timezone", async () => {
    const user = userEvent.setup();
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    mockApi.updateClassSettings.mockResolvedValue(classSettingsFixture);
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    const select = await screen.findByDisplayValue("🇺🇦 Киев");
    await user.selectOptions(select, "Europe/Warsaw");

    await waitFor(() =>
      expect(mockApi.updateClassSettings).toHaveBeenCalledWith(-100, {
        timezone: "Europe/Warsaw",
      }),
    );
  });

  it("keeps a timezone that is outside the short picker selectable", async () => {
    mockApi.classSettings.mockResolvedValue({
      ...classSettingsFixture,
      timezone: "Asia/Tokyo",
    });
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    expect(await screen.findByDisplayValue("Asia/Tokyo")).toBeInTheDocument();
  });

  it("disables every control for a member who may not edit", async () => {
    mockApi.classSettings.mockResolvedValue({
      ...classSettingsFixture,
      can_edit: false,
    });
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    expect(await screen.findByDisplayValue("5-А класс")).toBeDisabled();
    expect(
      screen.getByText("Менять настройки класса могут только администраторы чата."),
    ).toBeInTheDocument();
  });

  it("hides the homework-policy picker in a personal diary", async () => {
    mockApi.classSettings.mockResolvedValue({
      ...classSettingsFixture,
      chat_type: "private",
      profile: "personal",
      features: { ...classSettingsFixture.features, homework_policy: false },
    });
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    expect(await screen.findByText("Название дневника")).toBeInTheDocument();
    expect(screen.queryByText("Кто может менять задания")).not.toBeInTheDocument();
  });

  it("switches the profile and reports it to the server", async () => {
    const user = userEvent.setup();
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    mockApi.updateClassSettings.mockResolvedValue(classSettingsFixture);
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    const select = await screen.findByDisplayValue("🏫 Класс");
    await user.selectOptions(select, "tutor");

    await waitFor(() =>
      expect(mockApi.updateClassSettings).toHaveBeenCalledWith(-100, {
        profile: "tutor",
      }),
    );
  });
});

/**
 * Personal homework marks, as seen from the app.
 *
 * The wording has to change too: "Выполнено" is a statement about the class,
 * "Я сделал" is a statement about you, and a teacher wants the count.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "../test/utils";
import { classSettingsFixture, homeworkFixture } from "../test/fixtures";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      homework: vi.fn(),
      setHomeworkCompleted: vi.fn(),
      classSettings: vi.fn(),
      updateClassSettings: vi.fn(),
      reminderSettings: vi.fn(),
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

const PERSONAL = {
  ...homeworkFixture,
  per_student: true,
  completed_count: 3,
  is_completed: false,
};

function renderAt(path: string, element: React.ReactElement) {
  return renderWithProviders(
    <Routes>
      <Route path="/classes/:chatId/*" element={element} />
    </Routes>,
    { route: path },
  );
}

describe("personal homework marks", () => {
  it("says 'Я сделал' and shows how many are done", async () => {
    mockApi.homework.mockResolvedValue([PERSONAL]);
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    expect(await screen.findByRole("button", { name: "Я сделал" })).toBeInTheDocument();
    expect(screen.getByText(/сделали: 3/)).toBeInTheDocument();
  });

  it("offers to undo my own mark, not the class's", async () => {
    mockApi.homework.mockResolvedValue([{ ...PERSONAL, is_completed: true }]);
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    expect(await screen.findByRole("button", { name: "Я не сделал" })).toBeInTheDocument();
  });

  it("keeps the class wording where marks are shared", async () => {
    mockApi.homework.mockResolvedValue([homeworkFixture]);
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    renderAt("/classes/-100/homework", <HomeworkPage />);

    expect(await screen.findByRole("button", { name: "Выполнено" })).toBeInTheDocument();
    expect(screen.queryByText(/сделали:/)).not.toBeInTheDocument();
  });

  it("switches the setting on from the class screen", async () => {
    const user = userEvent.setup();
    mockApi.classSettings.mockResolvedValue(classSettingsFixture);
    mockApi.updateClassSettings.mockResolvedValue({
      ...classSettingsFixture,
      per_student_homework: true,
    });
    mockApi.reminderSettings.mockRejectedValue(new Error("not used here"));
    renderAt("/classes/-100/settings", <SettingsPage />);

    await user.click(
      await screen.findByRole("checkbox", { name: /Каждый отмечает выполненное за себя/ }),
    );

    await waitFor(() =>
      expect(mockApi.updateClassSettings).toHaveBeenCalledWith(-100, {
        per_student_homework: true,
      }),
    );
  });
});

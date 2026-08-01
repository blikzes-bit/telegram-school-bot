import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "../test/utils";
import { classesFixture, todayFixture } from "../test/fixtures";

vi.mock("../telegram", () => ({
  getInitData: () => "signed-init-data",
  applyTelegramTheme: () => {},
  getWebApp: () => undefined,
}));

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
      setHomeworkCompleted: vi.fn(),
      extra: vi.fn(),
      createExtra: vi.fn(),
      updateExtra: vi.fn(),
      deleteExtra: vi.fn(),
      reminderSettings: vi.fn(),
      updateReminderSettings: vi.fn(),
      auditLog: vi.fn(),
      exportUrl: vi.fn(),
    },
  };
});

import { api } from "../api/client";
import { App } from "../App";

const mockApi = vi.mocked(api);

describe("auth + navigation flow", () => {
  it("authenticates and shows the class picker", async () => {
    mockApi.authTelegram.mockResolvedValue({
      telegram_user_id: 1,
      display_name: "Аня",
    });
    mockApi.classes.mockResolvedValue(classesFixture);

    renderWithProviders(<App />, { route: "/" });

    expect(await screen.findByText("Выберите класс")).toBeInTheDocument();
    expect(screen.getByText("5-А класс")).toBeInTheDocument();
    expect(mockApi.authTelegram).toHaveBeenCalledWith("signed-init-data");
  });

  it("switches to a class and loads its dashboard", async () => {
    mockApi.authTelegram.mockResolvedValue({
      telegram_user_id: 1,
      display_name: "Аня",
    });
    mockApi.classes.mockResolvedValue(classesFixture);
    mockApi.today.mockResolvedValue(todayFixture);

    renderWithProviders(<App />, { route: "/" });

    const secondClass = await screen.findByText("6-Б класс");
    await userEvent.click(secondClass);

    await waitFor(() =>
      expect(mockApi.today).toHaveBeenCalledWith(-101, undefined),
    );
    expect(await screen.findByText("Стр. 42, номер 5")).toBeInTheDocument();
  });
});

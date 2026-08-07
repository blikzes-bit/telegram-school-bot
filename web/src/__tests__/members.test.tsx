/**
 * The members screen.
 *
 * What matters here is that the UI follows the server's answer rather than
 * guessing: a member who may not manage sees no controls at all, the owner's own
 * row is not editable, an invite link is shown once, and every change is sent as
 * the server expects it.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";

import { renderWithProviders } from "../test/utils";
import type { Invite, MembersPage as MembersPageData } from "../api/types";

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
import { MembersPage } from "../pages/MembersPage";
import { Route, Routes } from "react-router-dom";

const mockApi = vi.mocked(api);

const ROLES = [
  { name: "editor", label: "✍️ Редактор", description: "ведёт домашку" },
  { name: "student", label: "🎓 Ученик", description: "отмечает домашку" },
  { name: "viewer", label: "👀 Только смотрит", description: "только смотрит" },
];

const MODES = [
  { name: "telegram", label: "👥 Как в Telegram", description: "как раньше" },
  { name: "roles", label: "🔒 Только выбранные люди", description: "вносит владелец" },
];

function page(overrides: Partial<MembersPageData> = {}): MembersPageData {
  return {
    members: [
      {
        user_id: 1,
        display_name: "Репетитор",
        role: "owner",
        role_label: "👑 Владелец",
        app_role: null,
        is_owner: true,
        is_self: true,
      },
      {
        user_id: 2,
        display_name: "Мама",
        role: "viewer",
        role_label: "👀 Только смотрит",
        app_role: null,
        is_owner: false,
        is_self: false,
      },
    ],
    access_mode: "roles",
    access_mode_label: "🔒 Только выбранные люди",
    access_mode_options: MODES,
    assignable_roles: ROLES,
    can_manage: true,
    ...overrides,
  };
}

const INVITE: Invite = {
  id: 5,
  app_role: "viewer",
  role_label: "👀 Только смотрит",
  created_at: "2026-08-08T00:00:00+00:00",
  expires_at: "2026-08-09T00:00:00+00:00",
  created_by_name: "Репетитор",
  token: "secret-token",
  url: "https://t.me/bot/app?startapp=inv_secret-token",
};

function renderAt(chatPath: string, element: ReactElement) {
  return renderWithProviders(
    <Routes>
      <Route path="/classes/:chatId/*" element={element} />
    </Routes>,
    { route: chatPath },
  );
}

describe("members screen", () => {
  it("assigns a role to a member", async () => {
    const user = userEvent.setup();
    mockApi.members.mockResolvedValue(page());
    mockApi.setMemberRole.mockResolvedValue(page());
    mockApi.invites.mockResolvedValue([]);
    renderAt("/classes/-100/members", <MembersPage />);

    const select = await screen.findByDisplayValue("Только смотрит (роль не выдана)");
    await user.selectOptions(select, "editor");

    await waitFor(() =>
      expect(mockApi.setMemberRole).toHaveBeenCalledWith(-100, 2, "editor"),
    );
  });

  it("does not offer controls for the owner's own row", async () => {
    mockApi.members.mockResolvedValue(page());
    mockApi.invites.mockResolvedValue([]);
    renderAt("/classes/-100/members", <MembersPage />);

    expect(await screen.findByText(/Репетитор/)).toBeInTheDocument();
    // Exactly one editable member (Мама) — the owner row has no role picker.
    expect(screen.getAllByText("Что может делать")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Убрать из класса" })).toHaveLength(1);
  });

  it("removing a member takes a confirmation", async () => {
    const user = userEvent.setup();
    mockApi.members.mockResolvedValue(page());
    mockApi.removeMember.mockResolvedValue(undefined);
    mockApi.invites.mockResolvedValue([]);
    renderAt("/classes/-100/members", <MembersPage />);

    await user.click(await screen.findByRole("button", { name: "Убрать из класса" }));
    expect(mockApi.removeMember).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Да, убрать" }));
    await waitFor(() => expect(mockApi.removeMember).toHaveBeenCalledWith(-100, 2));
  });

  it("shows no management controls to a plain member", async () => {
    mockApi.members.mockResolvedValue(page({ can_manage: false }));
    renderAt("/classes/-100/members", <MembersPage />);

    expect(
      await screen.findByText("Менять участников может только владелец класса."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Что может делать")).not.toBeInTheDocument();
    expect(screen.queryByText("🔗 Пригласить")).not.toBeInTheDocument();
    // The invitation list must not even be requested.
    expect(mockApi.invites).not.toHaveBeenCalled();
  });

  it("creates an invite and shows the link once", async () => {
    const user = userEvent.setup();
    mockApi.members.mockResolvedValue(page());
    mockApi.invites.mockResolvedValue([]);
    mockApi.createInvite.mockResolvedValue(INVITE);
    renderAt("/classes/-100/members", <MembersPage />);

    await user.click(await screen.findByRole("button", { name: "Создать ссылку" }));

    await waitFor(() => expect(mockApi.createInvite).toHaveBeenCalledWith(-100, "viewer"));
    expect(await screen.findByText(INVITE.url!)).toBeInTheDocument();
    expect(
      screen.getByText(/второй раз её показать не получится/),
    ).toBeInTheDocument();
  });

  it("revokes an invite", async () => {
    const user = userEvent.setup();
    mockApi.members.mockResolvedValue(page());
    mockApi.invites.mockResolvedValue([{ ...INVITE, token: null, url: null }]);
    mockApi.revokeInvite.mockResolvedValue(undefined);
    renderAt("/classes/-100/members", <MembersPage />);

    await user.click(await screen.findByRole("button", { name: "Отозвать" }));
    await waitFor(() => expect(mockApi.revokeInvite).toHaveBeenCalledWith(-100, 5));
  });

  it("switches the access mode", async () => {
    const user = userEvent.setup();
    mockApi.members.mockResolvedValue(page({ access_mode: "telegram" }));
    mockApi.setAccessMode.mockResolvedValue(page());
    mockApi.invites.mockResolvedValue([]);
    renderAt("/classes/-100/members", <MembersPage />);

    const select = await screen.findByDisplayValue("👥 Как в Telegram");
    // While the chat is on Telegram rights, the screen says roles do nothing yet.
    expect(screen.getByText(/роли ниже ни на что не влияют/)).toBeInTheDocument();

    await user.selectOptions(select, "roles");
    await waitFor(() => expect(mockApi.setAccessMode).toHaveBeenCalledWith(-100, "roles"));
  });
});

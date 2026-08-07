/**
 * Thin fetch wrapper around the v1 API.
 *
 * Every request is same-origin with ``credentials: "include"`` so the HttpOnly
 * session cookie is sent automatically — the client never reads or stores a
 * token itself. Non-2xx responses raise a typed ``ApiError`` carrying the HTTP
 * status so callers (and TanStack Query) can branch on 401/403 vs. a transport
 * failure.
 */
import type {
  AccessMode,
  AppRole,
  AuditPage,
  DateOverrides,
  DayType,
  LessonSlot,
  ScheduleDayLesson,
  ScheduleTemplate,
  ClassInfo,
  ClassSettings,
  ClassSettingsUpdateInput,
  ExtraActivity,
  ExtraActivityCreateInput,
  ExtraActivityUpdateInput,
  Homework,
  HomeworkCreateInput,
  HomeworkUpdateInput,
  Invite,
  InviteAccepted,
  Me,
  MembersPage,
  Payment,
  PaymentCreateInput,
  PaymentUpdateInput,
  ReminderSettings,
  ReminderSettingsUpdateInput,
  ScheduleRange,
  Today,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    // Network / connection failure — distinct from an HTTP error status.
    throw new ApiError(0, "Не удалось соединиться с сервером");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the default message */
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export const api = {
  authTelegram: (initData: string) =>
    request<Me>("/auth/telegram", {
      method: "POST",
      body: JSON.stringify({ init_data: initData }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<Me>("/me"),
  classes: () => request<ClassInfo[]>("/classes"),
  today: (chatId: number, date?: string) =>
    request<Today>(
      `/classes/${chatId}/today${date ? `?date=${encodeURIComponent(date)}` : ""}`,
    ),
  schedule: (chatId: number, from: string, to: string) =>
    request<ScheduleRange>(
      `/classes/${chatId}/schedule?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
    ),
  homework: (chatId: number, status?: string) =>
    request<Homework[]>(
      `/classes/${chatId}/homework${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  createHomework: (chatId: number, input: HomeworkCreateInput) =>
    request<Homework>(`/classes/${chatId}/homework`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateHomework: (chatId: number, homeworkId: number, input: HomeworkUpdateInput) =>
    request<Homework>(`/classes/${chatId}/homework/${homeworkId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  deleteHomework: (chatId: number, homeworkId: number) =>
    request<void>(`/classes/${chatId}/homework/${homeworkId}`, { method: "DELETE" }),
  setHomeworkCompleted: (chatId: number, homeworkId: number, isCompleted: boolean) =>
    request<Homework>(`/classes/${chatId}/homework/${homeworkId}/complete`, {
      method: "PATCH",
      body: JSON.stringify({ is_completed: isCompleted }),
    }),
  extra: (chatId: number, from: string, to: string) =>
    request<ExtraActivity[]>(
      `/classes/${chatId}/extra?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
    ),
  createExtra: (chatId: number, input: ExtraActivityCreateInput) =>
    request<ExtraActivity>(`/classes/${chatId}/extra`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateExtra: (chatId: number, activityId: number, input: ExtraActivityUpdateInput) =>
    request<ExtraActivity>(`/classes/${chatId}/extra/${activityId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  deleteExtra: (chatId: number, activityId: number) =>
    request<void>(`/classes/${chatId}/extra/${activityId}`, { method: "DELETE" }),
  reminderSettings: (chatId: number) =>
    request<ReminderSettings>(`/classes/${chatId}/settings/reminders`),
  updateReminderSettings: (chatId: number, input: ReminderSettingsUpdateInput) =>
    request<ReminderSettings>(`/classes/${chatId}/settings/reminders`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  scheduleTemplate: (chatId: number, weekType?: string) =>
    request<ScheduleTemplate>(
      `/classes/${chatId}/schedule/template${weekType ? `?week_type=${weekType}` : ""}`,
    ),
  saveLessonSlots: (chatId: number, slots: LessonSlot[]) =>
    request<ScheduleTemplate>(`/classes/${chatId}/schedule/slots`, {
      method: "PUT",
      body: JSON.stringify({ slots }),
    }),
  saveScheduleDay: (
    chatId: number,
    weekday: number,
    lessons: ScheduleDayLesson[],
    weekType?: string,
  ) =>
    request<ScheduleTemplate>(
      `/classes/${chatId}/schedule/template/${weekday}${weekType ? `?week_type=${weekType}` : ""}`,
      { method: "PUT", body: JSON.stringify({ lessons }) },
    ),
  dateOverrides: (chatId: number, date: string) =>
    request<DateOverrides>(`/classes/${chatId}/overrides/${date}`),
  setDayOverride: (
    chatId: number,
    date: string,
    input: { day_type: DayType | null; note?: string },
  ) =>
    request<DateOverrides>(`/classes/${chatId}/overrides/${date}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  clearDateOverrides: (chatId: number, date: string) =>
    request<DateOverrides>(`/classes/${chatId}/overrides/${date}`, { method: "DELETE" }),
  setLessonOverride: (
    chatId: number,
    date: string,
    lessonNumber: number,
    input: {
      action: "cancel" | "set";
      subject_name?: string;
      start_time?: string;
      end_time?: string;
      note?: string;
    },
  ) =>
    request<DateOverrides>(
      `/classes/${chatId}/overrides/${date}/lessons/${lessonNumber}`,
      { method: "PUT", body: JSON.stringify(input) },
    ),
  clearLessonOverride: (chatId: number, date: string, lessonNumber: number) =>
    request<DateOverrides>(
      `/classes/${chatId}/overrides/${date}/lessons/${lessonNumber}`,
      { method: "DELETE" },
    ),
  payments: (chatId: number, unpaidOnly = false) =>
    request<Payment[]>(
      `/classes/${chatId}/payments${unpaidOnly ? "?unpaid=true" : ""}`,
    ),
  createPayment: (chatId: number, input: PaymentCreateInput) =>
    request<Payment>(`/classes/${chatId}/payments`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updatePayment: (chatId: number, paymentId: number, input: PaymentUpdateInput) =>
    request<Payment>(`/classes/${chatId}/payments/${paymentId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  setPaymentPaid: (chatId: number, paymentId: number, isPaid: boolean) =>
    request<Payment>(`/classes/${chatId}/payments/${paymentId}/paid`, {
      method: "PATCH",
      body: JSON.stringify({ is_paid: isPaid }),
    }),
  deletePayment: (chatId: number, paymentId: number) =>
    request<void>(`/classes/${chatId}/payments/${paymentId}`, { method: "DELETE" }),
  members: (chatId: number) => request<MembersPage>(`/classes/${chatId}/members`),
  setMemberRole: (chatId: number, userId: number, appRole: AppRole | null) =>
    request<MembersPage>(`/classes/${chatId}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ app_role: appRole }),
    }),
  removeMember: (chatId: number, userId: number) =>
    request<void>(`/classes/${chatId}/members/${userId}`, { method: "DELETE" }),
  setAccessMode: (chatId: number, mode: AccessMode) =>
    request<MembersPage>(
      `/classes/${chatId}/access-mode?mode=${encodeURIComponent(mode)}`,
      { method: "PUT" },
    ),
  invites: (chatId: number) => request<Invite[]>(`/classes/${chatId}/invites`),
  createInvite: (chatId: number, appRole: AppRole, ttlHours = 24) =>
    request<Invite>(`/classes/${chatId}/invites`, {
      method: "POST",
      body: JSON.stringify({ app_role: appRole, ttl_hours: ttlHours }),
    }),
  revokeInvite: (chatId: number, inviteId: number) =>
    request<void>(`/classes/${chatId}/invites/${inviteId}`, { method: "DELETE" }),
  acceptInvite: (token: string) =>
    request<InviteAccepted>("/invites/accept", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  classSettings: (chatId: number) =>
    request<ClassSettings>(`/classes/${chatId}/settings/class`),
  updateClassSettings: (chatId: number, input: ClassSettingsUpdateInput) =>
    request<ClassSettings>(`/classes/${chatId}/settings/class`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  auditLog: (chatId: number, page: number, entityType?: string) =>
    request<AuditPage>(
      `/classes/${chatId}/audit?page=${page}${entityType ? `&entity_type=${encodeURIComponent(entityType)}` : ""}`,
    ),
  exportUrl: (chatId: number, kind: "backup.json" | "audit.json" | "schedule.csv" | "calendar.ics") =>
    `${BASE}/classes/${chatId}/export/${kind}`,
};

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "./client";

/** Do not retry auth or connection failures — they will not fix themselves. */
function retry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && [0, 401, 403].includes(error.status)) {
    return false;
  }
  return failureCount < 2;
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me, retry });
}

export function useClasses() {
  return useQuery({ queryKey: ["classes"], queryFn: api.classes, retry });
}

export function useToday(chatId: number, date?: string) {
  return useQuery({
    queryKey: ["today", chatId, date ?? null],
    queryFn: () => api.today(chatId, date),
    retry,
  });
}

export function useSchedule(chatId: number, from: string, to: string) {
  return useQuery({
    queryKey: ["schedule", chatId, from, to],
    queryFn: () => api.schedule(chatId, from, to),
    retry,
  });
}

export function useHomework(chatId: number, status?: string) {
  return useQuery({
    queryKey: ["homework", chatId, status ?? "all"],
    queryFn: () => api.homework(chatId, status),
    retry,
  });
}

export function useExtra(chatId: number, from: string, to: string) {
  return useQuery({
    queryKey: ["extra", chatId, from, to],
    queryFn: () => api.extra(chatId, from, to),
    retry,
  });
}

export function useCreateHomework(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Parameters<typeof api.createHomework>[1]) =>
      api.createHomework(chatId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["homework", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useSetHomeworkCompleted(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ homeworkId, isCompleted }: { homeworkId: number; isCompleted: boolean }) =>
      api.setHomeworkCompleted(chatId, homeworkId, isCompleted),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["homework", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useUpdateHomework(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      homeworkId,
      input,
    }: {
      homeworkId: number;
      input: Parameters<typeof api.updateHomework>[2];
    }) => api.updateHomework(chatId, homeworkId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["homework", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useDeleteHomework(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (homeworkId: number) => api.deleteHomework(chatId, homeworkId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["homework", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useCreateExtra(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Parameters<typeof api.createExtra>[1]) =>
      api.createExtra(chatId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["extra", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useUpdateExtra(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      activityId,
      input,
    }: {
      activityId: number;
      input: Parameters<typeof api.updateExtra>[2];
    }) => api.updateExtra(chatId, activityId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["extra", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useDeleteExtra(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (activityId: number) => api.deleteExtra(chatId, activityId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["extra", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useReminderSettings(chatId: number) {
  return useQuery({
    queryKey: ["reminderSettings", chatId],
    queryFn: () => api.reminderSettings(chatId),
    retry,
  });
}

export function useUpdateReminderSettings(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Parameters<typeof api.updateReminderSettings>[1]) =>
      api.updateReminderSettings(chatId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reminderSettings", chatId] });
    },
  });
}

export function useClassSettings(chatId: number) {
  return useQuery({
    queryKey: ["classSettings", chatId],
    queryFn: () => api.classSettings(chatId),
    retry,
  });
}

export function useUpdateClassSettings(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Parameters<typeof api.updateClassSettings>[1]) =>
      api.updateClassSettings(chatId, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["classSettings", chatId] });
      // The class name and timezone are shown outside this screen too.
      queryClient.invalidateQueries({ queryKey: ["classes"] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useScheduleTemplate(chatId: number, weekType?: string) {
  return useQuery({
    queryKey: ["scheduleTemplate", chatId, weekType ?? "default"],
    queryFn: () => api.scheduleTemplate(chatId, weekType),
    retry,
  });
}

/** Template edits also change every future date, so the effective schedule and
 * the dashboard are both invalidated. */
function useTemplateMutation<TArgs>(
  chatId: number,
  fn: (args: TArgs) => Promise<import("./types").ScheduleTemplate>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduleTemplate", chatId] });
      queryClient.invalidateQueries({ queryKey: ["schedule", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useSaveLessonSlots(chatId: number) {
  return useTemplateMutation(chatId, (slots: import("./types").LessonSlot[]) =>
    api.saveLessonSlots(chatId, slots),
  );
}

export function useSaveScheduleDay(chatId: number) {
  return useTemplateMutation(
    chatId,
    ({
      weekday,
      lessons,
      weekType,
    }: {
      weekday: number;
      lessons: import("./types").ScheduleDayLesson[];
      weekType?: string;
    }) => api.saveScheduleDay(chatId, weekday, lessons, weekType),
  );
}

export function useDateOverrides(chatId: number, date: string, enabled = true) {
  return useQuery({
    queryKey: ["overrides", chatId, date],
    queryFn: () => api.dateOverrides(chatId, date),
    enabled,
    retry,
  });
}

/** A per-date change alters the effective schedule for that date only, but the
 * dashboard may be showing exactly that date. */
function useOverrideMutation<TArgs>(
  chatId: number,
  date: string,
  fn: (args: TArgs) => Promise<import("./types").DateOverrides>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (page) => {
      queryClient.setQueryData(["overrides", chatId, date], page);
      queryClient.invalidateQueries({ queryKey: ["schedule", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useSetDayOverride(chatId: number, date: string) {
  return useOverrideMutation(
    chatId,
    date,
    (input: Parameters<typeof api.setDayOverride>[2]) =>
      api.setDayOverride(chatId, date, input),
  );
}

export function useClearDateOverrides(chatId: number, date: string) {
  return useOverrideMutation(chatId, date, () => api.clearDateOverrides(chatId, date));
}

export function useSetLessonOverride(chatId: number, date: string) {
  return useOverrideMutation(
    chatId,
    date,
    ({
      lessonNumber,
      input,
    }: {
      lessonNumber: number;
      input: Parameters<typeof api.setLessonOverride>[3];
    }) => api.setLessonOverride(chatId, date, lessonNumber, input),
  );
}

export function useClearLessonOverride(chatId: number, date: string) {
  return useOverrideMutation(chatId, date, (lessonNumber: number) =>
    api.clearLessonOverride(chatId, date, lessonNumber),
  );
}

export function usePayments(chatId: number, enabled = true) {
  return useQuery({
    queryKey: ["payments", chatId],
    queryFn: () => api.payments(chatId),
    enabled,
    retry,
  });
}

/** Every payment mutation refreshes the list and the dashboard, since "Сегодня"
 * shows what is due. */
function usePaymentMutation<TArgs>(chatId: number, fn: (args: TArgs) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["payments", chatId] });
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
    },
  });
}

export function useCreatePayment(chatId: number) {
  return usePaymentMutation(chatId, (input: Parameters<typeof api.createPayment>[1]) =>
    api.createPayment(chatId, input),
  );
}

export function useUpdatePayment(chatId: number) {
  return usePaymentMutation(
    chatId,
    ({ paymentId, input }: { paymentId: number; input: Parameters<typeof api.updatePayment>[2] }) =>
      api.updatePayment(chatId, paymentId, input),
  );
}

export function useSetPaymentPaid(chatId: number) {
  return usePaymentMutation(
    chatId,
    ({ paymentId, isPaid }: { paymentId: number; isPaid: boolean }) =>
      api.setPaymentPaid(chatId, paymentId, isPaid),
  );
}

export function useDeletePayment(chatId: number) {
  return usePaymentMutation(chatId, (paymentId: number) =>
    api.deletePayment(chatId, paymentId),
  );
}

export function useMembers(chatId: number) {
  return useQuery({
    queryKey: ["members", chatId],
    queryFn: () => api.members(chatId),
    retry,
  });
}

/** Every member mutation returns the fresh page, so the cache is set from the
 * server's answer rather than guessed — a role change also changes what the
 * *actor* may do next. */
function useMembersMutation<TArgs>(
  chatId: number,
  fn: (args: TArgs) => Promise<import("./types").MembersPage>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (page) => {
      queryClient.setQueryData(["members", chatId], page);
      queryClient.invalidateQueries({ queryKey: ["today", chatId] });
      queryClient.invalidateQueries({ queryKey: ["homework", chatId] });
    },
  });
}

export function useSetMemberRole(chatId: number) {
  return useMembersMutation(
    chatId,
    ({ userId, appRole }: { userId: number; appRole: import("./types").AppRole | null }) =>
      api.setMemberRole(chatId, userId, appRole),
  );
}

export function useSetAccessMode(chatId: number) {
  return useMembersMutation(chatId, (mode: import("./types").AccessMode) =>
    api.setAccessMode(chatId, mode),
  );
}

export function useRemoveMember(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: number) => api.removeMember(chatId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["members", chatId] });
      queryClient.invalidateQueries({ queryKey: ["classes"] });
    },
  });
}

export function useInvites(chatId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["invites", chatId],
    queryFn: () => api.invites(chatId),
    enabled,
    retry,
  });
}

export function useCreateInvite(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (appRole: import("./types").AppRole) => api.createInvite(chatId, appRole),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["invites", chatId] }),
  });
}

export function useRevokeInvite(chatId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (inviteId: number) => api.revokeInvite(chatId, inviteId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["invites", chatId] }),
  });
}

export function useAcceptInvite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => api.acceptInvite(token),
    // A new class just appeared for this user.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["classes"] }),
  });
}

export function useAuditLog(chatId: number, page: number, entityType: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["audit", chatId, page, entityType ?? "all"],
    queryFn: () => api.auditLog(chatId, page, entityType),
    enabled,
    retry,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.logout,
    onSuccess: () => queryClient.clear(),
  });
}

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

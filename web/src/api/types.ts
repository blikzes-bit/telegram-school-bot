/** DTOs mirrored from the FastAPI ``application/dto.py`` (read-only, stage 1). */

export interface Me {
  telegram_user_id: number;
  display_name: string | null;
}

export interface ClassInfo {
  chat_id: number;
  title: string | null;
  role: string;
  timezone: string;
}

export interface Permissions {
  is_admin: boolean;
  can_edit_homework: boolean;
  can_edit_schedule: boolean;
}

export interface Lesson {
  lesson_number: number;
  start_time: string | null;
  end_time: string | null;
  subject_name: string | null;
  cancelled: boolean;
  added: boolean;
  time_changed: boolean;
  subject_changed: boolean;
  note: string | null;
}

export interface ExtraActivity {
  id: number;
  title: string;
  kind: string;
  day_of_week: number | null;
  activity_date: string | null;
  start_time: string;
  end_time: string | null;
  location: string | null;
  note: string | null;
  can_edit: boolean;
}

export interface ExtraActivityCreateInput {
  title: string;
  kind: "weekly" | "once";
  day_of_week?: number;
  activity_date?: string;
  start_time: string;
  end_time?: string;
  location?: string;
  note?: string;
}

export interface ExtraActivityUpdateInput {
  title?: string;
  start_time?: string;
  end_time?: string;
  location?: string;
  note?: string;
}

export type HomeworkStatus = "active" | "completed" | "overdue";

export interface Homework {
  id: number;
  subject_name: string;
  due_date: string;
  description: string;
  is_completed: boolean;
  status: HomeworkStatus;
  can_edit: boolean;
}

export interface HomeworkCreateInput {
  subject_name: string;
  due_date: string;
  description: string;
}

export interface DaySchedule {
  date: string;
  weekday: number;
  week_type: string;
  day_type: string | null;
  day_note: string | null;
  lessons: Lesson[];
  extra: ExtraActivity[];
}

export interface ScheduleRange {
  from_date: string;
  to_date: string;
  timezone: string;
  days: DaySchedule[];
}

export interface ReminderSettings {
  hw_reminder_enabled: boolean;
  hw_reminder_time: string;
  schedule_reminder_enabled: boolean;
  schedule_reminder_time: string;
  hw_duetoday_enabled: boolean;
  hw_duetoday_time: string;
  changes_reminder_enabled: boolean;
  extra_reminder_enabled: boolean;
  quiet_start: string | null;
  quiet_end: string | null;
  can_edit: boolean;
}

export interface ReminderSettingsUpdateInput {
  hw_reminder_enabled?: boolean;
  hw_reminder_time?: string;
  schedule_reminder_enabled?: boolean;
  schedule_reminder_time?: string;
  hw_duetoday_enabled?: boolean;
  hw_duetoday_time?: string;
  changes_reminder_enabled?: boolean;
  extra_reminder_enabled?: boolean;
  quiet_start?: string;
  quiet_end?: string;
  clear_quiet_hours?: boolean;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  actor_name: string;
  entity_type: string;
  entity_id: number | null;
  action: string;
  summary: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface Today {
  date: string;
  timezone: string;
  weekday: number;
  week_type: string;
  day_type: string | null;
  day_note: string | null;
  lessons: Lesson[];
  extra: ExtraActivity[];
  homework_today: Homework[];
  overdue: Homework[];
  upcoming: Homework[];
  permissions: Permissions;
}

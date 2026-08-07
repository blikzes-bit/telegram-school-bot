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
  role: string;
  is_owner: boolean;
  is_admin: boolean;
  can_edit_homework: boolean;
  can_edit_schedule: boolean;
  can_add_homework: boolean;
  can_complete_homework: boolean;
  can_edit_extra: boolean;
  can_edit_payments: boolean;
  can_manage_members: boolean;
}

/** A role that can be handed out (ownership is not one of them). */
export type AppRole = "editor" | "student" | "viewer";

export type AccessMode = "telegram" | "roles";

export interface RoleOption {
  name: string;
  label: string;
  description: string;
}

export interface Member {
  user_id: number;
  display_name: string | null;
  role: string;
  role_label: string;
  app_role: AppRole | null;
  is_owner: boolean;
  is_self: boolean;
}

export interface MembersPage {
  members: Member[];
  access_mode: AccessMode;
  access_mode_label: string;
  access_mode_options: RoleOption[];
  assignable_roles: RoleOption[];
  can_manage: boolean;
}

export interface Invite {
  id: number;
  app_role: string;
  role_label: string;
  created_at: string;
  expires_at: string;
  created_by_name: string | null;
  /** Present only in the response that created the invite — never again. */
  token: string | null;
  url: string | null;
}

export interface InviteAccepted {
  chat_id: number;
  title: string | null;
  app_role: string;
  role_label: string;
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
  /** Separate from can_edit: a student may tick homework off without editing it. */
  can_complete: boolean;
  /** True when the chat gives everybody their own mark; then is_completed is
   *  "I have done it" rather than "this task is closed for the class". */
  per_student: boolean;
  /** How many people are done. null where marks are shared, so 0 can never be
   *  misread as "nobody has done it yet". */
  completed_count: number | null;
}

export interface HomeworkCreateInput {
  subject_name: string;
  due_date: string;
  description: string;
}

/** Partial edit — send only what changed. */
export interface HomeworkUpdateInput {
  subject_name?: string;
  due_date?: string;
  description?: string;
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
  payment_reminder_enabled: boolean;
  payment_reminder_time: string;
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
  payment_reminder_enabled?: boolean;
  payment_reminder_time?: string;
  quiet_start?: string;
  quiet_end?: string;
  clear_quiet_hours?: boolean;
}

export type HomeworkEditPolicy =
  | "collaborative"
  | "creator_or_admin"
  | "admin_only";

export interface TimezoneOption {
  name: string;
  label: string;
}

/** What a chat is for. Decides which sections exist, never who may edit them. */
export type ChatProfile = "personal" | "class" | "tutor";

export interface ProfileOption {
  name: ChatProfile;
  label: string;
  description: string;
}

export interface ProfileFeatures {
  school_schedule: boolean;
  homework: boolean;
  extra_activities: boolean;
  payments: boolean;
  homework_policy: boolean;
}

export interface ClassSettings {
  chat_id: number;
  chat_type: string;
  title: string | null;
  /** Always set: a chat that was never asked resolves to its chat-type default. */
  profile: ChatProfile;
  profile_label: string;
  profile_options: ProfileOption[];
  features: ProfileFeatures;
  timezone: string;
  /** Rendered server-side (e.g. "Europe/Kyiv (UTC+03:00)"). */
  timezone_label: string;
  /** The class's current local time, rendered server-side. */
  local_time: string;
  hw_edit_policy: HomeworkEditPolicy;
  per_student_homework: boolean;
  can_edit: boolean;
  /** The friendly picker, served by the API so it matches the bot's list. */
  timezone_options: TimezoneOption[];
}

export interface ClassSettingsUpdateInput {
  /** A blank string clears the class name; omitting the field leaves it as is. */
  title?: string;
  timezone?: string;
  hw_edit_policy?: HomeworkEditPolicy;
  profile?: ChatProfile;
  per_student_homework?: boolean;
}

export interface LessonSlot {
  lesson_number: number;
  start_time: string;
  end_time: string;
}

export interface ScheduleDayLesson {
  lesson_number: number;
  subject_name: string | null;
}

export interface ScheduleTemplateDay {
  weekday: number;
  lessons: ScheduleDayLesson[];
}

/** The template you *edit* (vs ScheduleRange, the effective schedule you see). */
export interface ScheduleTemplate {
  week_type: string;
  week_mode: boolean;
  week_types: string[];
  slots: LessonSlot[];
  days: ScheduleTemplateDay[];
  can_edit: boolean;
}

export type DayType = "free" | "holiday" | "vacation" | "remote";

export interface DayOverride {
  day_type: DayType | null;
  day_type_label: string | null;
  note: string | null;
}

export interface LessonOverride {
  lesson_number: number;
  action: "cancel" | "set";
  subject_name: string | null;
  start_time: string | null;
  end_time: string | null;
  note: string | null;
}

export interface DateOverrides {
  date: string;
  day: DayOverride | null;
  lessons: LessonOverride[];
  day_type_options: RoleOption[];
  can_edit: boolean;
}

export type PaymentPeriod = "one_time" | "monthly" | "per_lesson";

export type PaymentStatus = "paid" | "due_soon" | "overdue" | "upcoming";

export interface Payment {
  id: number;
  title: string;
  /** Integer minor units (kopecks/cents) — money is never a float. */
  amount_minor: number;
  currency: string;
  /** Already formatted by the server; the client never does money maths. */
  amount_text: string;
  due_date: string;
  period: PaymentPeriod;
  period_label: string;
  is_paid: boolean;
  paid_at: string | null;
  note: string | null;
  remind_days_before: number;
  status: PaymentStatus;
  can_edit: boolean;
}

export interface PaymentCreateInput {
  title: string;
  amount_minor: number;
  due_date: string;
  currency?: string;
  period?: PaymentPeriod;
  note?: string;
  remind_days_before?: number;
}

export type PaymentUpdateInput = Partial<PaymentCreateInput>;

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

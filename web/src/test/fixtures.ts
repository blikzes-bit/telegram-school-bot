import type {
  ClassInfo,
  ClassSettings,
  ExtraActivity,
  Homework,
  Today,
} from "../api/types";

export const classesFixture: ClassInfo[] = [
  { chat_id: -100, title: "5-А класс", role: "admin", timezone: "Europe/Kyiv" },
  { chat_id: -101, title: "6-Б класс", role: "member", timezone: "Europe/Kyiv" },
];

export const homeworkFixture: Homework = {
  id: 1,
  subject_name: "Математика",
  due_date: "2024-01-15",
  description: "Стр. 42, номер 5",
  is_completed: false,
  status: "active",
  can_edit: true,
  can_complete: true,
  per_student: false,
  completed_count: null,
};

export const classSettingsFixture: ClassSettings = {
  chat_id: -100,
  chat_type: "group",
  title: "5-А класс",
  profile: "class",
  profile_label: "🏫 Класс",
  profile_options: [
    {
      name: "personal",
      label: "📖 Личный дневник",
      description: "только для меня: мои уроки, моя домашка, мои напоминания",
    },
    {
      name: "class",
      label: "🏫 Класс",
      description: "школьный класс: расписание и домашка на всех, вносит обычно учитель",
    },
    {
      name: "tutor",
      label: "👩‍🏫 Занятия с репетитором",
      description: "занятия с репетитором: без школьного расписания, только сами занятия",
    },
  ],
  features: {
    school_schedule: true,
    homework: true,
    extra_activities: true,
    payments: false,
    homework_policy: true,
  },
  timezone: "Europe/Kyiv",
  timezone_label: "Europe/Kyiv (UTC+02:00)",
  local_time: "09:30",
  hw_edit_policy: "collaborative",
  per_student_homework: false,
  can_edit: true,
  timezone_options: [
    { name: "Europe/Kyiv", label: "🇺🇦 Киев" },
    { name: "Europe/Warsaw", label: "🇵🇱 Варшава" },
  ],
};

export const extraActivityFixture: ExtraActivity = {
  id: 1,
  title: "Английский",
  kind: "weekly",
  day_of_week: 0,
  activity_date: null,
  start_time: "18:00",
  end_time: "19:00",
  location: null,
  note: null,
  can_edit: true,
};

export const todayFixture: Today = {
  date: "2024-01-15",
  timezone: "Europe/Kyiv",
  weekday: 0,
  week_type: "all",
  day_type: null,
  day_note: null,
  lessons: [
    {
      lesson_number: 1,
      start_time: "08:00",
      end_time: "08:45",
      subject_name: "Математика",
      cancelled: false,
      added: false,
      time_changed: false,
      subject_changed: false,
      note: null,
    },
  ],
  extra: [],
  homework_today: [homeworkFixture],
  overdue: [],
  upcoming: [],
  permissions: {
    role: "admin",
    is_owner: false,
    is_admin: true,
    can_edit_homework: true,
    can_edit_schedule: true,
    can_add_homework: true,
    can_complete_homework: true,
    can_edit_extra: true,
    can_edit_payments: true,
    can_manage_members: true,
  },
};

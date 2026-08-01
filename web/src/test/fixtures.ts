import type { ClassInfo, ExtraActivity, Homework, Today } from "../api/types";

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
    is_admin: true,
    can_edit_homework: true,
    can_edit_schedule: true,
  },
};

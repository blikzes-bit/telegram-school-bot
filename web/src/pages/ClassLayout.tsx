import { NavLink, Outlet, useParams } from "react-router-dom";

import { useClassSettings } from "../api/hooks";
import type { ProfileFeatures } from "../api/types";

type Tab = {
  to: string;
  label: string;
  icon: string;
  /** Shown only when this feature is on for the chat's profile. */
  needs?: keyof ProfileFeatures;
};

const TABS: Tab[] = [
  { to: "today", label: "Сегодня", icon: "📚" },
  { to: "schedule", label: "Расписание", icon: "📅", needs: "school_schedule" },
  { to: "homework", label: "Домашка", icon: "📝", needs: "homework" },
  { to: "extra", label: "Занятия", icon: "🎯", needs: "extra_activities" },
  { to: "payments", label: "Оплата", icon: "💳", needs: "payments" },
  { to: "settings", label: "Настройки", icon: "⚙️" },
];

/** Frame shared by every in-class screen: content area + bottom tab bar.
 *
 * The tab bar follows the chat's profile: a tutor chat has no school timetable,
 * so that tab is not rendered at all rather than shown and then explaining
 * itself away. While the profile is still loading we show the tabs that every
 * profile has, so the bar never jumps around under a finger. */
export function ClassLayout() {
  const { chatId } = useParams();
  const { data } = useClassSettings(Number(chatId));
  const features = data?.features;

  const tabs = TABS.filter((tab) => {
    if (!tab.needs) return true;
    if (!features) return false; // unknown yet — don't show what may vanish
    return features[tab.needs];
  });

  return (
    <div className="class-layout">
      <div className="class-layout__content">
        <Outlet />
      </div>
      <nav className="tabbar" aria-label="Разделы класса">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={`/classes/${chatId}/${tab.to}`}
            className={({ isActive }) =>
              `tabbar__item${isActive ? " tabbar__item--active" : ""}`
            }
          >
            <span className="tabbar__icon" aria-hidden="true">
              {tab.icon}
            </span>
            <span className="tabbar__label">{tab.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

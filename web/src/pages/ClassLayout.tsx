import { NavLink, Outlet, useParams } from "react-router-dom";

const TABS = [
  { to: "today", label: "Сегодня", icon: "📚" },
  { to: "schedule", label: "Расписание", icon: "📅" },
  { to: "homework", label: "Домашка", icon: "📝" },
  { to: "extra", label: "Доп.", icon: "🎯" },
  { to: "settings", label: "Настройки", icon: "⚙️" },
];

/** Frame shared by every in-class screen: content area + bottom tab bar. */
export function ClassLayout() {
  const { chatId } = useParams();

  return (
    <div className="class-layout">
      <div className="class-layout__content">
        <Outlet />
      </div>
      <nav className="tabbar" aria-label="Разделы класса">
        {TABS.map((tab) => (
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

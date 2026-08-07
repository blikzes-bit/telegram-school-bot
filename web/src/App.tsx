import {
  Navigate,
  Route,
  Routes,
} from "react-router-dom";

import { AuthGate } from "./pages/AuthGate";
import { ClassLayout } from "./pages/ClassLayout";
import { ClassPicker } from "./pages/ClassPicker";
import { ExtraActivitiesPage } from "./pages/ExtraActivitiesPage";
import { HomeworkPage } from "./pages/HomeworkPage";
import { MembersPage } from "./pages/MembersPage";
import { PaymentsPage } from "./pages/PaymentsPage";
import { SchedulePage } from "./pages/SchedulePage";
import { SettingsPage } from "./pages/SettingsPage";
import { TodayPage } from "./pages/TodayPage";
import { ForbiddenView } from "./components/StateViews";

export function App() {
  return (
    <Routes>
      <Route element={<AuthGate />}>
        <Route index element={<Navigate to="/classes" replace />} />
        <Route path="classes" element={<ClassPicker />} />
        <Route path="classes/:chatId" element={<ClassLayout />}>
          <Route index element={<Navigate to="today" replace />} />
          <Route path="today" element={<TodayPage />} />
          <Route path="schedule" element={<SchedulePage />} />
          <Route path="homework" element={<HomeworkPage />} />
          <Route path="extra" element={<ExtraActivitiesPage />} />
          <Route path="members" element={<MembersPage />} />
          <Route path="payments" element={<PaymentsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Route>
      <Route path="403" element={<ForbiddenView />} />
      <Route path="*" element={<Navigate to="/classes" replace />} />
    </Routes>
  );
}

/* eslint-disable react-refresh/only-export-components -- context + hook colocated intentionally */
import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import type { Me } from "../api/types";

export const AuthContext = createContext<Me | null>(null);

/** The authenticated web user, available to any descendant of AuthGate. */
export function useAuth(): Me {
  const me = useContext(AuthContext);
  if (!me) {
    throw new Error("useAuth must be used within an authenticated route");
  }
  return me;
}

/** Provide an authenticated user (used by AuthGate and by tests). */
export function AuthProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <AuthContext.Provider value={me}>{children}</AuthContext.Provider>;
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { api, type Session } from "./api";

type SessionContextValue = {
  session: Session | null;
  loading: boolean;
  setSession: (data: Session | null) => void;
  logout: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      // El servidor renueva el acceso vencido si el token de refresco sigue vigente.
      return await api<Session | null>("/api/auth/session");
    },
    staleTime: 60_000,
  });
  const logoutMutation = useMutation({
    mutationFn: () => api<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  });

  const value: SessionContextValue = {
    session: query.data ?? null,
    loading: query.isLoading,
    setSession: (data) => {
      // Datos del espacio anterior fuera; la sesión nueva queda en caché sin volver a pedirla.
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
      client.setQueryData(["session"], data);
    },
    logout: async () => {
      await logoutMutation.mutateAsync().catch(() => undefined);
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
      client.setQueryData(["session"], null);
    },
  };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession fuera de SessionProvider");
  return value;
}

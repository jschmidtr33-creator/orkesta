import "@orkesta/ui/theme.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { ApiError } from "./lib/api";
import { SessionProvider } from "./lib/session";
import { InvitationPage, LoginPage, RegisterPage, WelcomePage } from "./pages/auth";
import { DashboardPage } from "./pages/dashboard";
import { ModulePage } from "./pages/modules";
import { SettingsPage } from "./pages/settings";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter basename="/app">
        <SessionProvider>
          <Routes>
            <Route path="/ingresar" element={<LoginPage />} />
            <Route path="/registro" element={<RegisterPage />} />
            <Route path="/bienvenida" element={<WelcomePage />} />
            <Route path="/invitacion/:token" element={<InvitationPage />} />
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="/prospeccion" element={<ModulePage slug="prospeccion" />} />
              <Route path="/contactos" element={<ModulePage slug="contactos" />} />
              <Route path="/campanas" element={<ModulePage slug="campanas" />} />
              <Route path="/automatizacion" element={<ModulePage slug="automatizacion" />} />
              <Route path="/crm" element={<ModulePage slug="crm" />} />
              <Route path="/reportes" element={<ModulePage slug="reportes" />} />
              <Route path="/configuracion/*" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </SessionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);

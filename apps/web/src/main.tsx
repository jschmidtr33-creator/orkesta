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
import { ContactsPage, ListsPage } from "./pages/contacts";
import { CrmPage } from "./pages/crm";
import { ProposalEditor, ProposalsPage } from "./pages/proposals";
import { PublicProposalPage } from "./pages/publicProposal";
import { ImportPage } from "./pages/importer";
import { ModulePage } from "./pages/modules";
import { ProspectingPage } from "./pages/prospecting";
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
            <Route path="/p/:publicId" element={<PublicProposalPage />} />
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="/prospeccion" element={<ProspectingPage />} />
              <Route path="/contactos" element={<ContactsPage />} />
              <Route path="/contactos/importar" element={<ImportPage />} />
              <Route path="/contactos/listas" element={<ListsPage />} />
              <Route path="/campanas" element={<ModulePage slug="campanas" />} />
              <Route path="/automatizacion" element={<ModulePage slug="automatizacion" />} />
              <Route path="/crm" element={<CrmPage />} />
              <Route path="/propuestas" element={<ProposalsPage />} />
              <Route path="/propuestas/:id" element={<ProposalEditor />} />
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

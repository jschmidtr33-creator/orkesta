import "@orkesta/ui/theme.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { ApiError } from "./lib/api";
import { applyTheme } from "./lib/theme";
import { SessionProvider } from "./lib/session";
import { InvitationPage, LoginPage, RegisterPage, WelcomePage } from "./pages/auth";
import { DashboardPage } from "./pages/dashboard";

/** Cada pantalla se descarga solo cuando se abre: la app inicial pesa mucho menos. */
function page<K extends string>(load: () => Promise<Record<K, unknown>>, name: K) {
  const Lazy = lazy(() => load().then((m) => ({ default: m[name] as ComponentType })));
  return function Page() {
    return (
      <Suspense fallback={<Loading />}>
        <Lazy />
      </Suspense>
    );
  };
}

function Loading(): ReactNode {
  return (
    <div className="flex min-h-[40vh] items-center justify-center" role="status" aria-label="Cargando">
      <span className="size-6 animate-spin rounded-full border-2 border-ork-blue/30 border-t-ork-blue" />
    </div>
  );
}

const AdminApp = page(() => import("./pages/admin"), "AdminApp");
const AutomationPage = page(() => import("./pages/automation"), "AutomationPage");
const CampaignDetail = page(() => import("./pages/campaigns"), "CampaignDetail");
const CampaignsPage = page(() => import("./pages/campaigns"), "CampaignsPage");
const CrmPage = page(() => import("./pages/crm"), "CrmPage");
const ProposalEditor = page(() => import("./pages/proposals"), "ProposalEditor");
const ProposalsPage = page(() => import("./pages/proposals"), "ProposalsPage");
const PublicProposalPage = page(() => import("./pages/publicProposal"), "PublicProposalPage");
const ImportPage = page(() => import("./pages/importer"), "ImportPage");
const ProspectingPage = page(() => import("./pages/prospecting"), "ProspectingPage");
const ReportsPage = page(() => import("./pages/reports"), "ReportsPage");
const TeamMemberPage = page(() => import("./pages/team"), "TeamMemberPage");
const TeamPage = page(() => import("./pages/team"), "TeamPage");
const SettingsPage = page(() => import("./pages/settings"), "SettingsPage");
const ContactsPage = page(() => import("./pages/contacts"), "ContactsPage");
const ListsPage = page(() => import("./pages/contacts"), "ListsPage");
const ProductPage = page(() => import("./pages/product"), "ProductPage");

const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

applyTheme();

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
            <Route path="/admin/*" element={<AdminApp />} />
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="/prospeccion" element={<ProspectingPage />} />
              <Route path="/contactos" element={<ContactsPage />} />
              <Route path="/contactos/importar" element={<ImportPage />} />
              <Route path="/contactos/listas" element={<ListsPage />} />
              <Route path="/campanas" element={<CampaignsPage />} />
              <Route path="/campanas/:id" element={<CampaignDetail />} />
              <Route path="/automatizacion" element={<AutomationPage />} />
              <Route path="/crm" element={<CrmPage />} />
              <Route path="/propuestas" element={<ProposalsPage />} />
              <Route path="/propuestas/:id" element={<ProposalEditor />} />
              <Route path="/reportes" element={<ReportsPage />} />
              <Route path="/producto" element={<ProductPage />} />
              <Route path="/equipo" element={<TeamPage />} />
              <Route path="/equipo/:userId" element={<TeamMemberPage />} />
              <Route path="/configuracion/*" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </SessionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);

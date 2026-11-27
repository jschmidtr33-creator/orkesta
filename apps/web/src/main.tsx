import { ConnectExtensionPage } from "./pages/connectExtension";
import "@orkesta/ui/theme.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Component,
  lazy,
  StrictMode,
  Suspense,
  type ComponentType,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { ApiError } from "./lib/api";
import { applyTheme } from "./lib/theme";
import { SessionProvider } from "./lib/session";
import {
  ForgotPasswordPage,
  InvitationPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  WelcomePage,
} from "./pages/auth";
import { AutoCampaignPage } from "./pages/autoCampaign";
import { DashboardPage } from "./pages/dashboard";

/** Cada pantalla se descarga solo cuando se abre: la app inicial pesa mucho menos. */
function page<K extends string>(
  load: () => Promise<Record<K, unknown>>,
  name: K,
) {
  const Lazy = lazy(() =>
    load().then((m) => ({ default: m[name] as ComponentType })),
  );
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
    <div
      className="flex min-h-[40vh] items-center justify-center"
      role="status"
      aria-label="Cargando"
    >
      <span className="size-6 animate-spin rounded-full border-2 border-ork-blue/30 border-t-ork-blue" />
    </div>
  );
}

const AdminApp = page(() => import("./pages/admin"), "AdminApp");
const AutomationPage = page(
  () => import("./pages/automation"),
  "AutomationPage",
);
const CampaignDetail = page(
  () => import("./pages/campaigns"),
  "CampaignDetail",
);
const CampaignsPage = page(() => import("./pages/campaigns"), "CampaignsPage");
const CrmPage = page(() => import("./pages/crm"), "CrmPage");
const ProposalEditor = page(
  () => import("./pages/proposals"),
  "ProposalEditor",
);
const ProposalsPage = page(() => import("./pages/proposals"), "ProposalsPage");
const PublicProposalPage = page(
  () => import("./pages/publicProposal"),
  "PublicProposalPage",
);
const ImportPage = page(() => import("./pages/importer"), "ImportPage");
const ProspectingPage = page(
  () => import("./pages/prospecting"),
  "ProspectingPage",
);
const ReportsPage = page(() => import("./pages/reports"), "ReportsPage");
const TeamMemberPage = page(() => import("./pages/team"), "TeamMemberPage");
const TeamPage = page(() => import("./pages/team"), "TeamPage");
const SettingsPage = page(() => import("./pages/settings"), "SettingsPage");
const ContactsPage = page(() => import("./pages/contacts"), "ContactsPage");
const ListsPage = page(() => import("./pages/contacts"), "ListsPage");
const CalendarPage = page(() => import("./pages/calendar"), "CalendarPage");

const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) =>
        !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

applyTheme();

/** Después de publicar una versión nueva, una pestaña vieja puede pedir archivos que ya no existen:
 *  se recarga una vez (sin borrar caché) para traer la versión nueva. */
const RELOADED = "orkesta-reloaded";
function reloadOnce(): boolean {
  try {
    if (sessionStorage.getItem(RELOADED)) return false;
    sessionStorage.setItem(RELOADED, String(Date.now()));
  } catch {
    // sin almacenamiento: se recarga igual
  }
  window.location.reload();
  return true;
}
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  reloadOnce();
});
window.setTimeout(() => {
  try {
    sessionStorage.removeItem(RELOADED); // la app cargó bien: se permite otra recarga a futuro
  } catch {
    // nada
  }
}, 10_000);

/** Si algo falla al dibujar, nunca una pantalla en blanco: un aviso con un botón para seguir. */
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ORKESTA", error, info.componentStack);
    if (/dynamically imported module|Importing a module script|ChunkLoadError|Loading chunk/i.test(String(error?.message))) {
      reloadOnce();
    }
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-ork-surface p-6 text-center">
        <img src="/app/logo-mark.svg" alt="" className="size-12" />
        <h1 className="text-[20px] font-bold text-ork-ink">Algo no cargó bien</h1>
        <p className="max-w-sm text-[14px] text-ork-mist">
          Puede pasar justo después de una actualización de ORKESTA. Recarga y sigue donde ibas.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-xl bg-ork-blue px-5 py-2.5 text-[14px] font-semibold text-white"
        >
          Recargar
        </button>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
    <QueryClientProvider client={client}>
      <BrowserRouter basename="/app">
        <SessionProvider>
          <Routes>
            <Route path="/ingresar" element={<LoginPage />} />
            <Route path="/registro" element={<RegisterPage />} />
            <Route path="/recuperar" element={<ForgotPasswordPage />} />
            <Route path="/restablecer/:token" element={<ResetPasswordPage />} />
            <Route path="/bienvenida" element={<WelcomePage />} />
            <Route path="/invitacion/:token" element={<InvitationPage />} />
            <Route path="/p/:publicId" element={<PublicProposalPage />} />
            <Route path="/conectar-extension" element={<ConnectExtensionPage />} />
            <Route path="/admin/*" element={<AdminApp />} />
            <Route element={<AppLayout />}>
              <Route index element={<DashboardPage />} />
              <Route path="/prospeccion" element={<ProspectingPage />} />
              <Route path="/contactos" element={<ContactsPage />} />
              <Route path="/contactos/importar" element={<ImportPage />} />
              <Route path="/contactos/listas" element={<ListsPage />} />
              <Route path="/campanas" element={<CampaignsPage />} />
              <Route path="/campanas/automatizada" element={<AutoCampaignPage />} />
              <Route path="/campanas/:id" element={<CampaignDetail />} />
              <Route path="/automatizacion" element={<AutomationPage />} />
              <Route path="/crm" element={<CrmPage />} />
              <Route path="/propuestas" element={<ProposalsPage />} />
              <Route path="/propuestas/:id" element={<ProposalEditor />} />
              <Route path="/reportes" element={<ReportsPage />} />
              <Route path="/producto" element={<Navigate to="/" replace />} />
              <Route path="/calendario" element={<CalendarPage />} />
              <Route path="/equipo" element={<TeamPage />} />
              <Route path="/equipo/:userId" element={<TeamMemberPage />} />
              <Route path="/configuracion/*" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </SessionProvider>
      </BrowserRouter>
    </QueryClientProvider>
    </Boundary>
  </StrictMode>,
);

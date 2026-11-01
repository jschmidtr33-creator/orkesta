import * as Menu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Check,
  ChevronsUpDown,
  FileText,
  Handshake,
  Home,
  LogOut,
  Mail,
  Menu as MenuIcon,
  Plus,
  Radar,
  Settings,
  Users,
  Workflow,
  X,
} from "lucide-react";
import { useRef, useState, type ComponentType } from "react";
import { NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { api, type Session } from "../lib/api";
import { useSession } from "../lib/session";
import { Avatar, cx, Spinner, Wordmark } from "./ui";

type NavItem = { to: string; label: string; icon: ComponentType<{ className?: string }>; end?: boolean };

export const NAV: NavItem[] = [
  { to: "/", label: "Inicio", icon: Home, end: true },
  { to: "/prospeccion", label: "Prospección", icon: Radar },
  { to: "/contactos", label: "Contactos", icon: Users },
  { to: "/campanas", label: "Campañas", icon: Mail },
  { to: "/automatizacion", label: "Automatización", icon: Workflow },
  { to: "/crm", label: "CRM", icon: Handshake },
  { to: "/propuestas", label: "Propuestas", icon: FileText },
  { to: "/reportes", label: "Reportes", icon: BarChart3 },
];

function WorkspaceSwitcher({ session }: { session: Session }) {
  const { setSession } = useSession();
  const navigate = useNavigate();
  const switchTo = useMutation({
    mutationFn: (tenantId: string) => api<Session>("/api/auth/switch", { method: "POST", json: { tenant_id: tenantId } }),
    onSuccess: (data) => {
      setSession(data);
      navigate("/");
    },
  });
  const current = session.workspace;
  return (
    <Menu.Root>
      <Menu.Trigger className="ork-focus flex w-full items-center gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-left hover:bg-white/[0.07]">
        <span className="ork-gradient flex size-8 shrink-0 items-center justify-center rounded-lg text-[13px] font-bold text-white">
          {(current?.name ?? "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-semibold text-white">{current?.name}</span>
          <span className="block text-[12px] text-ork-mist">{current?.role_label}</span>
        </span>
        <ChevronsUpDown className="size-4 text-ork-mist" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="start"
          sideOffset={6}
          className="z-50 min-w-[240px] rounded-xl border border-ork-line bg-white p-1.5 shadow-[var(--shadow-pop)]"
        >
          <Menu.Label className="px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-ork-mist">
            Espacios de trabajo
          </Menu.Label>
          {session.workspaces.map((w) => (
            <Menu.Item
              key={w.id}
              onSelect={() => w.id !== current?.id && switchTo.mutate(w.id)}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13.5px] text-ork-ink outline-none data-[highlighted]:bg-ork-surface"
            >
              <span className="flex-1 truncate">{w.name}</span>
              {w.id === current?.id && <Check className="size-4 text-ork-blue" aria-hidden />}
            </Menu.Item>
          ))}
          <Menu.Separator className="my-1 h-px bg-ork-line" />
          <Menu.Item
            onSelect={() => navigate("/bienvenida")}
            className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13.5px] text-ork-graphite outline-none data-[highlighted]:bg-ork-surface"
          >
            <Plus className="size-4" aria-hidden /> Crear otro espacio
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function Sidebar({ session, onNavigate }: { session: Session; onNavigate?: () => void }) {
  const { logout } = useSession();
  const navigate = useNavigate();
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cx(
      "ork-focus flex items-center gap-3 rounded-xl px-3 py-2.5 text-[14px] font-medium transition",
      isActive ? "bg-ork-blue/20 text-white shadow-[inset_0_0_0_1px_rgb(37_99_235/0.35)]" : "text-ork-mist hover:bg-white/[0.05] hover:text-white",
    );
  return (
    <div className="flex h-full flex-col gap-6 px-4 py-5">
      <div className="px-2">
        <Wordmark dark size={28} />
      </div>
      <WorkspaceSwitcher session={session} />
      <nav aria-label="Módulos" className="flex flex-1 flex-col gap-1">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={linkClass} onClick={onNavigate}>
            <Icon className="size-[18px]" aria-hidden />
            {label}
          </NavLink>
        ))}
        <div className="my-3 h-px bg-white/10" />
        <NavLink to="/configuracion" className={linkClass} onClick={onNavigate}>
          <Settings className="size-[18px]" aria-hidden />
          Configuración
        </NavLink>
      </nav>
      <div className="flex items-center gap-3 rounded-xl px-2 py-2">
        <Avatar name={session.user.full_name} email={session.user.email} size={34} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-semibold text-white">{session.user.full_name || session.user.email}</p>
          <p className="truncate text-[12px] text-ork-mist">{session.user.email}</p>
        </div>
        <button
          type="button"
          className="ork-focus rounded-lg p-2 text-ork-mist hover:bg-white/[0.06] hover:text-white"
          aria-label="Cerrar sesión"
          title="Cerrar sesión"
          onClick={async () => {
            await logout();
            navigate("/ingresar", { replace: true });
          }}
        >
          <LogOut className="size-[18px]" />
        </button>
      </div>
    </div>
  );
}

export function AppLayout() {
  const { session, loading } = useSession();
  const [open, setOpen] = useState(false);
  if (loading) return <Spinner label="Abriendo ORKESTA" />;
  if (!session) return <Navigate to="/ingresar" replace />;
  if (!session.workspace) return <Navigate to="/bienvenida" replace />;
  return (
    <div className="min-h-dvh lg:pl-[272px]">
      <aside className="fixed inset-y-0 left-0 hidden w-[272px] bg-ork-ink lg:block">
        <Sidebar session={session} />
      </aside>
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-ork-line bg-white/90 px-4 backdrop-blur lg:hidden">
        <Wordmark size={24} />
        <button type="button" className="ork-focus rounded-lg p-2" aria-label="Abrir menú" onClick={() => setOpen(true)}>
          <MenuIcon className="size-5" />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menú">
          <div className="absolute inset-0 bg-ork-ink/60" onClick={() => setOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-[280px] bg-ork-ink">
            <button
              type="button"
              className="ork-focus absolute right-3 top-4 rounded-lg p-2 text-ork-mist"
              aria-label="Cerrar menú"
              onClick={() => setOpen(false)}
            >
              <X className="size-5" />
            </button>
            <Sidebar session={session} onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
      <main className="mx-auto max-w-[1240px] px-4 py-6 sm:px-8 sm:py-8">
        <NewVersionBanner />
        <Outlet />
      </main>
    </div>
  );
}


/** Avisa cuando se desplegó una versión nueva mientras la pestaña seguía abierta. */
function NewVersionBanner() {
  const first = useRef<string | null>(null);
  const health = useQuery({
    queryKey: ["app-version"],
    queryFn: () => api<{ version?: string }>("/api/health"),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const version = health.data?.version;
  if (version && first.current === null) first.current = version;
  if (!version || version === first.current) return null;
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ork-blue/30 bg-ork-blue/5 px-4 py-3 text-[13.5px] text-ork-ink" role="status">
      <span>
        <strong>Hay una versión nueva de ORKESTA.</strong> Recarga para usar las últimas mejoras.
      </span>
      <button type="button" onClick={() => window.location.reload()} className="ork-gradient ork-focus rounded-lg px-3 py-1.5 text-[13px] font-semibold text-white">
        Recargar
      </button>
    </div>
  );
}

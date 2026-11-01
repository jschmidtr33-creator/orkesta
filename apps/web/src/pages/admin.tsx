import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Building2,
  CheckCircle2,
  Inbox,
  LayoutDashboard,
  Lock,
  Menu as MenuIcon,
  Plug,
  ScrollText,
  Settings2,
  ShieldCheck,
  UserCog,
  Users,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useState, type ComponentType, type ReactNode } from "react";
import { Link, Navigate, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { Alert, Avatar, Badge, Button, Card, CardHeader, cx, Logo, SelectField, Spinner, TextField } from "../components/ui";
import { ADMIN_LOCKED_EVENT, api, ApiError, hasAdminToken, setAdminToken } from "../lib/api";
import { useSession } from "../lib/session";
import { formatWhen } from "./dashboard";

type Me = { user: { id: string; name: string; email: string }; role: string; role_label: string; permissions: string[] };

function useMe(enabled: boolean) {
  return useQuery({ queryKey: ["admin-me"], queryFn: () => api<Me>("/api/admin/me"), retry: false, enabled });
}
const can = (me: Me | undefined, p: string) => Boolean(me?.permissions.includes(p));
const n = (v: number) => new Intl.NumberFormat("es").format(v);

type Item = { to: string; label: string; icon: ComponentType<{ className?: string }>; perm: string; end?: boolean };
const NAV: Item[] = [
  { to: "/admin", label: "Resumen", icon: LayoutDashboard, perm: "ver_resumen", end: true },
  { to: "/admin/empresas", label: "Empresas", icon: Building2, perm: "ver_empresas" },
  { to: "/admin/usuarios", label: "Usuarios", icon: Users, perm: "ver_usuarios" },
  { to: "/admin/demos", label: "Solicitudes de demo", icon: Inbox, perm: "ver_demos" },
  { to: "/admin/configuracion", label: "Configuración", icon: Settings2, perm: "configuracion" },
  { to: "/admin/integraciones", label: "Integraciones", icon: Plug, perm: "integraciones" },
  { to: "/admin/equipo", label: "Equipo y permisos", icon: UserCog, perm: "" },
  { to: "/admin/bitacora", label: "Bitácora", icon: ScrollText, perm: "bitacora" },
];

/** Panel de administración de ORKESTA: aparte de la app de los clientes. Siempre pide las
 * credenciales antes de entrar, aunque ya haya una sesión abierta en ORKESTA. */
export function AdminApp() {
  const client = useQueryClient();
  const { session, loading } = useSession();
  const [unlocked, setUnlocked] = useState(hasAdminToken());
  const [expired, setExpired] = useState(false);
  const access = useQuery({
    queryKey: ["admin-access"],
    queryFn: () => api<{ email: string; name: string }>("/api/admin/access"),
    retry: false,
    enabled: Boolean(session),
  });
  const me = useMe(unlocked);
  useEffect(() => {
    const lock = () => {
      setUnlocked(false);
      setExpired(true);
      client.removeQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("admin-") && q.queryKey[0] !== "admin-access" });
    };
    window.addEventListener(ADMIN_LOCKED_EVENT, lock);
    return () => {
      window.removeEventListener(ADMIN_LOCKED_EVENT, lock);
      // Al salir del panel se olvida el pase: volver a entrar pide credenciales otra vez.
      setAdminToken(null);
      client.removeQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("admin-") });
    };
  }, [client]);
  if (loading || (session && access.isLoading)) return <Spinner label="Abriendo el panel" />;
  if (!session) return <Navigate to="/ingresar?siguiente=/admin" replace />;
  if (access.error) return <NoAccess forbidden={access.error instanceof ApiError && access.error.status === 403} />;
  if (!unlocked)
    return (
      <LockScreen
        expired={expired}
        onUnlock={(token) => {
          setAdminToken(token);
          setExpired(false);
          setUnlocked(true);
        }}
      />
    );
  if (me.isLoading) return <Spinner label="Abriendo el panel" />;
  if (me.error) return <NoAccess forbidden={me.error instanceof ApiError && me.error.status === 403} />;
  const lock = () => {
    setAdminToken(null);
    setExpired(false);
    setUnlocked(false);
    client.removeQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("admin-") && q.queryKey[0] !== "admin-access" });
  };
  return (
    <Routes>
      <Route element={<AdminLayout me={me.data!} onLock={lock} />}>
        <Route index element={<Guard me={me.data} perm="ver_resumen"><Overview /></Guard>} />
        <Route path="empresas" element={<Guard me={me.data} perm="ver_empresas"><Tenants me={me.data!} /></Guard>} />
        <Route path="usuarios" element={<Guard me={me.data} perm="ver_usuarios"><UsersPage me={me.data!} /></Guard>} />
        <Route path="demos" element={<Guard me={me.data} perm="ver_demos"><Demos /></Guard>} />
        <Route path="configuracion" element={<Guard me={me.data} perm="configuracion"><SettingsPage /></Guard>} />
        <Route path="integraciones" element={<Guard me={me.data} perm="integraciones"><Integrations /></Guard>} />
        <Route path="equipo" element={<Team />} />
        <Route path="bitacora" element={<Guard me={me.data} perm="bitacora"><Audit /></Guard>} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Route>
    </Routes>
  );
}

function LockScreen({ expired, onUnlock }: { expired: boolean; onUnlock: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const unlock = useMutation({
    mutationFn: () => api<{ token: string }>("/api/admin/unlock", { method: "POST", json: { email, password } }),
    onSuccess: (r) => {
      setPassword("");
      onUnlock(r.token);
    },
  });
  useEffect(() => {
    document.title = "Administración · ORKESTA";
  }, []);
  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-[#05070D] px-4 py-10">
      <div className="pointer-events-none absolute -left-32 -top-32 size-96 rounded-full bg-[#2563EB]/25 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -right-24 size-96 rounded-full bg-[#8B5CF6]/20 blur-3xl" />
      <form
        className="relative w-full max-w-[400px] rounded-3xl border border-white/10 bg-white/[0.04] p-7 text-white shadow-2xl backdrop-blur"
        onSubmit={(e) => {
          e.preventDefault();
          unlock.mutate();
        }}
      >
        <div className="flex items-center gap-2.5">
          <Logo size={34} />
          <span className="text-[18px] font-medium tracking-[0.2em]" style={{ fontFamily: "'Avenir Next', Avenir, Montserrat, 'Segoe UI', sans-serif" }}>
            ORKESTΛ
          </span>
        </div>
        <span className="mt-5 inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[#A5B4FC]">
          <Lock className="size-3.5" /> Acceso restringido
        </span>
        <h1 className="mt-3 text-[22px] font-bold">Panel de administración</h1>
        <p className="mt-1 text-[13.5px] text-white/60">
          {expired ? "Tu acceso al panel expiró. Confirma tus credenciales para continuar." : "Por seguridad, confirma tus credenciales de administración antes de entrar."}
        </p>
        <div className="mt-5 flex flex-col gap-3">
          <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-white/80">
            Correo
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="ork-focus h-11 rounded-xl border border-white/15 bg-white/[0.06] px-3.5 text-[14px] font-normal text-white placeholder:text-white/35"
              placeholder="tu@correo.com"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-white/80">
            Contraseña
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="ork-focus h-11 rounded-xl border border-white/15 bg-white/[0.06] px-3.5 text-[14px] font-normal text-white"
            />
          </label>
          {unlock.error && <p role="alert" className="rounded-xl bg-[#EF4444]/15 px-3 py-2 text-[13px] text-[#FCA5A5]">{unlock.error.message}</p>}
          <Button type="submit" size="lg" loading={unlock.isPending} icon={<ShieldCheck className="size-4" aria-hidden />}>
            Entrar al panel
          </Button>
        </div>
        <p className="mt-4 text-[12px] text-white/45">El acceso dura 30 minutos y se cierra al salir o recargar el panel.</p>
        <Link to="/" className="mt-4 inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-white/60 hover:text-white">
          <ArrowLeft className="size-3.5" /> Volver a ORKESTA
        </Link>
      </form>
    </div>
  );
}

function NoAccess({ forbidden }: { forbidden: boolean }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-[#05070D] px-4 text-center text-white">
      <div className="max-w-md">
        <Logo size={56} className="mx-auto" />
        <h1 className="mt-6 text-[22px] font-bold">{forbidden ? "No tienes acceso al panel" : "No pudimos abrir el panel"}</h1>
        <p className="mt-2 text-[14px] text-white/60">
          {forbidden ? "El panel de administración de ORKESTA es solo para el equipo autorizado por la creadora." : "Inténtalo de nuevo en un momento."}
        </p>
        <Link to="/" className="mt-6 inline-block rounded-xl bg-white/10 px-4 py-2 text-[14px] font-semibold hover:bg-white/15">
          Volver a ORKESTA
        </Link>
      </div>
    </div>
  );
}

function Guard({ me, perm, children }: { me?: Me; perm: string; children: ReactNode }) {
  if (!can(me, perm)) {
    const first = NAV.find((i) => !i.perm || can(me, i.perm));
    return first && first.to !== "/admin" ? <Navigate to={first.to} replace /> : <Alert>Tu rol no permite ver esta sección.</Alert>;
  }
  return <>{children}</>;
}

function AdminLayout({ me, onLock }: { me: Me; onLock: () => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    document.title = "Administración · ORKESTA";
  }, []);
  const items = NAV.filter((i) => !i.perm || can(me, i.perm));
  const nav = (
    <div className="flex h-full flex-col gap-6 px-4 py-6">
      <div className="px-2">
        <div className="flex items-center gap-2.5">
          <Logo size={30} />
          <span className="text-[17px] font-medium tracking-[0.2em] text-white" style={{ fontFamily: "'Avenir Next', Avenir, Montserrat, 'Segoe UI', sans-serif" }}>
            ORKESTΛ
          </span>
        </div>
        <span className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[#A5B4FC]">
          <ShieldCheck className="size-3.5" /> Administración
        </span>
      </div>
      <nav className="flex flex-1 flex-col gap-1" aria-label="Panel de administración">
        {items.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={() => setOpen(false)}
            className={({ isActive }) =>
              cx(
                "ork-focus flex items-center gap-3 rounded-xl px-3 py-2.5 text-[14px] font-medium transition",
                isActive ? "bg-gradient-to-r from-[#2563EB]/30 to-[#8B5CF6]/25 text-white shadow-[inset_0_0_0_1px_rgb(139_92_246/0.4)]" : "text-white/60 hover:bg-white/[0.05] hover:text-white",
              )
            }
          >
            <Icon className="size-[18px]" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
        <div className="flex items-center gap-3">
          <Avatar name={me.user.name} email={me.user.email} size={34} />
          <div className="min-w-0">
            <p className="truncate text-[13.5px] font-semibold text-white">{me.user.name}</p>
            <p className="text-[12px] text-[#A5B4FC]">{me.role_label}</p>
          </div>
        </div>
        <button type="button" onClick={onLock} className="mt-3 flex items-center gap-1.5 text-[12.5px] font-semibold text-white/60 hover:text-white">
          <Lock className="size-3.5" /> Bloquear panel
        </button>
        <Link to="/" className="mt-2 flex items-center gap-1.5 text-[12.5px] font-semibold text-white/60 hover:text-white">
          <ArrowLeft className="size-3.5" /> Ir a la app de ORKESTA
        </Link>
      </div>
    </div>
  );
  return (
    <div className="min-h-dvh bg-[#F4F5FA] lg:pl-[264px]">
      <aside className="fixed inset-y-0 left-0 hidden w-[264px] overflow-hidden bg-[#05070D] lg:block">
        <div className="pointer-events-none absolute -left-24 -top-24 size-72 rounded-full bg-[#2563EB]/25 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-28 -right-20 size-72 rounded-full bg-[#8B5CF6]/20 blur-3xl" />
        <div className="relative h-full">{nav}</div>
      </aside>
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between bg-[#05070D] px-4 text-white lg:hidden">
        <span className="flex items-center gap-2">
          <Logo size={24} /> <span className="text-[13px] font-semibold tracking-[0.16em]">ADMIN</span>
        </span>
        <button type="button" aria-label="Abrir menú" onClick={() => setOpen(true)} className="rounded-lg p-2">
          <MenuIcon className="size-5" />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-[280px] bg-[#05070D]">
            <button type="button" aria-label="Cerrar menú" onClick={() => setOpen(false)} className="absolute right-3 top-4 rounded-lg p-2 text-white/60">
              <X className="size-5" />
            </button>
            {nav}
          </aside>
        </div>
      )}
      <div className="relative">
        <div className="h-36 bg-gradient-to-r from-[#0B1430] via-[#1E2A78] to-[#4C1D95]" />
        <main className="mx-auto -mt-24 max-w-[1240px] px-4 pb-10 sm:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Title({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3 text-white">
      <div>
        <h1 className="text-[24px] font-bold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-[14px] text-white/70">{description}</p>}
      </div>
      {action}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card className="py-4">
      <p className="text-[12.5px] font-semibold text-ork-mist">{label}</p>
      <p className="mt-1 text-[26px] font-bold text-ork-ink">{value}</p>
      {hint && <p className="text-[12px] text-ork-mist">{hint}</p>}
    </Card>
  );
}

// --- Resumen --------------------------------------------------------------------------------

type OverviewData = {
  tenants: number;
  tenants_active: number;
  tenants_new_30d: number;
  users: number;
  users_active_7d: number;
  users_new_30d: number;
  totals: { contacts: number; emails_sent: number; deals: number; proposals: number; research_runs: number };
  signups: { date: string; signups: number }[];
  top_tenants: { id: string; name: string; plan: string; contacts: number; emails_sent: number; proposals: number }[];
};

function Overview() {
  const q = useQuery({ queryKey: ["admin-overview"], queryFn: () => api<OverviewData>("/api/admin/overview") });
  const d = q.data;
  const max = Math.max(1, ...(d?.signups.map((s) => s.signups) ?? [1]));
  return (
    <>
      <Title title="Resumen de la plataforma" description="Cómo va ORKESTA en todas las empresas." />
      {!d ? (
        <Card>
          <Spinner />
        </Card>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Empresas" value={n(d.tenants)} hint={`${d.tenants_active} activas · +${d.tenants_new_30d} en 30 días`} />
            <Stat label="Usuarios" value={n(d.users)} hint={`+${d.users_new_30d} en 30 días`} />
            <Stat label="Activos (7 días)" value={n(d.users_active_7d)} hint="Iniciaron sesión" />
            <Stat label="Contactos en la plataforma" value={n(d.totals.contacts)} />
            <Stat label="Correos enviados" value={n(d.totals.emails_sent)} />
            <Stat label="Oportunidades" value={n(d.totals.deals)} />
            <Stat label="Propuestas" value={n(d.totals.proposals)} />
            <Stat label="Investigaciones" value={n(d.totals.research_runs)} />
          </div>
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader title="Registros nuevos" description="Usuarios creados por día, últimos 30 días" />
              <div className="flex h-40 items-end gap-[3px]" role="img" aria-label="Registros por día">
                {d.signups.map((s) => (
                  <div key={s.date} className="group relative flex-1" title={`${s.date}: ${s.signups}`}>
                    <div className="rounded-t-[3px] bg-gradient-to-t from-[#2563EB] to-[#8B5CF6]" style={{ height: `${Math.max(s.signups ? 6 : 2, (s.signups / max) * 150)}px`, opacity: s.signups ? 1 : 0.15 }} />
                  </div>
                ))}
              </div>
            </Card>
            <Card>
              <CardHeader title="Empresas con más contactos" />
              <ul className="divide-y divide-ork-line">
                {d.top_tenants.map((t) => (
                  <li key={t.id} className="flex items-center justify-between gap-3 py-2.5 text-[13.5px]">
                    <span className="min-w-0">
                      <span className="block truncate font-semibold text-ork-ink">{t.name}</span>
                      <span className="text-[12px] text-ork-mist">
                        {n(t.emails_sent)} correos · {n(t.proposals)} propuestas
                      </span>
                    </span>
                    <span className="shrink-0 font-semibold text-ork-graphite">{n(t.contacts)}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}

// --- Empresas -------------------------------------------------------------------------------

type TenantRow = {
  id: string;
  name: string;
  plan: string;
  status: string;
  created_at: string;
  members: number;
  owner: { name: string; email: string } | null;
  contacts: number;
  emails_sent: number;
  deals: number;
  proposals: number;
  research_runs: number;
};

function Tenants({ me }: { me: Me }) {
  const client = useQueryClient();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["admin-tenants", q], queryFn: () => api<{ items: TenantRow[]; plans: Record<string, string> }>(`/api/admin/tenants?q=${encodeURIComponent(q)}`) });
  const patch = useMutation({
    mutationFn: ({ id, ...json }: { id: string; status?: string; plan?: string }) => api(`/api/admin/tenants/${id}`, { method: "PATCH", json }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-tenants"] }),
  });
  const manage = can(me, "gestionar_empresas");
  return (
    <>
      <Title title="Empresas" description="Cada espacio de trabajo en ORKESTA, su plan y su uso." />
      <Card className="overflow-x-auto p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar empresa…" className="ork-focus h-10 w-full max-w-xs rounded-xl border border-ork-line px-3 text-[14px]" />
          {patch.error && <Alert>{patch.error.message}</Alert>}
        </div>
        {list.isLoading ? (
          <Spinner />
        ) : (
          <table className="mt-4 w-full min-w-[980px] text-left text-[13.5px]">
            <thead className="border-y border-ork-line bg-ork-surface/60 text-[11.5px] uppercase tracking-wide text-ork-mist">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Empresa</th>
                <th className="py-2.5 font-semibold">Plan</th>
                <th className="py-2.5 text-right font-semibold">Usuarios</th>
                <th className="py-2.5 text-right font-semibold">Contactos</th>
                <th className="py-2.5 text-right font-semibold">Correos</th>
                <th className="py-2.5 text-right font-semibold">Propuestas</th>
                <th className="py-2.5 pl-4 font-semibold">Estado</th>
                <th className="px-5 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ork-line">
              {(list.data?.items ?? []).map((t) => (
                <tr key={t.id}>
                  <td className="px-5 py-3">
                    <p className="font-semibold text-ork-ink">{t.name}</p>
                    <p className="text-[12px] text-ork-mist">
                      {t.owner ? `${t.owner.name} · ${t.owner.email}` : "Sin administrador"} · desde {formatWhen(t.created_at)}
                    </p>
                  </td>
                  <td className="py-3">
                    {manage ? (
                      <select value={t.plan} onChange={(e) => patch.mutate({ id: t.id, plan: e.target.value })} className="ork-focus h-9 rounded-lg border border-ork-line bg-white px-2 text-[13px]">
                        {Object.entries(list.data?.plans ?? {}).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    ) : (
                      list.data?.plans[t.plan] ?? t.plan
                    )}
                  </td>
                  <td className="py-3 text-right">{t.members}</td>
                  <td className="py-3 text-right">{n(t.contacts)}</td>
                  <td className="py-3 text-right">{n(t.emails_sent)}</td>
                  <td className="py-3 text-right">{n(t.proposals)}</td>
                  <td className="py-3 pl-4">{t.status === "active" ? <Badge tone="ok">Activa</Badge> : <Badge tone="bad">Suspendida</Badge>}</td>
                  <td className="px-5 py-3 text-right">
                    {manage &&
                      (t.status === "active" ? (
                        <Button size="sm" variant="danger" onClick={() => window.confirm(`¿Suspender «${t.name}»? Sus usuarios no podrán entrar.`) && patch.mutate({ id: t.id, status: "suspended" })}>
                          Suspender
                        </Button>
                      ) : (
                        <Button size="sm" variant="secondary" onClick={() => patch.mutate({ id: t.id, status: "active" })}>
                          Reactivar
                        </Button>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

// --- Usuarios -------------------------------------------------------------------------------

type UserRow = { id: string; name: string; email: string; is_active: boolean; platform_role: string | null; created_at: string; last_login_at: string | null; workspaces: string[]; google: boolean };

function UsersPage({ me }: { me: Me }) {
  const client = useQueryClient();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["admin-users", q], queryFn: () => api<{ items: UserRow[] }>(`/api/admin/users?q=${encodeURIComponent(q)}`) });
  const refresh = () => client.invalidateQueries({ queryKey: ["admin-users"] });
  const patch = useMutation({ mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => api(`/api/admin/users/${id}`, { method: "PATCH", json: { is_active } }), onSuccess: refresh });
  const logout = useMutation({ mutationFn: (id: string) => api(`/api/admin/users/${id}/logout`, { method: "POST" }), onSuccess: refresh });
  const manage = can(me, "gestionar_usuarios");
  return (
    <>
      <Title title="Usuarios" description="Todas las personas con cuenta en ORKESTA." />
      <Card className="overflow-x-auto p-0">
        <div className="px-5 pt-5">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por nombre o correo…" className="ork-focus h-10 w-full max-w-xs rounded-xl border border-ork-line px-3 text-[14px]" />
          {(patch.error || logout.error) && (
            <div className="mt-3">
              <Alert>{(patch.error ?? logout.error)?.message}</Alert>
            </div>
          )}
        </div>
        {list.isLoading ? (
          <Spinner />
        ) : (
          <table className="mt-4 w-full min-w-[900px] text-left text-[13.5px]">
            <thead className="border-y border-ork-line bg-ork-surface/60 text-[11.5px] uppercase tracking-wide text-ork-mist">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Persona</th>
                <th className="py-2.5 font-semibold">Espacios de trabajo</th>
                <th className="py-2.5 font-semibold">Último ingreso</th>
                <th className="py-2.5 font-semibold">Estado</th>
                <th className="px-5 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ork-line">
              {(list.data?.items ?? []).map((u) => (
                <tr key={u.id}>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={u.name} email={u.email} size={34} />
                      <div>
                        <p className="font-semibold text-ork-ink">
                          {u.name} {u.platform_role && <Badge tone="violet">Equipo ORKESTA</Badge>}
                        </p>
                        <p className="text-[12px] text-ork-mist">
                          {u.email}
                          {u.google && " · Google"}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="py-3 text-[12.5px] text-ork-graphite">{u.workspaces.join(", ") || "—"}</td>
                  <td className="py-3 text-[12.5px] text-ork-mist">{u.last_login_at ? formatWhen(u.last_login_at) : "Nunca"}</td>
                  <td className="py-3">{u.is_active ? <Badge tone="ok">Activo</Badge> : <Badge tone="bad">Suspendido</Badge>}</td>
                  <td className="px-5 py-3 text-right">
                    {manage && u.platform_role !== "owner" && u.id !== me.user.id && (
                      <div className="flex justify-end gap-1 whitespace-nowrap">
                        <Button size="sm" variant="ghost" onClick={() => logout.mutate(u.id)}>
                          Cerrar sesiones
                        </Button>
                        {u.is_active ? (
                          <Button size="sm" variant="danger" onClick={() => window.confirm(`¿Suspender a ${u.email}?`) && patch.mutate({ id: u.id, is_active: false })}>
                            Suspender
                          </Button>
                        ) : (
                          <Button size="sm" variant="secondary" onClick={() => patch.mutate({ id: u.id, is_active: true })}>
                            Reactivar
                          </Button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

// --- Demos e integraciones ------------------------------------------------------------------

function Demos() {
  const q = useQuery({ queryKey: ["admin-demos"], queryFn: () => api<{ id: string; at: string; name: string; email: string; company: string; job_title: string; country: string; message: string }[]>("/api/admin/demos") });
  return (
    <>
      <Title title="Solicitudes de demo" description="Lo que llega desde el formulario del sitio web." />
      <Card>
        {q.isLoading ? (
          <Spinner />
        ) : !q.data?.length ? (
          <p className="py-8 text-center text-[13.5px] text-ork-mist">Todavía no hay solicitudes.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {q.data.map((d) => (
              <li key={d.id} className="py-3 text-[13.5px]">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-semibold text-ork-ink">
                    {d.name} · {d.company}
                  </p>
                  <span className="text-[12px] text-ork-mist">{formatWhen(d.at)}</span>
                </div>
                <p className="text-ork-graphite">
                  <a href={`mailto:${d.email}`} className="text-ork-blue hover:underline">
                    {d.email}
                  </a>
                  {d.job_title && ` · ${d.job_title}`}
                  {d.country && ` · ${d.country}`}
                </p>
                {d.message && <p className="mt-1 text-ork-mist">«{d.message}»</p>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function Integrations() {
  const q = useQuery({ queryKey: ["admin-integrations"], queryFn: () => api<{ key: string; name: string; ok: boolean; detail: string }[]>("/api/admin/integrations") });
  return (
    <>
      <Title title="Integraciones" description="Estado de las conexiones del servidor (se configuran en Render)." />
      <div className="grid gap-4 md:grid-cols-2">
        {(q.data ?? []).map((i) => (
          <Card key={i.key} className="flex items-start gap-3">
            {i.ok ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-ok" /> : <XCircle className="mt-0.5 size-5 shrink-0 text-bad" />}
            <div>
              <p className="text-[14.5px] font-semibold text-ork-ink">{i.name}</p>
              <p className="text-[13px] text-ork-mist">{i.detail}</p>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}

// --- Configuración --------------------------------------------------------------------------

type AppSettings = {
  signup_open: boolean;
  maintenance: boolean;
  maintenance_message: string;
  announcement: { active: boolean; text: string; tone: "blue" | "ok" | "warn" | "bad" };
  limits: Record<string, { contacts: number; emails_per_day: number; users: number }>;
};

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4 rounded-xl border border-ork-line p-4">
      <span>
        <span className="block text-[14px] font-semibold text-ork-ink">{label}</span>
        <span className="text-[12.5px] text-ork-mist">{hint}</span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx("relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition", checked ? "bg-gradient-to-r from-[#2563EB] to-[#8B5CF6]" : "bg-ork-ink/20")}
      >
        <span className={cx("absolute top-0.5 size-5 rounded-full bg-white shadow transition-all", checked ? "left-[22px]" : "left-0.5")} />
      </button>
    </label>
  );
}

function SettingsPage() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["admin-settings"], queryFn: () => api<{ settings: AppSettings; plans: Record<string, string>; can_edit_plans: boolean }>("/api/admin/settings") });
  const [draft, setDraft] = useState<AppSettings | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (q.data) setDraft(q.data.settings);
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api("/api/admin/settings", { method: "PUT", json: draft }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["admin-settings"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });
  if (!draft || !q.data) return <Spinner />;
  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data.settings);
  return (
    <>
      <Title
        title="Configuración de ORKESTA"
        description="Ajustes globales para todas las empresas."
        action={
          <Button disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
            {saved ? "Guardado ✓" : "Guardar cambios"}
          </Button>
        }
      />
      {save.error && (
        <div className="mb-4">
          <Alert>{save.error.message}</Alert>
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="Acceso" />
          <div className="flex flex-col gap-3">
            <Toggle label="Registro abierto" hint="Si lo apagas, nadie nuevo puede crear cuenta (las invitaciones siguen funcionando)." checked={draft.signup_open} onChange={(v) => setDraft({ ...draft, signup_open: v })} />
            <Toggle label="Modo mantenimiento" hint="Los clientes ven el mensaje y no pueden usar la app. El equipo de ORKESTA sí entra." checked={draft.maintenance} onChange={(v) => setDraft({ ...draft, maintenance: v })} />
            <TextField label="Mensaje de mantenimiento" value={draft.maintenance_message} onChange={(e) => setDraft({ ...draft, maintenance_message: e.target.value })} />
          </div>
        </Card>
        <Card>
          <CardHeader title="Anuncio para todos los usuarios" description="Una franja arriba de la app (novedades, avisos, promociones)." />
          <div className="flex flex-col gap-3">
            <Toggle label="Mostrar anuncio" hint="Aparece en la app de todas las empresas." checked={draft.announcement.active} onChange={(v) => setDraft({ ...draft, announcement: { ...draft.announcement, active: v } })} />
            <TextField label="Texto" value={draft.announcement.text} maxLength={400} onChange={(e) => setDraft({ ...draft, announcement: { ...draft.announcement, text: e.target.value } })} placeholder="Nuevo: envía propuestas por correo desde ORKESTA" />
            <SelectField label="Estilo" value={draft.announcement.tone} onChange={(e) => setDraft({ ...draft, announcement: { ...draft.announcement, tone: e.target.value as AppSettings["announcement"]["tone"] } })}>
              <option value="blue">Información (azul)</option>
              <option value="ok">Novedad (verde)</option>
              <option value="warn">Atención (amarillo)</option>
              <option value="bad">Urgente (rojo)</option>
            </SelectField>
            {draft.announcement.text && <Alert tone={draft.announcement.tone === "ok" ? "ok" : draft.announcement.tone === "bad" ? "bad" : "blue"}>{draft.announcement.text}</Alert>}
          </div>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Planes y límites" description={q.data.can_edit_plans ? "Límites de referencia por plan." : "Tu rol puede ver, pero no cambiar los límites."} />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-[13.5px]">
              <thead className="text-[12px] text-ork-mist">
                <tr>
                  <th className="pb-2 font-semibold">Plan</th>
                  <th className="pb-2 font-semibold">Contactos</th>
                  <th className="pb-2 font-semibold">Correos por día y buzón</th>
                  <th className="pb-2 font-semibold">Usuarios</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(q.data.plans).map(([plan, label]) => (
                  <tr key={plan}>
                    <td className="py-1.5 pr-3 font-semibold text-ork-ink">{label}</td>
                    {(["contacts", "emails_per_day", "users"] as const).map((k) => (
                      <td key={k} className="py-1.5 pr-3">
                        <input
                          type="number"
                          min={0}
                          disabled={!q.data.can_edit_plans}
                          value={draft.limits[plan]?.[k] ?? 0}
                          onChange={(e) => setDraft({ ...draft, limits: { ...draft.limits, [plan]: { ...draft.limits[plan], [k]: Number(e.target.value) } } })}
                          className="ork-focus h-9 w-36 rounded-lg border border-ork-line px-2"
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  );
}

// --- Equipo y permisos ----------------------------------------------------------------------

type TeamData = {
  members: { id: string; name: string; email: string; role: string; last_login_at: string | null }[];
  roles: Record<string, string>;
  permissions: Record<string, string>;
  matrix: Record<string, string[]>;
  can_edit: boolean;
};

function Team() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["admin-team"], queryFn: () => api<TeamData>("/api/admin/team") });
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["admin-team"] });
    client.invalidateQueries({ queryKey: ["admin-me"] });
  };
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("admin");
  const add = useMutation({
    mutationFn: () => api("/api/admin/team", { method: "POST", json: { email, role } }),
    onSuccess: () => {
      setEmail("");
      refresh();
    },
  });
  const change = useMutation({ mutationFn: ({ id, role }: { id: string; role: string | null }) => api(`/api/admin/team/${id}`, { method: "PATCH", json: { role } }), onSuccess: refresh });
  const perms = useMutation({ mutationFn: (matrix: Record<string, string[]>) => api("/api/admin/permissions", { method: "PUT", json: matrix }), onSuccess: refresh });
  const d = q.data;
  if (!d) return <Spinner />;
  const editable = ["admin", "soporte", "lectura"];
  const toggle = (r: string, p: string) => {
    const current = new Set(d.matrix[r] ?? []);
    if (current.has(p)) current.delete(p);
    else current.add(p);
    perms.mutate({ ...d.matrix, [r]: [...current] });
  };
  const error = add.error ?? change.error ?? perms.error;
  return (
    <>
      <Title title="Equipo y permisos" description={d.can_edit ? "Tú decides quién entra al panel y qué puede hacer cada rol." : "Solo la creadora puede cambiar el equipo y los permisos."} />
      {error && (
        <div className="mb-4">
          <Alert>{error.message}</Alert>
        </div>
      )}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Card className="p-0">
          <div className="px-5 pt-5">
            <h2 className="text-[15px] font-bold text-ork-ink">Personas del equipo</h2>
          </div>
          <ul className="mt-3 divide-y divide-ork-line border-t border-ork-line">
            {d.members.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={m.name} email={m.email} size={38} />
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-ork-ink">{m.name}</p>
                    <p className="truncate text-[12.5px] text-ork-mist">
                      {m.email} · {m.last_login_at ? formatWhen(m.last_login_at) : "sin ingresos"}
                    </p>
                  </div>
                </div>
                {m.role === "owner" || !d.can_edit ? (
                  <Badge tone={m.role === "owner" ? "violet" : "blue"}>{d.roles[m.role]}</Badge>
                ) : (
                  <div className="flex items-center gap-1">
                    <select value={m.role} onChange={(e) => change.mutate({ id: m.id, role: e.target.value })} className="ork-focus h-9 rounded-lg border border-ork-line bg-white px-2 text-[13px]">
                      {editable.map((r) => (
                        <option key={r} value={r}>
                          {d.roles[r]}
                        </option>
                      ))}
                    </select>
                    <Button size="sm" variant="ghost" onClick={() => window.confirm(`¿Quitar el acceso al panel a ${m.email}?`) && change.mutate({ id: m.id, role: null })}>
                      Quitar
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {d.can_edit && (
            <form
              className="flex flex-wrap items-end gap-2 border-t border-ork-line px-5 py-4"
              onSubmit={(e) => {
                e.preventDefault();
                add.mutate();
              }}
            >
              <TextField label="Dar acceso a" type="email" placeholder="correo@ejemplo.com" className="min-w-[220px] flex-1" value={email} onChange={(e) => setEmail(e.target.value)} hint="La persona debe tener cuenta en ORKESTA." />
              <SelectField label="Rol" value={role} onChange={(e) => setRole(e.target.value)}>
                {editable.map((r) => (
                  <option key={r} value={r}>
                    {d.roles[r]}
                  </option>
                ))}
              </SelectField>
              <Button type="submit" loading={add.isPending} disabled={!email.includes("@")} className="mb-[22px]">
                Dar acceso
              </Button>
            </form>
          )}
        </Card>
        <Card className="overflow-x-auto p-0">
          <div className="flex items-baseline justify-between px-5 pt-5">
            <h2 className="text-[15px] font-bold text-ork-ink">Qué puede hacer cada rol</h2>
            {d.can_edit && <span className="text-[12px] text-ork-mist">toca para cambiar</span>}
          </div>
          <table className="mt-3 w-full min-w-[520px] text-left text-[13.5px]">
            <thead className="border-y border-ork-line bg-ork-surface/60 text-[11px] uppercase tracking-wide text-ork-mist">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Permiso</th>
                <th className="px-2 py-2.5 text-center font-semibold">Creadora</th>
                {editable.map((r) => (
                  <th key={r} className="px-2 py-2.5 text-center font-semibold">
                    {d.roles[r]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-ork-line">
              {Object.entries(d.permissions).map(([p, label]) => (
                <tr key={p}>
                  <td className="px-5 py-2.5 text-ork-graphite">{label}</td>
                  <td className="py-2.5 text-center text-ok">✓</td>
                  {editable.map((r) => (
                    <td key={r} className="py-2.5 text-center">
                      <input type="checkbox" className="size-4 accent-[#4F46E5]" checked={d.matrix[r]?.includes(p) ?? false} disabled={!d.can_edit || perms.isPending} onChange={() => toggle(r, p)} aria-label={`${d.roles[r]}: ${label}`} />
                    </td>
                  ))}
                </tr>
              ))}
              <tr>
                <td className="px-5 py-2.5 text-ork-graphite">Dar acceso al panel y cambiar permisos</td>
                <td className="py-2.5 text-center text-ok">✓</td>
                {editable.map((r) => (
                  <td key={r} className="py-2.5 text-center text-ork-mist">—</td>
                ))}
              </tr>
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}

// --- Bitácora -------------------------------------------------------------------------------

const ACTIONS: Record<string, string> = {
  "tenant.suspended": "suspendió la empresa",
  "tenant.active": "reactivó la empresa",
  "tenant.plan": "cambió el plan de",
  "user.suspended": "suspendió a",
  "user.active": "reactivó a",
  "user.logout": "cerró las sesiones de",
  "settings.updated": "cambió la configuración",
  "team.added": "dio acceso al panel a",
  "team.role": "cambió el rol de",
  "team.removed": "quitó el acceso al panel a",
  "permissions.updated": "cambió los permisos por rol",
};

function Audit() {
  const q = useQuery({ queryKey: ["admin-audit"], queryFn: () => api<{ id: string; at: string; actor: string; action: string; target: string; detail: Record<string, unknown> }[]>("/api/admin/audit") });
  return (
    <>
      <Title title="Bitácora" description="Cada cambio que hace el equipo en el panel." />
      <Card>
        {!q.data?.length ? (
          <p className="py-8 text-center text-[13.5px] text-ork-mist">Sin movimientos todavía.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {q.data.map((a) => (
              <li key={a.id} className="flex items-start gap-3 py-3 text-[13.5px]">
                <Activity className="mt-0.5 size-4 shrink-0 text-ork-violet" />
                <div className="min-w-0 flex-1">
                  <p className="text-ork-graphite">
                    <strong className="text-ork-ink">{a.actor}</strong> {ACTIONS[a.action] ?? a.action} {a.target && <strong className="text-ork-ink">{a.target}</strong>}
                    {"after" in a.detail && a.detail.after !== undefined && ` → ${String(a.detail.after ?? "sin acceso")}`}
                  </p>
                </div>
                <span className="shrink-0 text-[12px] text-ork-mist">{formatWhen(a.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

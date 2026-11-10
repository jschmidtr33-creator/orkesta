import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Brain,
  Building2,
  CheckCircle2,
  Database,
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
import { ThemeSwitch } from "../components/AppLayout";
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
  { to: "/admin/demos", label: "Demos", icon: Inbox, perm: "ver_demos" },
  { to: "/admin/configuracion", label: "Configuración", icon: Settings2, perm: "configuracion" },
  { to: "/admin/integraciones", label: "Integraciones", icon: Plug, perm: "integraciones" },
  { to: "/admin/directorio", label: "Directorio de empresas", icon: Database, perm: "directorio" },
  { to: "/admin/ia", label: "IA propia · Cerebro", icon: Brain, perm: "ia" },
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
        <Route path="demos" element={<Guard me={me.data} perm="ver_demos"><Demos me={me.data!} /></Guard>} />
        <Route path="configuracion" element={<Guard me={me.data} perm="configuracion"><SettingsPage /></Guard>} />
        <Route path="integraciones" element={<Guard me={me.data} perm="integraciones"><Integrations /></Guard>} />
        <Route path="directorio" element={<Guard me={me.data} perm="directorio"><DirectoryPage /></Guard>} />
        <Route path="ia" element={<Guard me={me.data} perm="ia"><BrainPage /></Guard>} />
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
  const [code, setCode] = useState("");
  const unlock = useMutation({
    mutationFn: () => api<{ token: string }>("/api/admin/unlock", { method: "POST", json: { email, password, code: code.trim() || undefined } }),
    onSuccess: (r) => {
      setPassword("");
      setCode("");
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
          <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-white/80">
            <span>
              Código de verificación <span className="font-normal text-white/45">(si activaste la doble verificación)</span>
            </span>
            <input
              inputMode="text"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="ork-focus h-11 rounded-xl border border-white/15 bg-white/[0.06] px-3.5 text-[14px] font-normal tracking-[0.2em] text-white placeholder:tracking-normal placeholder:text-white/35"
              placeholder="123456"
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
      <ThemeSwitch />
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
    <div className="min-h-dvh bg-ork-surface lg:pl-[264px]">
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
  const remove = useMutation({
    mutationFn: ({ id, confirm }: { id: string; confirm: string }) => api(`/api/admin/tenants/${id}/delete`, { method: "POST", json: { confirm } }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["admin-tenants"] });
      client.invalidateQueries({ queryKey: ["admin-users"] });
      client.invalidateQueries({ queryKey: ["admin-trials"] });
    },
  });
  const manage = can(me, "gestionar_empresas");
  const canMerge = manage && me.role === "owner";
  const [merging, setMerging] = useState<TenantRow | null>(null);
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<DemoStarted | null>(null);
  return (
    <>
      <Title
        title="Empresas"
        description="Cada espacio de trabajo en ORKESTA, su plan y su uso."
        action={
          manage && (
            <Button
              onClick={() => {
                setCreated(null);
                setAdding(true);
              }}
            >
              Agregar empresa
            </Button>
          )
        }
      />
      {adding && (
        <AddCompany
          plans={list.data?.plans ?? {}}
          onClose={() => setAdding(false)}
          onDone={(r) => {
            setAdding(false);
            setCreated(r);
            client.invalidateQueries({ queryKey: ["admin-tenants"] });
            client.invalidateQueries({ queryKey: ["admin-trials"] });
          }}
        />
      )}
      {created && <DemoReady demo={created} title={`Empresa lista: ${created.name}`} onClose={() => setCreated(null)} />}
      {merging && <MergeDialog source={merging} tenants={list.data?.items ?? []} onClose={() => setMerging(null)} />}
      <Card className="overflow-x-auto p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar empresa…" className="ork-focus h-10 w-full max-w-xs rounded-xl border border-ork-line px-3 text-[14px]" />
          {(patch.error || remove.error) && <Alert>{(patch.error ?? remove.error)?.message}</Alert>}
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
                      <select value={t.plan} onChange={(e) => patch.mutate({ id: t.id, plan: e.target.value })} className="ork-focus h-9 rounded-lg border border-ork-line bg-ork-card px-2 text-[13px]">
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
                  <td className="space-x-2 whitespace-nowrap px-5 py-3 text-right">
                    {canMerge && (
                      <Button size="sm" variant="secondary" onClick={() => setMerging(t)}>
                        Unir a otra empresa
                      </Button>
                    )}
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
                    {manage && (
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => {
                          const typed = window.prompt(
                            `Vas a BORRAR «${t.name}» con todos sus datos: contactos, campañas, CRM, propuestas y archivos. Las cuentas que solo están en esta empresa también se borran. No se puede deshacer.\n\nEscribe el nombre de la empresa para confirmar:`,
                          );
                          if (typed) remove.mutate({ id: t.id, confirm: typed });
                        }}
                      >
                        Eliminar
                      </Button>
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

type MergeReport = {
  source: string;
  target: string;
  moved: Record<string, number>;
  personas_movidas?: number;
  contactos_que_ya_existian?: number;
  empresas_cliente_unidas?: number;
  propuestas_renumeradas?: number;
};

function MergeDialog({ source, tenants, onClose }: { source: TenantRow; tenants: TenantRow[]; onClose: () => void }) {
  const client = useQueryClient();
  const [target, setTarget] = useState("");
  const [role, setRole] = useState("vendedor");
  const [confirm, setConfirm] = useState("");
  const merge = useMutation({
    mutationFn: () => api<MergeReport>(`/api/admin/tenants/${source.id}/merge`, { method: "POST", json: { target_id: target, role, confirm } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-tenants"] }),
  });
  const r = merge.data;
  const others = tenants.filter((t) => t.id !== source.id);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Unir empresas">
      <Card className="max-h-[90vh] w-full max-w-lg overflow-y-auto">
        <CardHeader title={`Unir «${source.name}» a otra empresa`} description="Todo su trabajo pasa a la empresa elegida y cada registro conserva su dueño. Después, esta cuenta se cierra." />
        {r ? (
          <div className="flex flex-col gap-3 text-[13.5px]">
            <Alert tone="ok">
              Listo: «{r.source}» ahora es parte de «{r.target}».
            </Alert>
            <ul className="list-disc pl-5 text-ork-graphite">
              <li>{r.personas_movidas ?? 0} personas movidas</li>
              <li>{n(r.moved.contacts ?? 0)} contactos, {n(r.moved.campaigns ?? 0)} campañas, {n(r.moved.deals ?? 0)} oportunidades y {n(r.moved.proposals ?? 0)} propuestas</li>
              {!!r.contactos_que_ya_existian && <li>{r.contactos_que_ya_existian} contactos ya existían en el destino: cada persona conserva el suyo</li>}
              {!!r.propuestas_renumeradas && <li>{r.propuestas_renumeradas} propuestas siguen ahora la numeración del destino (las firmadas conservan su constancia)</li>}
            </ul>
            <p className="text-ork-mist">Las personas movidas solo deben volver a ingresar.</p>
            <Button onClick={onClose}>Cerrar</Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              merge.mutate();
            }}
          >
            {merge.error && <Alert>{merge.error.message}</Alert>}
            <SelectField label="Empresa de destino" required value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Elige la empresa…</option>
              {others.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} {t.owner ? `· ${t.owner.email}` : ""}
                </option>
              ))}
            </SelectField>
            <SelectField label="Rol de sus personas en el destino" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="vendedor">Vendedor: ve solo lo suyo</option>
              <option value="supervisor">Jefatura: ve a las personas que se le asignen</option>
              <option value="admin">Administrador: ve todo</option>
            </SelectField>
            <p className="-mt-2 text-[12.5px] text-ork-mist">Quien ya es parte del destino conserva su rol allí. Revisa que el plan del destino admita a todas las personas.</p>
            <TextField label={`Para confirmar, escribe «${source.name}»`} required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={onClose}>
                Cancelar
              </Button>
              <Button type="submit" variant="danger" loading={merge.isPending} disabled={!target || !confirm}>
                Unir empresas
              </Button>
            </div>
          </form>
        )}
      </Card>
    </div>
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
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/admin/users/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const manage = can(me, "gestionar_usuarios");
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<DemoStarted | null>(null);
  return (
    <>
      <Title
        title="Usuarios"
        description="Todas las personas con cuenta en ORKESTA."
        action={
          manage && (
            <Button
              onClick={() => {
                setAdded(null);
                setAdding(true);
              }}
            >
              Agregar usuario
            </Button>
          )
        }
      />
      {adding && (
        <AddUser
          onClose={() => setAdding(false)}
          onDone={(r) => {
            setAdding(false);
            setAdded(r);
            refresh();
          }}
        />
      )}
      {added && <DemoReady demo={added} title={`Acceso listo para ${added.name}`} onClose={() => setAdded(null)} />}
      <Card className="overflow-x-auto p-0">
        <div className="px-5 pt-5">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por nombre o correo…" className="ork-focus h-10 w-full max-w-xs rounded-xl border border-ork-line px-3 text-[14px]" />
          {(patch.error || logout.error || remove.error) && (
            <div className="mt-3">
              <Alert>{(patch.error ?? logout.error ?? remove.error)?.message}</Alert>
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
                        {!u.platform_role && (
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() =>
                              window.confirm(
                                `¿Eliminar por completo la cuenta de ${u.email}?\n\nPierde el acceso para siempre, se desconecta su correo y se pausan sus campañas activas. Sus contactos, oportunidades y propuestas quedan en la empresa sin dueño para reasignarlos. No se puede deshacer.`,
                              ) && remove.mutate(u.id)
                            }
                          >
                            Eliminar
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

type DemoRequest = { id: string; at: string; name: string; email: string; company: string; job_title: string; country: string; message: string };
type Trial = { id: string; name: string; created_at: string; trial_ends_at: string | null; days_left: number | null; expired: boolean; status: string; members: number; owner: { name: string; email: string } | null; contacts: number; emails_sent: number; deals: number; proposals: number };
type DemoStarted = { name: string; link: string; trial_ends_at: string | null; message: string };

function Demos({ me }: { me: Me }) {
  const client = useQueryClient();
  const manage = can(me, "gestionar_empresas");
  const requests = useQuery({ queryKey: ["admin-demos"], queryFn: () => api<DemoRequest[]>("/api/admin/demos") });
  const trials = useQuery({ queryKey: ["admin-trials"], queryFn: () => api<Trial[]>("/api/admin/trials"), enabled: can(me, "ver_empresas") });
  const [form, setForm] = useState<{ company: string; email: string; days: number; request_id?: string } | null>(null);
  const [started, setStarted] = useState<DemoStarted | null>(null);
  const start = useMutation({
    mutationFn: (json: { company: string; email: string; days: number; request_id?: string }) => api<DemoStarted>("/api/admin/demos/start", { method: "POST", json }),
    onSuccess: (r) => {
      setStarted(r);
      setForm(null);
      client.invalidateQueries({ queryKey: ["admin-trials"] });
      client.invalidateQueries({ queryKey: ["admin-tenants"] });
    },
  });
  const patch = useMutation({
    mutationFn: ({ id, ...json }: { id: string; extend_days?: number; end_trial?: boolean; plan?: string }) => api(`/api/admin/tenants/${id}`, { method: "PATCH", json }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-trials"] }),
  });
  const open = (r?: DemoRequest) => {
    start.reset();
    setStarted(null);
    setForm(r ? { company: r.company, email: r.email, days: 14, request_id: r.id } : { company: "", email: "", days: 14 });
  };
  return (
    <>
      <Title
        title="Demos"
        description="Demos de 2 semanas para empresas: acceso completo a todas las funciones de ORKESTA. Al vencer, el espacio se pausa y sus datos quedan guardados."
        action={manage && <Button onClick={() => open()}>Crear demo</Button>}
      />
      {form && (
        <Card className="mb-5">
          <CardHeader title="Nueva demo" description="Se crea el espacio de la empresa y se invita a su administración. Te damos el enlace y un mensaje listo para enviar." />
          <form
            className="grid gap-3 sm:grid-cols-[1fr_1fr_140px]"
            onSubmit={(e) => {
              e.preventDefault();
              start.mutate(form);
            }}
          >
            <TextField label="Empresa" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} required minLength={2} />
            <TextField label="Correo de quien la administra" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            <TextField label="Días" type="number" min={1} max={60} value={form.days} onChange={(e) => setForm({ ...form, days: Number(e.target.value) || 14 })} />
            <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
              <Button type="submit" disabled={start.isPending}>{start.isPending ? "Creando…" : "Crear demo"}</Button>
              <Button type="button" variant="secondary" onClick={() => setForm(null)}>Cancelar</Button>
              {start.error && <Alert>{start.error.message}</Alert>}
            </div>
          </form>
        </Card>
      )}
      {started && <DemoReady demo={started} onClose={() => setStarted(null)} />}

      {can(me, "ver_empresas") && (
        <Card className="mb-5 overflow-x-auto p-0">
          <div className="px-5 pt-5">
            <CardHeader title="Demos en curso" description="Días que quedan y lo que cada empresa alcanzó a usar." />
            {patch.error && <Alert>{patch.error.message}</Alert>}
          </div>
          {trials.isLoading ? (
            <Spinner />
          ) : !trials.data?.length ? (
            <p className="px-5 pb-6 text-[13.5px] text-ork-mist">Todavía no hay demos. Crea una arriba o desde una solicitud.</p>
          ) : (
            <table className="w-full min-w-[900px] text-left text-[13.5px]">
              <thead className="border-y border-ork-line bg-ork-surface/60 text-[11.5px] uppercase tracking-wide text-ork-mist">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Empresa</th>
                  <th className="py-2.5 font-semibold">Vence</th>
                  <th className="py-2.5 text-right font-semibold">Usuarios</th>
                  <th className="py-2.5 text-right font-semibold">Contactos</th>
                  <th className="py-2.5 text-right font-semibold">Correos</th>
                  <th className="py-2.5 text-right font-semibold">Propuestas</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-ork-line">
                {trials.data.map((t) => (
                  <tr key={t.id}>
                    <td className="px-5 py-3">
                      <p className="font-semibold text-ork-ink">{t.name}</p>
                      <p className="text-[12px] text-ork-mist">{t.owner ? `${t.owner.name} · ${t.owner.email}` : "Invitación sin aceptar"}</p>
                    </td>
                    <td className="py-3">
                      {t.expired ? (
                        <Badge tone="bad">Vencida</Badge>
                      ) : (
                        <Badge tone={(t.days_left ?? 0) <= 3 ? "warn" : "ok"}>{t.days_left === 1 ? "Queda 1 día" : `Quedan ${t.days_left} días`}</Badge>
                      )}
                      {t.trial_ends_at && <p className="mt-1 text-[12px] text-ork-mist">{new Date(t.trial_ends_at).toLocaleDateString("es", { day: "numeric", month: "short" })}</p>}
                    </td>
                    <td className="py-3 text-right">{t.members}</td>
                    <td className="py-3 text-right">{n(t.contacts)}</td>
                    <td className="py-3 text-right">{n(t.emails_sent)}</td>
                    <td className="py-3 text-right">{n(t.proposals)}</td>
                    <td className="space-x-2 whitespace-nowrap px-5 py-3 text-right">
                      {manage && (
                        <>
                          <Button size="sm" variant="secondary" onClick={() => patch.mutate({ id: t.id, extend_days: 7 })}>
                            +7 días
                          </Button>
                          <select
                            value=""
                            onChange={(e) => e.target.value && window.confirm(`¿Pasar «${t.name}» al plan ${e.target.selectedOptions[0].text}? La demo deja de vencer.`) && patch.mutate({ id: t.id, plan: e.target.value })}
                            className="ork-focus h-8 rounded-lg border border-ork-line bg-ork-card px-2 text-[12.5px]"
                          >
                            <option value="">Convertir a plan…</option>
                            <option value="inicial">Inicial</option>
                            <option value="pro">Pro</option>
                            <option value="empresa">Empresa</option>
                          </select>
                          {!t.expired && (
                            <Button size="sm" variant="danger" onClick={() => window.confirm(`¿Terminar hoy la demo de «${t.name}»? Sus datos quedan guardados.`) && patch.mutate({ id: t.id, end_trial: true })}>
                              Terminar
                            </Button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      <Card>
        <CardHeader title="Solicitudes del sitio web" description="Lo que llega desde el formulario de la landing." />
        {requests.isLoading ? (
          <Spinner />
        ) : !requests.data?.length ? (
          <p className="py-8 text-center text-[13.5px] text-ork-mist">Todavía no hay solicitudes.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {requests.data.map((d) => (
              <li key={d.id} className="flex flex-wrap items-start justify-between gap-3 py-3 text-[13.5px]">
                <div className="min-w-0 flex-1">
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
                </div>
                {manage && (
                  <Button size="sm" variant="secondary" onClick={() => open(d)}>
                    Crear demo
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function AddCompany({ plans, onClose, onDone }: { plans: Record<string, string>; onClose: () => void; onDone: (r: DemoStarted) => void }) {
  const [form, setForm] = useState({ company: "", email: "", plan: "pro" });
  const add = useMutation({
    mutationFn: () => api<DemoStarted>("/api/admin/tenants", { method: "POST", json: form }),
    onSuccess: onDone,
  });
  return (
    <Card className="mb-5">
      <CardHeader title="Agregar empresa" description="Se crea su espacio con el plan elegido y se invita a su administración. Te damos el enlace y un mensaje listo para enviar." />
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_200px]"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <TextField label="Empresa" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} required minLength={2} />
        <TextField label="Correo de quien la administra" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        <label className="text-[13px] font-semibold text-ork-graphite">
          Plan
          <select
            value={form.plan}
            onChange={(e) => setForm({ ...form, plan: e.target.value })}
            className="ork-focus mt-1.5 h-11 w-full rounded-xl border border-ork-line bg-ork-card px-3 text-[14px] font-normal"
          >
            {Object.entries(plans).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
          <Button type="submit" disabled={add.isPending}>{add.isPending ? "Creando…" : "Crear empresa"}</Button>
          <Button type="button" variant="secondary" onClick={onClose}>Cancelar</Button>
          {add.error && <Alert>{add.error.message}</Alert>}
        </div>
      </form>
    </Card>
  );
}

function AddUser({ onClose, onDone }: { onClose: () => void; onDone: (r: DemoStarted) => void }) {
  const tenants = useQuery({ queryKey: ["admin-tenants", ""], queryFn: () => api<{ items: TenantRow[] }>("/api/admin/tenants?q=") });
  const [form, setForm] = useState({ email: "", tenant_id: "", role: "vendedor" });
  const add = useMutation({
    mutationFn: () => api<{ tenant: string; link: string; message: string; email: string }>("/api/admin/users", { method: "POST", json: form }),
    onSuccess: (r) => onDone({ name: `${r.email} (${r.tenant})`, link: r.link, message: r.message, trial_ends_at: null }),
  });
  return (
    <Card className="mb-5">
      <CardHeader title="Agregar usuario" description="Elige la empresa y el rol. Te damos el enlace y un mensaje listo para enviárselo; al entrar crea su contraseña." />
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_160px]"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <TextField label="Correo" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        <label className="text-[13px] font-semibold text-ork-graphite">
          Empresa
          <select
            required
            value={form.tenant_id}
            onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
            className="ork-focus mt-1.5 h-11 w-full rounded-xl border border-ork-line bg-ork-card px-3 text-[14px] font-normal"
          >
            <option value="">Selecciona…</option>
            {(tenants.data?.items ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[13px] font-semibold text-ork-graphite">
          Rol
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}
            className="ork-focus mt-1.5 h-11 w-full rounded-xl border border-ork-line bg-ork-card px-3 text-[14px] font-normal"
          >
            <option value="vendedor">Vendedor</option>
            <option value="supervisor">Jefatura</option>
            <option value="admin">Administrador</option>
          </select>
        </label>
        <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
          <Button type="submit" disabled={add.isPending}>{add.isPending ? "Creando…" : "Crear acceso"}</Button>
          <Button type="button" variant="secondary" onClick={onClose}>Cancelar</Button>
          {add.error && <Alert>{add.error.message}</Alert>}
        </div>
      </form>
    </Card>
  );
}

function DemoReady({ demo, onClose, title }: { demo: DemoStarted; onClose: () => void; title?: string }) {
  const [copied, setCopied] = useState("");
  const copy = (what: string, text: string) => {
    void navigator.clipboard?.writeText(text).then(() => setCopied(what));
  };
  return (
    <Card className="mb-5 border-ok/30">
      <CardHeader title={title ?? `Demo lista para ${demo.name}`} description="Envíale este mensaje (por tu correo o WhatsApp). Al entrar crea su contraseña y ya tiene todo activo." />
      <textarea readOnly value={demo.message} rows={10} className="w-full rounded-xl border border-ork-line bg-ork-surface/50 p-3 text-[13.5px]" />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => copy("mensaje", demo.message)}>Copiar mensaje</Button>
        <Button variant="secondary" onClick={() => copy("enlace", demo.link)}>Copiar solo el enlace</Button>
        <Button variant="secondary" onClick={onClose}>Listo</Button>
        {copied && <span className="text-[13px] text-ok">Copiado el {copied}.</span>}
      </div>
    </Card>
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

// --- Directorio de empresas -----------------------------------------------------------------

type ImportInfo = { status: string; saved: number; rows?: number; error: string; finished_at?: string; started_at?: string };
type DirectoryData = {
  total: number;
  countries: { country: string; companies: number; with_domain: number }[];
  presets: { id: string; label: string; country: string; source: string; last: ImportInfo | null; live: ImportInfo | null }[];
  uploads: { id: string; last: ImportInfo | null; live: ImportInfo | null }[];
  people?: { people: number; with_email: number; verified: number };
  crawler?: { by_status: Record<string, number>; patterns: number; running: boolean; last_run?: string; last_batch?: number };
};
const COUNTRY_OPTIONS: [string, string][] = [
  ["CO", "Colombia"], ["MX", "México"], ["PE", "Perú"], ["CL", "Chile"], ["AR", "Argentina"], ["EC", "Ecuador"],
  ["BR", "Brasil"], ["PA", "Panamá"], ["CR", "Costa Rica"], ["GT", "Guatemala"], ["DO", "República Dominicana"],
  ["UY", "Uruguay"], ["PY", "Paraguay"], ["BO", "Bolivia"], ["SV", "El Salvador"], ["HN", "Honduras"], ["US", "Estados Unidos"], ["ES", "España"],
];

function ImportStatus({ info }: { info: ImportInfo | null | undefined }) {
  if (!info) return <span className="text-[12.5px] text-ork-mist">Sin cargar todavía</span>;
  if (info.status === "running") return <Badge tone="blue">Cargando… {n(info.saved ?? 0)} empresas</Badge>;
  if (info.status === "failed") return <span className="text-[12.5px] text-bad">Falló: {info.error}</span>;
  return (
    <span className="text-[12.5px] text-ork-graphite">
      <Badge tone="ok">{n(info.saved)} empresas</Badge> {info.finished_at && <span className="ml-1 text-ork-mist">· {formatWhen(info.finished_at)}</span>}
      {info.error && <span className="ml-1 text-warn">· {info.error}</span>}
    </span>
  );
}

type TrainResult = {
  people: number;
  files: number;
  domains: number;
  patterns: number;
  companies: number;
  with_domain: number;
  profiles?: number;
  evaluation: { evaluated: number; accuracy: number; baseline: number };
  errors: string[];
};

/** Índice propio de perfiles profesionales públicos, con salida a pedido de la persona. */
function PeopleCard({ stats, onDone }: { stats: NonNullable<DirectoryData["people"]>; onDone: () => void }) {
  const [value, setValue] = useState("");
  const remove = useMutation({
    mutationFn: () => api<{ removed: number }>("/api/admin/people/remove", { method: "POST", json: { value } }),
    onSuccess: () => {
      setValue("");
      onDone();
    },
  });
  return (
    <Card className="mt-4">
      <CardHeader
        title="Índice de personas"
        description="Perfiles profesionales públicos que encuentran las búsquedas (nombre, cargo, empresa, ubicación, LinkedIn y correo corporativo). Las siguientes búsquedas los muestran al instante. No guarda teléfonos ni correos personales."
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Perfiles en el índice" value={n(stats.people)} />
        <Stat label="Con correo corporativo" value={n(stats.with_email)} />
        <Stat label="Correos verificados" value={n(stats.verified)} hint="Publicados o confirmados por una respuesta" />
      </div>
      <form
        className="mt-4 flex flex-wrap items-end gap-3 border-t border-ork-line pt-4"
        onSubmit={(e) => {
          e.preventDefault();
          remove.mutate();
        }}
      >
        <div className="min-w-[260px] flex-1">
          <TextField
            label="Quitar a una persona del índice"
            placeholder="URL de LinkedIn o correo"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </div>
        <Button type="submit" variant="danger" loading={remove.isPending} disabled={value.trim().length < 5}>
          Quitar para siempre
        </Button>
      </form>
      {remove.data && <p className="mt-2 text-[13px] text-ork-graphite">Listo: {remove.data.removed} perfil(es) quitado(s). No se volverán a guardar.</p>}
      {remove.error && <Alert>{remove.error.message}</Alert>}
    </Card>
  );
}

/** Rastreador propio: lee los sitios oficiales de las empresas del directorio. */
function CrawlerCard({ crawler, onStart }: { crawler: NonNullable<DirectoryData["crawler"]>; onStart: () => void }) {
  const crawl = useMutation({ mutationFn: () => api("/api/admin/directory/crawl", { method: "POST" }), onSuccess: onStart });
  const s = crawler.by_status;
  const read = s.leido ?? 0;
  const pending = s.pendiente ?? 0;
  const failed = (s.sin_acceso ?? 0) + (s.bloqueado ?? 0) + (s.error ?? 0);
  return (
    <Card className="mt-4">
      <CardHeader
        title="Rastreador de sitios web"
        description="ORKESTA lee solo, poco a poco y respetando robots.txt, el sitio oficial de cada empresa del directorio: teléfono, correo general, LinkedIn, tamaño, descripción y formato de correo. Unas 1.700 empresas al día."
        action={
          <Button size="sm" variant="secondary" loading={crawl.isPending} disabled={crawler.running} onClick={() => crawl.mutate()}>
            {crawler.running ? "Leyendo sitios…" : "Rastrear ahora"}
          </Button>
        }
      />
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Sitios leídos" value={n(read)} />
        <Stat label="Formatos de correo aprendidos" value={n(crawler.patterns)} />
        <Stat label="Pendientes" value={n(pending)} />
        <Stat label="Sin acceso" value={n(failed)} hint="Sitios caídos, bloqueados o solo con JavaScript" />
      </div>
      {crawler.last_run && <p className="mt-3 text-[12.5px] text-ork-mist">Última tanda: {formatWhen(crawler.last_run)} · {crawler.last_batch ?? 0} sitios.</p>}
    </Card>
  );
}

/** Entrena la IA con bases reales: solo se guarda lo aprendido de cada empresa. */
function TrainCard({ onDone }: { onDone: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const train = useMutation({
    mutationFn: () => {
      const body = new FormData();
      files.forEach((f) => body.append("files", f));
      return api<TrainResult>("/api/admin/knowledge", { method: "POST", body });
    },
    onSuccess: () => {
      setFiles([]);
      onDone();
    },
  });
  const r = train.data;
  return (
    <Card className="mt-4 border-ork-violet/25">
      <CardHeader
        title="Entrenar la IA con bases reales"
        description="Sube exportaciones de Wiza, registros de eventos o listas de campañas (CSV o Excel), o el archivo de conocimiento de ORKESTA (.json). De cada fila se leen solo nombre, cargo, empresa, ubicación, correo y teléfono: ORKESTA aprende el dominio y el formato de correo de cada empresa, y las personas entran al índice de personas. Úsalo solo con bases que tengas autorización para usar. Nunca guarda correos personales (gmail, hotmail…)."
      />
      {train.error && <Alert>{train.error.message}</Alert>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-[13px] font-semibold text-ork-ink">
          Archivos (hasta 40)
          <input type="file" multiple accept=".csv,.xlsx,.json" className="mt-1 block text-[13px]" onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
        </label>
        <Button loading={train.isPending} disabled={files.length === 0} onClick={() => train.mutate()}>
          Entrenar con {files.length || ""} archivo{files.length === 1 ? "" : "s"}
        </Button>
      </div>
      {r && (
        <div className="mt-4 rounded-xl bg-ork-violet/[0.06] p-4 text-[13.5px] text-ork-graphite">
          <p className="font-semibold text-ork-ink">
            Listo: {n(r.people)} personas leídas de {r.files} archivo(s).
          </p>
          <p className="mt-1">
            ORKESTA aprendió el formato de correo de {n(r.domains)} dominios y el sitio web de {n(r.with_domain)} empresas; {n(r.companies)} empresas
            quedaron en el directorio{r.profiles ? ` y ${n(r.profiles)} personas en el índice` : ""}.
          </p>
          {r.evaluation.evaluated > 0 && (
            <p className="mt-1">
              Prueba de exactitud con {n(r.evaluation.evaluated)} correos reales: acierta el <strong className="text-ok">{r.evaluation.accuracy}%</strong> en empresas
              conocidas (adivinando nombre.apellido sería {r.evaluation.baseline}%).
            </p>
          )}
          {r.errors.length > 0 && <p className="mt-1 text-warn">No se pudieron leer: {r.errors.join(" · ")}</p>}
        </div>
      )}
    </Card>
  );
}

function DirectoryPage() {
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ["admin-directory"],
    queryFn: () => api<DirectoryData>("/api/admin/directory"),
    refetchInterval: (query) => {
      const d = query.state.data;
      return d && [...d.presets, ...d.uploads].some((x) => x.live?.status === "running") ? 3000 : false;
    },
  });
  const preset = useMutation({
    mutationFn: (id: string) => api(`/api/admin/directory/presets/${id}`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["admin-directory"] }),
  });
  const [file, setFile] = useState<File | null>(null);
  const [country, setCountry] = useState("CO");
  const [source, setSource] = useState("");
  const upload = useMutation({
    mutationFn: () => {
      const body = new FormData();
      body.append("file", file as File);
      body.append("country", country);
      body.append("source", source.trim());
      return api("/api/admin/directory/upload", { method: "POST", body });
    },
    onSuccess: () => {
      setFile(null);
      client.invalidateQueries({ queryKey: ["admin-directory"] });
    },
  });
  const d = q.data;
  return (
    <>
      <Title
        title="Directorio de empresas"
        description="Base propia de ORKESTA con registros públicos oficiales. La búsqueda de Cuentas la consulta primero, y crece sola con cada búsqueda."
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Empresas en el directorio" value={n(d?.total ?? 0)} />
        <Stat label="Países" value={d?.countries.length ?? 0} />
        <Stat label="Con sitio web identificado" value={n((d?.countries ?? []).reduce((a, c) => a + c.with_domain, 0))} />
      </div>
      <Card className="mt-4">
        <CardHeader title="Registros oficiales automáticos" description="Se descargan solos la primera vez y se actualizan cada mes." />
        <ul className="divide-y divide-ork-line">
          {(d?.presets ?? []).map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div>
                <p className="text-[14px] font-semibold text-ork-ink">{p.label}</p>
                <ImportStatus info={p.live?.status === "running" ? p.live : p.last} />
              </div>
              <Button size="sm" variant="secondary" loading={preset.isPending && preset.variables === p.id} disabled={p.live?.status === "running"} onClick={() => preset.mutate(p.id)}>
                Actualizar ahora
              </Button>
            </li>
          ))}
        </ul>
      </Card>
      {d?.crawler && <CrawlerCard crawler={d.crawler} onStart={() => client.invalidateQueries({ queryKey: ["admin-directory"] })} />}
      {d?.people && <PeopleCard stats={d.people} onDone={() => client.invalidateQueries({ queryKey: ["admin-directory"] })} />}
      <TrainCard onDone={() => client.invalidateQueries({ queryKey: ["admin-directory"] })} />
      <Card className="mt-4">
        <CardHeader
          title="Cargar un registro público (CSV)"
          description="Sirve cualquier registro oficial descargable: DENUE de INEGI (México), SUNAT (Perú), SII (Chile), RUES o cámaras de comercio. ORKESTA detecta sola las columnas: razón social, NIT/RUT/RFC, actividad (CIIU o SCIAN), ciudad, ingresos, empleados y sitio web."
        />
        {upload.error && <Alert>{upload.error.message}</Alert>}
        <div className="grid gap-3 md:grid-cols-[1fr_180px_1fr_auto] md:items-end">
          <label className="text-[13px] font-semibold text-ork-ink">
            Archivo
            <input type="file" accept=".csv,.txt" className="mt-1 block w-full text-[13px]" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <SelectField label="País" value={country} onChange={(e) => setCountry(e.target.value)}>
            {COUNTRY_OPTIONS.map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </SelectField>
          <TextField label="Fuente" placeholder="Ej.: DENUE Jalisco 2026" value={source} onChange={(e) => setSource(e.target.value)} />
          <Button loading={upload.isPending} disabled={!file || source.trim().length < 2} onClick={() => upload.mutate()}>
            Cargar
          </Button>
        </div>
        {(d?.uploads.length ?? 0) > 0 && (
          <ul className="mt-4 divide-y divide-ork-line border-t border-ork-line">
            {d!.uploads.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-[13.5px]">
                <span className="font-semibold text-ork-ink">{u.id.replace("archivo:", "")}</span>
                <ImportStatus info={u.live?.status === "running" ? u.live : u.last} />
              </li>
            ))}
          </ul>
        )}
      </Card>
      {(d?.countries.length ?? 0) > 0 && (
        <Card className="mt-4">
          <CardHeader title="Por país" />
          <table className="w-full text-left text-[13.5px]">
            <thead className="text-[11.5px] uppercase tracking-wide text-ork-mist">
              <tr>
                <th className="py-2 font-semibold">País</th>
                <th className="py-2 font-semibold">Empresas</th>
                <th className="py-2 font-semibold">Con sitio web</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ork-line">
              {d!.countries.map((c) => (
                <tr key={c.country}>
                  <td className="py-2">{COUNTRY_OPTIONS.find(([code]) => code === c.country)?.[1] ?? c.country}</td>
                  <td className="py-2">{n(c.companies)}</td>
                  <td className="py-2">{n(c.with_domain)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
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

// --- IA propia · Cerebro ---------------------------------------------------------------------

type BrainVersion = {
  version: number;
  algorithm: string;
  active: boolean;
  trigger: string;
  trained_at: string;
  accuracy: number | null;
  macro_f1: number | null;
  baseline: number | null;
  n_train: number;
  n_test: number;
  notes: string;
  duration_ms: number;
};
type BrainTask = {
  name: string;
  label: string;
  description: string;
  step: number;
  trainable: boolean;
  method: string;
  status: { label: string; value: string; hint?: string }[];
  feedback: number;
  active:
    | (BrainVersion & {
        threshold: number;
        confident: { threshold: number; coverage: number; accuracy: number } | null;
        per_label: Record<string, { precision: number; recall: number; f1: number; support: number }> | null;
        confusions: [string, string, number][] | null;
        size_kb: number;
        extra?: {
          titles?: number;
          coverage_rules?: number;
          coverage_model?: number;
          eval_n?: number;
          eval_rules?: number;
          eval_model?: number;
          eval_decides?: number;
        } | null;
      })
    | null;
  history: BrainVersion[];
  running: { state: "running" | "done" | "error"; error?: string; version?: number; active?: boolean } | null;
};
type BrainData = { tasks: BrainTask[]; nightly: string | null; schedule: string };

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 1000) / 10} %`);
const ALGORITHMS: Record<string, string> = { naive_bayes: "Naive Bayes", logistic: "Regresión logística", lexicon: "Léxico aprendido" };

function AccuracyBar({ label, value, tone }: { label: string; value: number | null; tone: "model" | "rules" }) {
  return (
    <div>
      <div className="flex items-center justify-between text-[12.5px]">
        <span className="font-semibold text-ork-graphite">{label}</span>
        <span className="font-bold text-ork-ink">{pct(value)}</span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-ork-ink/5">
        <div
          className={cx("h-full rounded-full", tone === "model" ? "bg-gradient-to-r from-[#2563EB] to-[#8B5CF6]" : "bg-ork-mist/50")}
          style={{ width: `${Math.max(2, (value ?? 0) * 100)}%` }}
        />
      </div>
    </div>
  );
}

function BrainCard({ task, onChange }: { task: BrainTask; onChange: () => void }) {
  const client = useQueryClient();
  const train = useMutation({ mutationFn: () => api(`/api/admin/brain/${task.name}/train`, { method: "POST" }), onSuccess: onChange });
  const activate = useMutation({
    mutationFn: (version: number) => api<BrainData>(`/api/admin/brain/${task.name}/versions/${version}/activate`, { method: "POST" }),
    onSuccess: (data) => client.setQueryData(["admin-brain"], data),
  });
  const a = task.active;
  const running = task.running?.state === "running";
  const gain = a?.accuracy != null && a.baseline != null ? a.accuracy - a.baseline : null;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-2xl">
          <h2 className="text-[16px] font-bold text-ork-ink">{task.label}</h2>
          <p className="mt-0.5 text-[13px] text-ork-mist">{task.description}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={a ? "violet" : "neutral"}>{task.method}</Badge>
          {task.trainable ? (
            <Button size="sm" variant="secondary" loading={running || train.isPending} onClick={() => train.mutate()}>
              {running ? "Entrenando…" : "Entrenar ahora"}
            </Button>
          ) : (
            <Badge tone="blue">Modelo en el paso {task.step}</Badge>
          )}
        </div>
      </div>
      {train.error && <div className="mt-3"><Alert>{train.error.message}</Alert></div>}
      {task.running?.state === "error" && <div className="mt-3"><Alert>{task.running.error}</Alert></div>}
      {task.running?.state === "done" && task.running.active === false && (
        <div className="mt-3"><Alert tone="blue">La versión {task.running.version} se entrenó pero no se activó: no superó a la versión activa o a las reglas.</Alert></div>
      )}
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {task.status.map((s) => (
          <div key={s.label} className="rounded-xl bg-ork-ink/[0.03] px-3 py-2.5">
            <p className="text-[11.5px] font-semibold text-ork-mist">{s.label}</p>
            <p className="text-[18px] font-bold text-ork-ink">{s.value}</p>
            {s.hint && <p className="text-[11.5px] text-ork-mist">{s.hint}</p>}
          </div>
        ))}
        <div className="rounded-xl bg-ork-ink/[0.03] px-3 py-2.5">
          <p className="text-[11.5px] font-semibold text-ork-mist">Correcciones de personas</p>
          <p className="text-[18px] font-bold text-ork-ink">{n(task.feedback)}</p>
          <p className="text-[11.5px] text-ork-mist">Ejemplos para aprender</p>
        </div>
      </div>
      {a && (
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <AccuracyBar label={`Modelo v${a.version}`} value={a.accuracy} tone="model" />
            <AccuracyBar label="Reglas (método anterior)" value={a.baseline} tone="rules" />
            <p className="text-[12.5px] text-ork-mist">
              Medido en {n(a.n_test)} casos que el modelo no vio al entrenar.
              {gain != null && (
                <strong className={cx("ml-1", gain >= 0 ? "text-ok" : "text-bad")}>
                  {gain >= 0 ? "+" : ""}
                  {Math.round(gain * 1000) / 10} puntos frente a las reglas.
                </strong>
              )}
            </p>
            {a.confident && (
              <p className="rounded-xl bg-ork-blue/5 px-3 py-2 text-[12.5px] text-ork-graphite">
                Decide solo en el <strong>{pct(a.confident.coverage)}</strong> de los casos, con un acierto de <strong>{pct(a.confident.accuracy)}</strong>; en el resto
                duda y deja la decisión a las reglas o a una persona.
              </p>
            )}
            {a.extra?.eval_n != null && (
              <p className="rounded-xl bg-ok/5 px-3 py-2 text-[12.5px] text-ork-graphite">
                Prueba independiente ({a.extra.eval_n} respuestas que nunca usó para aprender): las listas de frases aciertan{" "}
                <strong>{pct(a.extra.eval_rules)}</strong>; el modelo decide solo en el <strong>{pct(a.extra.eval_decides)}</strong> y acierta{" "}
                <strong className="text-ok">{pct(a.extra.eval_model)}</strong>. El resto te lo pregunta.
              </p>
            )}
            {a.extra?.titles != null && (
              <p className="rounded-xl bg-ok/5 px-3 py-2 text-[12.5px] text-ork-graphite">
                En los {n(a.extra.titles)} cargos del índice: <strong>{pct(a.extra.coverage_rules)}</strong> con solo reglas →{" "}
                <strong className="text-ok">{pct(a.extra.coverage_model)}</strong> con el modelo.
              </p>
            )}
            <p className="text-[12px] text-ork-mist">
              {ALGORITHMS[a.algorithm] ?? a.algorithm} · {n(a.n_train)} ejemplos de entrenamiento · {a.size_kb} KB · entrenado {formatWhen(a.trained_at)}
            </p>
          </div>
          <div>
            <p className="text-[12.5px] font-semibold text-ork-graphite">Versiones</p>
            <ul className="mt-1.5 divide-y divide-ork-line text-[13px]">
              {task.history.map((v) => (
                <li key={v.version} className="flex items-center justify-between gap-3 py-2">
                  <span className="min-w-0">
                    <span className="font-semibold text-ork-ink">v{v.version}</span>
                    <span className="ml-2 text-ork-mist">
                      {pct(v.accuracy)} · {v.trigger === "nocturno" ? "nocturno" : "manual"} · {formatWhen(v.trained_at)}
                    </span>
                    {v.notes && <span className="block text-[12px] text-ork-mist">{v.notes}</span>}
                  </span>
                  {v.active ? (
                    <Badge tone="ok">Activa</Badge>
                  ) : (
                    <Button size="sm" variant="ghost" loading={activate.isPending && activate.variables === v.version} onClick={() => activate.mutate(v.version)}>
                      Activar
                    </Button>
                  )}
                </li>
              ))}
            </ul>
            {a.confusions && a.confusions.length > 0 && (
              <>
                <p className="mt-3 text-[12.5px] font-semibold text-ork-graphite">Confusiones más frecuentes</p>
                <ul className="mt-1 text-[12.5px] text-ork-mist">
                  {a.confusions.slice(0, 4).map(([real, pred, count]) => (
                    <li key={`${real}-${pred}`}>
                      {real === "_ninguna" ? "sin área" : real} → {pred === "_ninguna" ? "sin área" : pred}: {count}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>
      )}
      {task.name === "cargos" && <TitleTeacher onTaught={onChange} />}
    </Card>
  );
}

type TitleTry = {
  title: string;
  rules: { level: string | null; area: string | null };
  cargos: { label: string | null; confidence: number; sure: boolean; evidence: string[] } | null;
  cargos_nivel: { label: string | null; confidence: number; sure: boolean; evidence: string[] } | null;
  final: { level: string | null; area: string | null };
};
type Doubts = { items: { title: string; count: number }[]; areas: Record<string, string>; levels: Record<string, string> };

function TitleTeacher({ onTaught }: { onTaught: () => void }) {
  const client = useQueryClient();
  const [title, setTitle] = useState("");
  const doubts = useQuery({ queryKey: ["admin-brain-doubts"], queryFn: () => api<Doubts>("/api/admin/brain/cargos/doubts") });
  const tryIt = useMutation({ mutationFn: (t: string) => api<TitleTry>("/api/admin/brain/cargos/try", { method: "POST", json: { title: t } }) });
  const teach = useMutation({
    mutationFn: (body: { title: string; area: string | null; level?: string | null }) => api("/api/admin/brain/cargos/teach", { method: "POST", json: body }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["admin-brain-doubts"] });
      onTaught();
    },
  });
  const areas = doubts.data?.areas ?? {};
  const label = (code: string | null | undefined) => (code ? areas[code] ?? doubts.data?.levels[code] ?? code : "Sin área");
  const r = tryIt.data;
  return (
    <div className="mt-5 grid gap-5 border-t border-ork-line pt-5 lg:grid-cols-2">
      <div>
        <p className="text-[13.5px] font-bold text-ork-ink">Probar un cargo</p>
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (title.trim()) tryIt.mutate(title.trim());
          }}
        >
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Ej.: Gerente de Siniestros"
            className="ork-focus h-10 min-w-0 flex-1 rounded-xl border border-ork-line bg-ork-card px-3 text-[13.5px] text-ork-ink"
          />
          <Button type="submit" size="sm" loading={tryIt.isPending}>
            Probar
          </Button>
        </form>
        {r && (
          <div className="mt-3 rounded-xl bg-ork-ink/[0.03] p-3 text-[13px]">
            <p>
              <span className="text-ork-mist">Reglas:</span> <strong className="text-ork-ink">{label(r.rules.area)}</strong> · {label(r.rules.level)}
            </p>
            {r.cargos && (
              <p className="mt-1">
                <span className="text-ork-mist">Modelo:</span> <strong className="text-ork-ink">{label(r.cargos.label)}</strong> ({Math.round(r.cargos.confidence * 100)} %
                {r.cargos.sure ? ", seguro" : ", duda"})
                {r.cargos.evidence.length > 0 && <span className="text-ork-mist"> por «{r.cargos.evidence.join("», «")}»</span>}
              </p>
            )}
            <p className="mt-1">
              <span className="text-ork-mist">ORKESTA decide:</span> <strong className="text-ork-blue">{label(r.final.area)}</strong> · {label(r.final.level)}
            </p>
          </div>
        )}
      </div>
      <div>
        <p className="text-[13.5px] font-bold text-ork-ink">Cargos por enseñar</p>
        <p className="text-[12.5px] text-ork-mist">Los más frecuentes del índice que aún quedan sin área. Cada respuesta enseña al modelo en el próximo entrenamiento.</p>
        {teach.error && <div className="mt-2"><Alert>{teach.error.message}</Alert></div>}
        <ul className="mt-2 max-h-72 divide-y divide-ork-line overflow-y-auto pr-1 text-[13px]">
          {(doubts.data?.items ?? []).map((d) => (
            <li key={d.title} className="flex items-center justify-between gap-2 py-1.5">
              <span className="min-w-0 truncate text-ork-ink" title={d.title}>
                {d.title} <span className="text-ork-mist">· {d.count}</span>
              </span>
              <select
                aria-label={`Área de ${d.title}`}
                className="ork-focus h-8 shrink-0 rounded-lg border border-ork-line bg-ork-card px-2 text-[12.5px] text-ork-ink"
                defaultValue=""
                disabled={teach.isPending}
                onChange={(e) => e.target.value && teach.mutate({ title: d.title, area: e.target.value === "_" ? null : e.target.value })}
              >
                <option value="" disabled>
                  Elegir área…
                </option>
                <option value="_">No tiene área</option>
                {Object.entries(areas).map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </select>
            </li>
          ))}
          {doubts.data && doubts.data.items.length === 0 && <li className="py-2 text-ork-mist">No quedan cargos frecuentes por enseñar.</li>}
        </ul>
      </div>
    </div>
  );
}

function BrainPage() {
  const q = useQuery({
    queryKey: ["admin-brain"],
    queryFn: () => api<BrainData>("/api/admin/brain"),
    refetchInterval: (query) => (query.state.data?.tasks.some((t) => t.running?.state === "running") ? 2000 : false),
  });
  return (
    <>
      <Title
        title="IA propia · Cerebro"
        description={`Lo que ORKESTA aprende de los datos de la plataforma, sin servicios externos. Un modelo nuevo solo se activa si acierta más que las reglas y que la versión anterior. Reentrenamiento automático: ${q.data?.schedule ?? "todas las noches"}.${q.data?.nightly ? ` Última revisión: ${q.data.nightly}.` : ""}`}
      />
      {!q.data ? (
        <Card>
          <Spinner />
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          {q.data.tasks.map((t) => (
            <BrainCard key={t.name} task={t} onChange={() => q.refetch()} />
          ))}
        </div>
      )}
    </>
  );
}

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
        <span className={cx("absolute top-0.5 size-5 rounded-full bg-ork-card shadow transition-all", checked ? "left-[22px]" : "left-0.5")} />
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
            <Toggle label="Registro abierto" hint="Apagado (recomendado): ORKESTA es solo por invitación; solo entra quien invites desde Demos o Equipo. Encendido: cualquiera puede crear su empresa." checked={draft.signup_open} onChange={(v) => setDraft({ ...draft, signup_open: v })} />
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
                    <select value={m.role} onChange={(e) => change.mutate({ id: m.id, role: e.target.value })} className="ork-focus h-9 rounded-lg border border-ork-line bg-ork-card px-2 text-[13px]">
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
  "tenant.merged": "unió las empresas",
  "tenant.deleted": "eliminó la empresa",
  "tenant.created": "creó la empresa",
  "tenant.demo_started": "creó la demo de",
  "user.added": "agregó a",
  "user.deleted": "eliminó la cuenta de",
  "brain.train": "entrenó el modelo",
  "brain.activate": "activó la versión",
  "brain.teach": "enseñó el cargo",
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Link2, MailPlus } from "lucide-react";
import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  CardHeader,
  cx,
  Modal,
  PageHeader,
  SelectField,
  Spinner,
  TextField,
} from "../components/ui";
import { api, type Session } from "../lib/api";
import { useSession } from "../lib/session";
import { GmailCard } from "./campaigns";
import { formatWhen } from "./dashboard";

type Team = {
  roles: { value: string; label: string }[];
  members: {
    id: string;
    full_name: string;
    email: string;
    job_title?: string;
    avatar_url?: string | null;
    role: string;
    active: boolean;
    is_me: boolean;
    last_login_at: string | null;
  }[];
  invitations: { id: string; email: string; role: string; expires_at: string }[];
};

export function SettingsPage() {
  const { session } = useSession();
  const role = session?.workspace?.role;
  const tabs = [
    { to: "/configuracion/perfil", label: "Mi perfil" },
    { to: "/configuracion/empresa", label: "Empresa" },
    { to: "/configuracion/equipo", label: "Equipo" },
    { to: "/configuracion/integraciones", label: "Integraciones" },
    ...(role !== "vendedor" ? [{ to: "/configuracion/auditoria", label: "Auditoría" }] : []),
  ];
  return (
    <>
      <PageHeader title="Configuración" description="Tu perfil, tu empresa, tu equipo y las conexiones de ORKESTA." />
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-ork-line" aria-label="Secciones de configuración">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              cx(
                "ork-focus -mb-px whitespace-nowrap border-b-2 px-3.5 py-2.5 text-[14px] font-semibold",
                isActive ? "border-ork-blue text-ork-blue" : "border-transparent text-ork-mist hover:text-ork-ink",
              )
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="/configuracion/perfil" replace />} />
        <Route path="perfil" element={<ProfileSettings />} />
        <Route path="empresa" element={<CompanySettings />} />
        <Route path="equipo" element={<TeamSettings />} />
        <Route path="integraciones" element={<IntegrationSettings />} />
        <Route path="auditoria" element={<AuditSettings />} />
        <Route path="plataforma" element={<Navigate to="/admin" replace />} />
      </Routes>
    </>
  );
}

type Profile = {
  full_name: string;
  email: string;
  job_title: string;
  phone: string;
  recovery_email: string;
  timezone: string;
  avatar_url: string | null;
  has_password: boolean;
};

const TIMEZONES = [
  "America/Bogota", "America/Mexico_City", "America/Lima", "America/Santiago", "America/Argentina/Buenos_Aires",
  "America/Guayaquil", "America/Caracas", "America/Panama", "America/Costa_Rica", "America/Guatemala",
  "America/Santo_Domingo", "America/Montevideo", "America/Asuncion", "America/La_Paz", "America/Sao_Paulo",
  "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/Madrid", "UTC",
];

/** Reduce la foto a un cuadrado de 320 px (JPG) antes de subirla. */
async function squarePhoto(file: File): Promise<Blob> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("No pude leer esa imagen."));
      el.src = url;
    });
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 320;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, 320, 320);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("No pude procesar la foto."))), "image/jpeg", 0.86),
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}

function ProfileSettings() {
  const { setSession } = useSession();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["profile"], queryFn: () => api<Profile>("/api/profile") });
  const [form, setForm] = useState<Partial<Profile>>({});
  const p = { ...q.data, ...form } as Profile;
  const refresh = async (data: Profile) => {
    client.setQueryData(["profile"], data);
    setSession(await api<Session>("/api/auth/me"));
  };
  const save = useMutation({
    mutationFn: () =>
      api<Profile>("/api/profile", {
        method: "PATCH",
        json: { full_name: p.full_name, job_title: p.job_title, phone: p.phone, recovery_email: p.recovery_email, timezone: p.timezone },
      }),
    onSuccess: async (data) => {
      setForm({});
      await refresh(data);
    },
  });
  const photo = useMutation({
    mutationFn: async (file: File | null) => {
      if (!file) return api<Profile>("/api/profile/avatar", { method: "DELETE" });
      const body = new FormData();
      body.append("file", await squarePhoto(file), "foto.jpg");
      return api<Profile>("/api/profile/avatar", { method: "PUT", body });
    },
    onSuccess: refresh,
  });
  const [pw, setPw] = useState({ current: "", new: "", repeat: "" });
  const password = useMutation({
    mutationFn: () => {
      if (pw.new !== pw.repeat) throw new Error("Las contraseñas nuevas no coinciden.");
      return api("/api/profile/password", { method: "POST", json: { current: pw.current, new: pw.new } });
    },
    onSuccess: () => setPw({ current: "", new: "", repeat: "" }),
  });
  if (q.isLoading || !q.data) return <Spinner />;
  const set = (patch: Partial<Profile>) => setForm((f) => ({ ...f, ...patch }));
  return (
    <div className="grid max-w-4xl gap-5 lg:grid-cols-[280px_1fr]">
      <Card className="flex flex-col items-center gap-3 text-center">
        <Avatar name={p.full_name} email={p.email} size={112} src={p.avatar_url} />
        <div>
          <p className="text-[16px] font-bold text-ork-ink">{p.full_name || p.email}</p>
          <p className="text-[13px] text-ork-mist">{p.job_title || "Sin cargo"}</p>
        </div>
        {photo.error && <Alert>{photo.error.message}</Alert>}
        <label className="ork-focus inline-flex h-9 cursor-pointer items-center rounded-xl border border-ork-line bg-ork-card px-3 text-[13px] font-semibold text-ork-ink hover:border-ork-blue/40">
          {photo.isPending ? "Subiendo…" : p.avatar_url ? "Cambiar foto" : "Subir foto"}
          <input type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={(e) => e.target.files?.[0] && photo.mutate(e.target.files[0])} />
        </label>
        {p.avatar_url && (
          <button type="button" className="text-[12.5px] font-semibold text-ork-mist hover:text-bad" onClick={() => photo.mutate(null)}>
            Quitar foto
          </button>
        )}
        <p className="text-[12px] text-ork-mist">Tu foto aparece en la barra lateral, en tu equipo y junto a todo lo que registras en ORKESTA.</p>
      </Card>
      <div className="flex flex-col gap-5">
        <Card>
          <CardHeader title="Datos personales" description="Así te verá tu equipo en ORKESTA." />
          <form
            className="grid gap-4 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            {save.error && <div className="sm:col-span-2"><Alert>{save.error.message}</Alert></div>}
            {save.isSuccess && <div className="sm:col-span-2"><Alert tone="ok">Perfil actualizado.</Alert></div>}
            <TextField label="Nombre completo" value={p.full_name} onChange={(e) => set({ full_name: e.target.value })} required />
            <TextField label="Cargo" placeholder="Ej.: Gerente comercial" value={p.job_title} onChange={(e) => set({ job_title: e.target.value })} />
            <TextField label="Correo de la cuenta" value={p.email} disabled />
            <TextField label="Correo de recuperación" type="email" placeholder="otro@correo.com" value={p.recovery_email} onChange={(e) => set({ recovery_email: e.target.value })} />
            <TextField label="Teléfono" type="tel" placeholder="+57 300 000 0000" value={p.phone} onChange={(e) => set({ phone: e.target.value })} />
            <SelectField label="Zona horaria" value={p.timezone} onChange={(e) => set({ timezone: e.target.value })}>
              <option value="">Detectar automáticamente</option>
              {TIMEZONES.map((z) => (
                <option key={z} value={z}>
                  {z.replace("America/", "").replace("Europe/", "").replaceAll("_", " ")}
                </option>
              ))}
            </SelectField>
            <div className="sm:col-span-2">
              <Button type="submit" loading={save.isPending} disabled={Object.keys(form).length === 0}>
                Guardar cambios
              </Button>
            </div>
          </form>
        </Card>
        <Card>
          <CardHeader title={p.has_password ? "Cambiar contraseña" : "Crear contraseña"} description="Mínimo 10 caracteres." />
          <form
            className="grid gap-4 sm:grid-cols-3"
            onSubmit={(e) => {
              e.preventDefault();
              password.mutate();
            }}
          >
            {password.error && <div className="sm:col-span-3"><Alert>{password.error.message}</Alert></div>}
            {password.isSuccess && <div className="sm:col-span-3"><Alert tone="ok">Contraseña actualizada.</Alert></div>}
            {p.has_password && <TextField label="Contraseña actual" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />}
            <TextField label="Nueva contraseña" type="password" autoComplete="new-password" value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} />
            <TextField label="Repite la nueva" type="password" autoComplete="new-password" value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} />
            <div className="sm:col-span-3">
              <Button type="submit" variant="secondary" loading={password.isPending} disabled={!pw.new}>
                Guardar contraseña
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}

function CompanySettings() {
  const { session, setSession } = useSession();
  const isAdmin = session?.workspace?.role === "admin";
  const [name, setName] = useState(session?.workspace?.name ?? "");
  const save = useMutation({
    mutationFn: () => api<{ name: string }>("/api/workspace", { method: "PATCH", json: { name } }),
    onSuccess: async () => setSession(await api<Session>("/api/auth/me")),
  });
  return (
    <Card className="max-w-2xl">
      <CardHeader title="Datos de la empresa" description="La identidad visual, los productos y los servicios se configuran en etapas siguientes." />
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        {save.error && <Alert>{save.error.message}</Alert>}
        {save.isSuccess && <Alert tone="ok">Nombre actualizado.</Alert>}
        <TextField label="Nombre de la empresa" value={name} disabled={!isAdmin} onChange={(e) => setName(e.target.value)} />
        {isAdmin ? (
          <div>
            <Button type="submit" loading={save.isPending}>
              Guardar
            </Button>
          </div>
        ) : (
          <p className="text-[13px] text-ork-mist">Solo un administrador puede cambiarlo.</p>
        )}
      </form>
    </Card>
  );
}

function TeamSettings() {
  const { session } = useSession();
  const isAdmin = session?.workspace?.role === "admin";
  const client = useQueryClient();
  const team = useQuery({ queryKey: ["team"], queryFn: () => api<Team>("/api/team") });
  const [inviteOpen, setInviteOpen] = useState(false);
  const [form, setForm] = useState({ email: "", role: "vendedor" });
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const refresh = () => client.invalidateQueries({ queryKey: ["team"] });
  const invite = useMutation({
    mutationFn: () => api<{ link: string }>("/api/team/invitations", { method: "POST", json: form }),
    onSuccess: (data) => {
      setLink(data.link);
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, ...body }: { id: string; role?: string; active?: boolean }) =>
      api(`/api/team/members/${id}`, { method: "PATCH", json: body }),
    onSettled: refresh,
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api/team/invitations/${id}`, { method: "DELETE" }),
    onSettled: refresh,
  });
  const roleLabel = (value: string) => team.data?.roles.find((r) => r.value === value)?.label ?? value;

  if (team.isLoading) return <Spinner />;
  if (team.error) return <Alert>{team.error.message}</Alert>;
  const data = team.data!;
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader
          title={`Miembros (${data.members.filter((m) => m.active).length})`}
          description="Administrador: todo. Supervisor: ve el trabajo del equipo y la auditoría. Vendedor: su propio trabajo."
          action={
            isAdmin && (
              <Button
                icon={<MailPlus className="size-4" aria-hidden />}
                onClick={() => {
                  setLink(null);
                  setCopied(false);
                  setForm({ email: "", role: "vendedor" });
                  setInviteOpen(true);
                }}
              >
                Invitar
              </Button>
            )
          }
        />
        {update.error && (
          <div className="mb-3">
            <Alert>{update.error.message}</Alert>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-[13.5px]">
            <thead>
              <tr className="border-b border-ork-line text-[12px] uppercase tracking-wide text-ork-mist">
                <th className="py-2.5 pr-3 font-semibold">Nombre</th>
                <th className="py-2.5 pr-3 font-semibold">Rol</th>
                <th className="py-2.5 pr-3 font-semibold">Estado</th>
                <th className="py-2.5 pr-3 font-semibold">Último ingreso</th>
                <th className="py-2.5" />
              </tr>
            </thead>
            <tbody>
              {data.members.map((m) => (
                <tr key={m.id} className="border-b border-ork-line last:border-0">
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={m.full_name} email={m.email} src={m.avatar_url} />
                      <div>
                        <p className="font-semibold text-ork-ink">
                          {m.full_name || m.email} {m.is_me && <span className="font-normal text-ork-mist">(tú)</span>}
                        </p>
                        <p className="text-[12.5px] text-ork-mist">
                          {m.job_title ? `${m.job_title} · ` : ""}
                          {m.email}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="py-3 pr-3">
                    {isAdmin ? (
                      <select
                        aria-label={`Rol de ${m.email}`}
                        value={m.role}
                        onChange={(e) => update.mutate({ id: m.id, role: e.target.value })}
                        className="ork-focus h-9 rounded-lg border border-ork-line bg-ork-card px-2 text-[13.5px]"
                      >
                        {data.roles.map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      roleLabel(m.role)
                    )}
                  </td>
                  <td className="py-3 pr-3">{m.active ? <Badge tone="ok">Activo</Badge> : <Badge>Desactivado</Badge>}</td>
                  <td className="py-3 pr-3 text-ork-mist">{m.last_login_at ? formatWhen(m.last_login_at) : "Nunca"}</td>
                  <td className="py-3 text-right">
                    {isAdmin && !m.is_me && (
                      <Button size="sm" variant={m.active ? "danger" : "secondary"} onClick={() => update.mutate({ id: m.id, active: !m.active })}>
                        {m.active ? "Desactivar" : "Reactivar"}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <CardHeader title="Invitaciones pendientes" description="Cada enlace sirve una sola vez y vence en 7 días." />
        {data.invitations.length === 0 ? (
          <p className="text-[13.5px] text-ork-mist">No hay invitaciones pendientes.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {data.invitations.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div>
                  <p className="text-[14px] font-semibold text-ork-ink">{i.email}</p>
                  <p className="text-[12.5px] text-ork-mist">
                    {roleLabel(i.role)} · vence {formatWhen(i.expires_at)}
                  </p>
                </div>
                {isAdmin && (
                  <Button size="sm" variant="ghost" onClick={() => revoke.mutate(i.id)}>
                    Anular
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        title="Invitar al equipo"
        description="ORKESTA genera un enlace personal para que lo compartas por tu correo o chat."
      >
        {link ? (
          <div className="flex flex-col gap-4">
            <Alert tone="ok">Invitación creada para {form.email}.</Alert>
            <div className="flex items-center gap-2 rounded-xl border border-ork-line bg-ork-surface p-2 pl-3">
              <Link2 className="size-4 shrink-0 text-ork-mist" aria-hidden />
              <input readOnly value={link} aria-label="Enlace de invitación" className="min-w-0 flex-1 bg-transparent text-[13px] text-ork-ink outline-none" />
              <Button
                size="sm"
                variant="secondary"
                icon={<Copy className="size-3.5" aria-hidden />}
                onClick={async () => {
                  await navigator.clipboard.writeText(link);
                  setCopied(true);
                }}
              >
                {copied ? "Copiado" : "Copiar"}
              </Button>
            </div>
            <p className="text-[12.5px] text-ork-mist">El envío automático por correo llega cuando conectes tu correo (Etapa 5).</p>
            <Button variant="secondary" onClick={() => setInviteOpen(false)}>
              Listo
            </Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              invite.mutate();
            }}
          >
            {invite.error && <Alert>{invite.error.message}</Alert>}
            <TextField label="Correo" type="email" required autoFocus value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            <SelectField label="Rol" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {data.roles.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </SelectField>
            <Button type="submit" loading={invite.isPending}>
              Crear invitación
            </Button>
          </form>
        )}
      </Modal>
    </div>
  );
}

function IntegrationSettings() {
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api<{ google: boolean }>("/api/auth/config") });
  const items = [
    {
      name: "Inicio de sesión con Google",
      detail: "Tu equipo entra con su cuenta de Google Workspace, sin contraseñas nuevas.",
      status: config.data?.google ? "connected" : "pending",
    },
    { name: "Microsoft 365 y Outlook", detail: "Campañas desde buzones de Microsoft.", status: "stage", stage: 8 },
  ] as const;
  return (
    <div className="flex flex-col gap-4">
      <GmailCard />
      <DiagnosticsCard />
      <div className="grid gap-4 md:grid-cols-2">
      {items.map((item) => (
        <Card key={item.name} className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[14.5px] font-semibold text-ork-ink">{item.name}</p>
            <p className="mt-1 text-[13px] text-ork-mist">{item.detail}</p>
          </div>
          {item.status === "connected" && <Badge tone="ok">Conectado</Badge>}
          {item.status === "pending" && <Badge tone="warn">Pendiente de conexión</Badge>}
          {item.status === "stage" && <Badge tone="violet">Etapa {"stage" in item ? item.stage : ""}</Badge>}
        </Card>
      ))}
      </div>
    </div>
  );
}

type DiagCheck = { name: string; ok: boolean; detail: string; ms: number };

export function DiagnosticsCard() {
  const { session } = useSession();
  // Detalle técnico del motor: solo para el equipo de ORKESTA, no para los clientes.
  const allowed = Boolean(session?.user.platform_admin) && session?.workspace?.role !== "vendedor";
  const test = useMutation({ mutationFn: () => api<{ checks: DiagCheck[] }>("/api/research/diagnostics") });
  if (!allowed) return null;
  return (
    <Card>
      <CardHeader
        title="Diagnóstico de conexiones"
        description="Prueba en vivo las fuentes del motor de prospección y la verificación de correos."
        action={
          <Button size="sm" variant="secondary" loading={test.isPending} onClick={() => test.mutate()}>
            Probar conexiones
          </Button>
        }
      />
      {test.error && <Alert>{test.error.message}</Alert>}
      {test.data && (
        <ul className="divide-y divide-ork-line">
          {test.data.checks.map((c) => (
            <li key={c.name} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
              <div>
                <p className="text-[13.5px] font-semibold text-ork-ink">{c.name}</p>
                <p className="text-[12.5px] text-ork-mist">
                  {c.detail} · {(c.ms / 1000).toFixed(1)} s
                </p>
              </div>
              <Badge tone={c.ok ? "ok" : "bad"}>{c.ok ? "Funciona" : "Falla"}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function AuditSettings() {
  const audit = useQuery({
    queryKey: ["audit", 100],
    queryFn: () => api<{ id: string; at: string; actor: string; actor_avatar?: string | null; label: string }[]>("/api/audit?limit=100"),
  });
  if (audit.isLoading) return <Spinner />;
  if (audit.error) return <Alert>{audit.error.message}</Alert>;
  return (
    <Card>
      <CardHeader title="Registro de auditoría" description="Quién hizo qué y cuándo en tu espacio de trabajo." />
      <ul className="divide-y divide-ork-line">
        {audit.data!.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-[13.5px]">
            <span className="flex items-center gap-2.5">
              <Avatar name={a.actor} email="" size={28} src={a.actor_avatar} />
              <span>
                <strong className="text-ork-ink">{a.actor}</strong> {a.label}
              </span>
            </span>
            <span className="text-[12.5px] text-ork-mist">{formatWhen(a.at)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}



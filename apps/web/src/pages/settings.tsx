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
    { to: "/configuracion/empresa", label: "Empresa" },
    { to: "/configuracion/equipo", label: "Equipo" },
    { to: "/configuracion/integraciones", label: "Integraciones" },
    ...(role !== "vendedor" ? [{ to: "/configuracion/auditoria", label: "Auditoría" }] : []),
  ];
  return (
    <>
      <PageHeader title="Configuración" description="Tu empresa, tu equipo y las conexiones de ORKESTA." />
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
        <Route index element={<Navigate to="/configuracion/empresa" replace />} />
        <Route path="empresa" element={<CompanySettings />} />
        <Route path="equipo" element={<TeamSettings />} />
        <Route path="integraciones" element={<IntegrationSettings />} />
        <Route path="auditoria" element={<AuditSettings />} />
        <Route path="plataforma" element={<Navigate to="/admin" replace />} />
      </Routes>
    </>
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
                      <Avatar name={m.full_name} email={m.email} />
                      <div>
                        <p className="font-semibold text-ork-ink">
                          {m.full_name || m.email} {m.is_me && <span className="font-normal text-ork-mist">(tú)</span>}
                        </p>
                        <p className="text-[12.5px] text-ork-mist">{m.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="py-3 pr-3">
                    {isAdmin ? (
                      <select
                        aria-label={`Rol de ${m.email}`}
                        value={m.role}
                        onChange={(e) => update.mutate({ id: m.id, role: e.target.value })}
                        className="ork-focus h-9 rounded-lg border border-ork-line bg-white px-2 text-[13.5px]"
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
  const research = useQuery({ queryKey: ["research-options"], queryFn: () => api<{ web_search: boolean }>("/api/research/options") });
  const items = [
    {
      name: "Inicio de sesión con Google",
      detail: "Tu equipo entra con su cuenta de Google Workspace, sin contraseñas nuevas.",
      status: config.data?.google ? "connected" : "pending",
    },
    { name: "Microsoft 365 y Outlook", detail: "Campañas desde buzones de Microsoft.", status: "stage", stage: 8 },
    {
      name: "Buscador de Google (Serper)",
      detail: research.data?.web_search
        ? "Motor principal de prospección: decisores con perfil público y empresas en Google Maps."
        : "2.500 búsquedas gratis en serper.dev; agrega la clave en Render como SERPER_API_KEY.",
      status: research.data?.web_search ? "connected" : "pending",
    },
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
  const allowed = session?.workspace?.role !== "vendedor";
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
    queryFn: () => api<{ id: string; at: string; actor: string; label: string }[]>("/api/audit?limit=100"),
  });
  if (audit.isLoading) return <Spinner />;
  if (audit.error) return <Alert>{audit.error.message}</Alert>;
  return (
    <Card>
      <CardHeader title="Registro de auditoría" description="Quién hizo qué y cuándo en tu espacio de trabajo." />
      <ul className="divide-y divide-ork-line">
        {audit.data!.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-[13.5px]">
            <span>
              <strong className="text-ork-ink">{a.actor}</strong> {a.label}
            </span>
            <span className="text-[12.5px] text-ork-mist">{formatWhen(a.at)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}



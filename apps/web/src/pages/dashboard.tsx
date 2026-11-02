import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Bell, CalendarClock, Check, CheckCircle2, Clock3, FileText, Reply } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Card, CardHeader, cx, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";

type Summary = {
  team: { members: number; pending_invitations: number };
  contacts: number;
  kpis: Record<"contacts_found" | "emails_validated" | "open_opportunities" | "conversion_rate", number | null>;
  integrations: { google_sign_in: boolean };
};

type AuditItem = { id: string; at: string; actor: string; label: string };
type Task = { id: string; body: string; due_at: string | null; done_at: string | null; contact_id: string | null; deal_id: string | null; contact_name: string | null; deal_title: string | null };
type Notice = { id: string; kind: string; title: string; body: string; link: string; read: boolean; created_at: string };

function dueLabel(iso: string | null): { text: string; tone: "bad" | "warn" | "neutral" } {
  if (!iso) return { text: "Sin fecha", tone: "neutral" };
  const due = new Date(iso);
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startDue = new Date(due.getFullYear(), due.getMonth(), due.getDate());
  const days = Math.round((startDue.getTime() - startToday.getTime()) / 86400000);
  const time = due.toLocaleTimeString("es", { hour: "numeric", minute: "2-digit" });
  if (due.getTime() < today.getTime()) return { text: days === 0 ? `Venció hoy ${time}` : `Vencida · ${formatWhen(iso)}`, tone: "bad" };
  if (days === 0) return { text: `Hoy ${time}`, tone: "warn" };
  if (days === 1) return { text: `Mañana ${time}`, tone: "neutral" };
  return { text: formatWhen(iso), tone: "neutral" };
}

const NOTICE_ICON: Record<string, typeof Bell> = { task_due: CalendarClock, reply: Reply, proposal: FileText };

/** Lo primero que se ve: tareas pendientes y novedades importantes, siempre al día. */
function TodayPanel() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const tasks = useQuery({ queryKey: ["crm-tasks"], queryFn: () => api<Task[]>("/api/crm/tasks"), refetchInterval: 30000, refetchOnWindowFocus: true });
  const notices = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<{ items: Notice[]; unread: number }>("/api/notifications"),
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });
  const done = useMutation({
    mutationFn: (id: string) => api(`/api/crm/activities/${id}/done`, { method: "POST" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["crm-tasks"] });
      client.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
  const read = useMutation({
    mutationFn: (ids: string[]) => api("/api/notifications/read", { method: "POST", json: { ids } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const pending = (tasks.data ?? []).filter((t) => !t.done_at);
  const overdue = pending.filter((t) => t.due_at && new Date(t.due_at).getTime() < Date.now()).length;
  const important = (notices.data?.items ?? []).filter((n) => n.kind !== "task_due").slice(0, 8);
  const open = (link: string) => link.startsWith("/app") && navigate(link.slice(4) || "/");
  return (
    <div className="mb-6 grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader
          title="Tus pendientes"
          description={pending.length ? `${pending.length} tarea(s)${overdue ? ` · ${overdue} vencida(s)` : ""}` : "Tareas de seguimiento de tus contactos y oportunidades."}
          action={
            <Link to="/crm?vista=tareas" className="inline-flex items-center gap-1 text-[13px] font-semibold text-ork-blue hover:underline">
              Ver todas <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          }
        />
        {tasks.isLoading ? (
          <Spinner />
        ) : pending.length === 0 ? (
          <p className="flex items-center gap-2 py-6 text-[13.5px] text-ork-mist">
            <CheckCircle2 className="size-4 text-ok" aria-hidden /> Estás al día: no tienes tareas pendientes.
          </p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {pending.slice(0, 8).map((t) => {
              const due = dueLabel(t.due_at);
              const target = t.deal_id ? "/crm" : t.contact_id ? "/contactos" : "/crm?vista=tareas";
              return (
                <li key={t.id} className="flex items-start gap-3 py-3">
                  <button
                    type="button"
                    aria-label={`Marcar como hecha: ${t.body}`}
                    onClick={() => done.mutate(t.id)}
                    className="ork-focus mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full border-2 border-ork-line text-transparent hover:border-ok hover:text-ok"
                  >
                    <Check className="size-3" aria-hidden />
                  </button>
                  <Link to={target} className="min-w-0 flex-1">
                    <p className="text-[14px] font-medium text-ork-ink">{t.body}</p>
                    <p className="text-[12.5px] text-ork-mist">{[t.contact_name, t.deal_title].filter(Boolean).join(" · ") || "Tarea"}</p>
                  </Link>
                  <Badge tone={due.tone === "bad" ? "bad" : due.tone === "warn" ? "warn" : "neutral"}>{due.text}</Badge>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <Card>
        <CardHeader
          title="Novedades"
          description={notices.data?.unread ? `${notices.data.unread} sin leer` : "Respuestas a tus campañas y movimientos de tus propuestas."}
          action={
            important.some((n) => !n.read) && (
              <button type="button" onClick={() => read.mutate(important.filter((n) => !n.read).map((n) => n.id))} className="text-[13px] font-semibold text-ork-blue hover:underline">
                Marcar como leídas
              </button>
            )
          }
        />
        {notices.isLoading ? (
          <Spinner />
        ) : important.length === 0 ? (
          <p className="py-6 text-[13.5px] text-ork-mist">Aquí verás cuando alguien responda una campaña, abra una propuesta o la acepte.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {important.map((n) => {
              const Icon = NOTICE_ICON[n.kind] ?? Bell;
              return (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => {
                      if (!n.read) read.mutate([n.id]);
                      open(n.link);
                    }}
                    className={cx("flex w-full items-start gap-3 rounded-lg py-3 text-left hover:bg-ork-ink/[0.02]", !n.read && "bg-ork-blue/[0.03]")}
                  >
                    <span className={cx("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full", n.kind === "reply" ? "bg-ok/15 text-ok" : "bg-ork-violet/15 text-ork-violet")}>
                      <Icon className="size-4" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className={cx("block text-[13.5px]", n.read ? "text-ork-graphite" : "font-semibold text-ork-ink")}>{n.title}</span>
                      {n.body && <span className="block truncate text-[12.5px] text-ork-mist">{n.body}</span>}
                      <span className="block text-[11.5px] text-ork-mist">{formatWhen(n.created_at)}</span>
                    </span>
                    {!n.read && <span className="mt-2 size-2 shrink-0 rounded-full bg-ork-blue" aria-label="Sin leer" />}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

const KPIS: { key: keyof Summary["kpis"]; label: string; stage: number }[] = [
  { key: "emails_validated", label: "Correos validados", stage: 3 },
  { key: "open_opportunities", label: "Oportunidades abiertas", stage: 6 },
  { key: "conversion_rate", label: "Tasa de conversión", stage: 6 },
];

export function formatWhen(iso: string): string {
  return new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

export function DashboardPage() {
  const { session } = useSession();
  const canSeeAudit = session?.workspace?.role !== "vendedor";
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Summary>("/api/dashboard/summary"), refetchInterval: 60000 });
  const audit = useQuery({
    queryKey: ["audit", 6],
    queryFn: () => api<AuditItem[]>("/api/audit?limit=6"),
    enabled: canSeeAudit,
  });
  const firstName = session?.user.full_name.split(" ")[0];

  if (summary.isLoading) return <Spinner />;
  const data = summary.data;
  return (
    <>
      <PageHeader
        title={`Resumen general${firstName ? `, ${firstName}` : ""}`}
        description={`Espacio de trabajo ${session?.workspace?.name}.`}
      />
      <TodayPanel />
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Card className="flex flex-col gap-3">
          <p className="text-[13px] font-medium text-ork-mist">Contactos en tu base</p>
          <p className="text-[28px] font-bold leading-none text-ork-ink">{(data?.contacts ?? 0).toLocaleString("es")}</p>
          <Link to="/contactos" className="self-start text-[12.5px] font-semibold text-ork-blue hover:underline">
            Ver contactos
          </Link>
        </Card>
        {KPIS.map(({ key, label, stage }) => {
          const value = data?.kpis[key];
          return (
            <Card key={key} className="flex flex-col gap-3">
              <p className="text-[13px] font-medium text-ork-mist">{label}</p>
              {key === "conversion_rate" && value === null ? (
                <>
                  <p className="text-[28px] font-bold leading-none text-ork-ink/25">—</p>
                  <span className="text-[12.5px] text-ork-mist">Aún no hay oportunidades cerradas</span>
                </>
              ) : value === null || value === undefined ? (
                <>
                  <p className="text-[28px] font-bold leading-none text-ork-ink/25">—</p>
                  <span className="self-start">
                    <Badge tone="violet">
                      <Clock3 className="size-3.5" aria-hidden /> Etapa {stage}
                    </Badge>
                  </span>
                </>
              ) : (
                <p className="text-[28px] font-bold leading-none text-ork-ink">
                  {value.toLocaleString("es")}
                  {key === "conversion_rate" ? " %" : ""}
                </p>
              )}
            </Card>
          );
        })}
      </div>

      <div className="mt-6 grid gap-6">
        <Card>
          <CardHeader title="Actividad reciente" description="Registro de auditoría de tu espacio." />
          {!canSeeAudit && <p className="text-[13.5px] text-ork-mist">Visible para administradores y supervisores.</p>}
          {canSeeAudit && audit.isLoading && <Spinner />}
          {canSeeAudit && audit.data && audit.data.length === 0 && (
            <p className="text-[13.5px] text-ork-mist">Sin actividad todavía.</p>
          )}
          <ul className="flex flex-col gap-3">
            {audit.data?.map((item) => (
              <li key={item.id} className="flex gap-3">
                <span className="ork-gradient mt-1.5 size-2 shrink-0 rounded-full" aria-hidden />
                <div>
                  <p className="text-[13.5px] text-ork-ink">
                    <strong>{item.actor}</strong> {item.label}
                  </p>
                  <p className="text-[12px] text-ork-mist">{formatWhen(item.at)}</p>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}

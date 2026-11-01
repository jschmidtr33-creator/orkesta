import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  CalendarClock,
  CheckCircle2,
  Circle,
  KanbanSquare,
  ListChecks,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  Search,
  Settings2,
  Table2,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type DragEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Avatar, Badge, Button, Card, cx, Modal, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { formatWhen } from "./dashboard";

export type Stage = { id: string; name: string; kind: "open" | "won" | "lost"; position: number; probability: number; deals: number; value: Record<string, number> };
export type Deal = {
  id: string;
  title: string;
  status: "open" | "won" | "lost";
  value: number;
  currency: string;
  expected_close: string | null;
  lost_reason: string | null;
  stage_id: string;
  stage: string;
  probability: number;
  contact: { id: string; full_name: string; job_title: string | null; email: string | null; phone: string | null } | null;
  company: string | null;
  owner: { id: string; name: string } | null;
  created_at: string;
  updated_at: string | null;
};
export type ActivityItem = {
  id: string;
  type: "note" | "call" | "meeting" | "email" | "task" | "stage" | "system";
  label: string;
  body: string;
  due_at: string | null;
  done_at: string | null;
  user: { id: string; name: string } | null;
  created_at: string;
  contact_name?: string | null;
  deal_title?: string | null;
  contact_id?: string | null;
  deal_id?: string | null;
};
type Metrics = {
  open: number;
  won: number;
  lost: number;
  conversion_rate: number | null;
  pipeline: Record<string, number>;
  weighted: Record<string, number>;
  won_value: Record<string, number>;
  overdue_tasks: number;
};

export function money(values: Record<string, number> | number, currency = "USD"): string {
  const entries = typeof values === "number" ? [[currency, values] as const] : Object.entries(values);
  if (!entries.length) return "0";
  return entries
    .map(([cur, v]) => new Intl.NumberFormat("es", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(v))
    .join(" · ");
}

export function useStages() {
  return useQuery({ queryKey: ["crm-stages"], queryFn: () => api<{ stages: Stage[]; currencies: string[] }>("/api/crm/stages") });
}

const KIND_TONE = { open: "blue", won: "ok", lost: "bad" } as const;

export function CrmPage() {
  const { session } = useSession();
  const isManager = session?.workspace?.role !== "vendedor";
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = params.get("vista") === "tabla" ? "tabla" : params.get("vista") === "tareas" ? "tareas" : "tablero";
  const openId = params.get("oportunidad");
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [editingStages, setEditingStages] = useState(false);
  const [lostFor, setLostFor] = useState<{ deal: Deal; stageId: string } | null>(null);
  const stages = useStages();
  const deals = useQuery({
    queryKey: ["crm-deals", q],
    queryFn: () => api<{ deals: Deal[]; metrics: Metrics }>(`/api/crm/deals?q=${encodeURIComponent(q)}`),
  });

  const move = useMutation({
    mutationFn: ({ id, stageId, reason }: { id: string; stageId: string; reason?: string }) =>
      api<Deal>(`/api/crm/deals/${id}/move`, { method: "POST", json: { stage_id: stageId, lost_reason: reason ?? "" } }),
    onMutate: async ({ id, stageId }) => {
      await client.cancelQueries({ queryKey: ["crm-deals", q] });
      const previous = client.getQueryData<{ deals: Deal[]; metrics: Metrics }>(["crm-deals", q]);
      if (previous) {
        client.setQueryData(["crm-deals", q], { ...previous, deals: previous.deals.map((d) => (d.id === id ? { ...d, stage_id: stageId } : d)) });
      }
      return { previous };
    },
    onError: (_e, _v, ctx) => ctx?.previous && client.setQueryData(["crm-deals", q], ctx.previous),
    onSettled: () => {
      client.invalidateQueries({ queryKey: ["crm-deals"] });
      client.invalidateQueries({ queryKey: ["crm-stages"] });
      client.invalidateQueries({ queryKey: ["crm-deal"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const requestMove = (deal: Deal, stageId: string) => {
    if (deal.stage_id === stageId) return;
    const target = stages.data?.stages.find((s) => s.id === stageId);
    if (target?.kind === "lost") setLostFor({ deal, stageId });
    else move.mutate({ id: deal.id, stageId });
  };

  const setView = (v: string) => {
    const next = new URLSearchParams(params);
    next.set("vista", v);
    setParams(next, { replace: true });
  };
  const openDeal = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("oportunidad", id);
    else next.delete("oportunidad");
    setParams(next, { replace: true });
  };

  const m = deals.data?.metrics;
  return (
    <>
      <PageHeader
        title="CRM"
        description="Tu pipeline comercial: oportunidades por etapa, historial de cada contacto y tareas de seguimiento."
        action={
          <div className="flex flex-wrap gap-2">
            {isManager && (
              <Button variant="ghost" icon={<Settings2 className="size-4" aria-hidden />} onClick={() => setEditingStages(true)}>
                Etapas
              </Button>
            )}
            <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setCreating(true)}>
              Nueva oportunidad
            </Button>
          </div>
        }
      />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Metric label="Oportunidades abiertas" value={m ? String(m.open) : "—"} />
        <Metric label="Valor del pipeline" value={m ? money(m.pipeline) : "—"} />
        <Metric label="Valor ponderado" value={m ? money(m.weighted) : "—"} hint="Valor × probabilidad de la etapa" />
        <Metric label="Tasa de conversión" value={m?.conversion_rate == null ? "—" : `${m.conversion_rate} %`} hint={m ? `${m.won} ganadas · ${m.lost} perdidas` : undefined} />
        <Metric label="Tareas vencidas" value={m ? String(m.overdue_tasks) : "—"} tone={m && m.overdue_tasks > 0 ? "bad" : undefined} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-xl border border-ork-line bg-white p-0.5" role="tablist">
          {(
            [
              ["tablero", "Tablero", <KanbanSquare key="k" className="size-4" aria-hidden />],
              ["tabla", "Tabla", <Table2 key="t" className="size-4" aria-hidden />],
              ["tareas", "Tareas", <ListChecks key="l" className="size-4" aria-hidden />],
            ] as const
          ).map(([value, label, icon]) => (
            <button
              key={value}
              role="tab"
              aria-selected={view === value}
              onClick={() => setView(value)}
              className={cx("ork-focus inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold", view === value ? "ork-gradient text-white" : "text-ork-graphite")}
            >
              {icon}
              {label}
            </button>
          ))}
        </div>
        {view !== "tareas" && (
          <label className="relative ml-auto">
            <span className="sr-only">Buscar oportunidades</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ork-mist" aria-hidden />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar por oportunidad, contacto o empresa"
              className="ork-focus h-10 w-[300px] max-w-full rounded-xl border border-ork-line bg-white pl-9 pr-3 text-[14px]"
            />
          </label>
        )}
      </div>

      {move.error && <div className="mb-3"><Alert>{move.error.message}</Alert></div>}
      {(stages.isLoading || deals.isLoading) && view !== "tareas" ? (
        <Card><Spinner /></Card>
      ) : view === "tablero" ? (
        <Board stages={stages.data?.stages ?? []} deals={deals.data?.deals ?? []} onOpen={openDeal} onMove={requestMove} />
      ) : view === "tabla" ? (
        <DealsTable stages={stages.data?.stages ?? []} deals={deals.data?.deals ?? []} onOpen={openDeal} onMove={requestMove} />
      ) : (
        <TasksView onOpenDeal={openDeal} isManager={isManager} />
      )}

      {!deals.isLoading && view !== "tareas" && (deals.data?.deals.length ?? 0) === 0 && !q && (
        <Card className="mt-4 text-center">
          <p className="text-[14px] text-ork-graphite">Aún no hay oportunidades. Crea la primera desde aquí o desde la ficha de un contacto.</p>
        </Card>
      )}

      <NewDealModal open={creating} onOpenChange={setCreating} stages={stages.data?.stages ?? []} currencies={stages.data?.currencies ?? ["USD"]} onCreated={(d) => openDeal(d.id)} />
      {isManager && <StagesModal open={editingStages} onOpenChange={setEditingStages} stages={stages.data?.stages ?? []} />}
      <LostReasonModal
        target={lostFor}
        onClose={() => setLostFor(null)}
        onConfirm={(reason) => {
          if (lostFor) move.mutate({ id: lostFor.deal.id, stageId: lostFor.stageId, reason });
          setLostFor(null);
        }}
      />
      <DealDrawer id={openId} onClose={() => openDeal(null)} stages={stages.data?.stages ?? []} currencies={stages.data?.currencies ?? ["USD"]} onMove={requestMove} />
    </>
  );
}

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "bad" }) {
  return (
    <Card className="flex flex-col gap-1.5 p-4">
      <p className="text-[12.5px] font-medium text-ork-mist">{label}</p>
      <p className={cx("truncate text-[20px] font-bold leading-tight", tone === "bad" ? "text-bad" : "text-ork-ink")} title={value}>
        {value}
      </p>
      {hint && <p className="text-[11.5px] text-ork-mist">{hint}</p>}
    </Card>
  );
}

// --- Tablero (Kanban) ---------------------------------------------------------------------

function Board({ stages, deals, onOpen, onMove }: { stages: Stage[]; deals: Deal[]; onOpen: (id: string) => void; onMove: (d: Deal, stageId: string) => void }) {
  const [over, setOver] = useState<string | null>(null);
  const byStage = useMemo(() => {
    const map = new Map<string, Deal[]>();
    for (const s of stages) map.set(s.id, []);
    for (const d of deals) map.get(d.stage_id)?.push(d);
    return map;
  }, [stages, deals]);
  const onDrop = (e: DragEvent, stageId: string) => {
    e.preventDefault();
    setOver(null);
    const id = e.dataTransfer.getData("text/plain");
    const deal = deals.find((d) => d.id === id);
    if (deal) onMove(deal, stageId);
  };
  return (
    <div className="flex gap-3 overflow-x-auto pb-3">
      {stages.map((s) => {
        const items = byStage.get(s.id) ?? [];
        const total = items.reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.currency]: (acc[d.currency] ?? 0) + d.value }), {});
        return (
          <section
            key={s.id}
            aria-label={`Etapa ${s.name}`}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(s.id);
            }}
            onDragLeave={() => setOver((o) => (o === s.id ? null : o))}
            onDrop={(e) => onDrop(e, s.id)}
            className={cx(
              "flex w-[272px] shrink-0 flex-col rounded-2xl border bg-ork-ink/[0.025] p-2.5 transition",
              over === s.id ? "border-ork-blue bg-ork-blue/5" : "border-transparent",
            )}
          >
            <header className="mb-2 px-1">
              <div className="flex items-center justify-between gap-2">
                <h3 className="truncate text-[13.5px] font-bold text-ork-ink">{s.name}</h3>
                <Badge tone={KIND_TONE[s.kind]}>{items.length}</Badge>
              </div>
              <p className="text-[12px] text-ork-mist">
                {money(total)} · {s.probability} %
              </p>
            </header>
            <div className="flex min-h-[80px] flex-col gap-2">
              {items.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData("text/plain", d.id);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onClick={() => onOpen(d.id)}
                  className="ork-focus cursor-grab rounded-xl border border-ork-line bg-white p-3 text-left shadow-[0_1px_2px_rgb(8_11_18/0.04)] transition hover:border-ork-blue/40 active:cursor-grabbing"
                >
                  <p className="text-[13.5px] font-semibold leading-snug text-ork-ink">{d.title}</p>
                  {(d.contact || d.company) && (
                    <p className="mt-1 truncate text-[12px] text-ork-mist">{[d.contact?.full_name, d.company].filter(Boolean).join(" · ")}</p>
                  )}
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <span className="text-[13px] font-semibold text-ork-graphite">{money(d.value, d.currency)}</span>
                    <span className="flex items-center gap-1.5">
                      {d.expected_close && <span className="text-[11px] text-ork-mist">{new Date(d.expected_close + "T00:00").toLocaleDateString("es", { day: "numeric", month: "short" })}</span>}
                      {d.owner && <Avatar name={d.owner.name} email="" size={22} />}
                    </span>
                  </div>
                </button>
              ))}
              {items.length === 0 && <p className="rounded-xl border border-dashed border-ork-line px-3 py-6 text-center text-[12px] text-ork-mist">Arrastra aquí</p>}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function DealsTable({ stages, deals, onOpen, onMove }: { stages: Stage[]; deals: Deal[]; onOpen: (id: string) => void; onMove: (d: Deal, stageId: string) => void }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-left text-[13.5px]">
          <thead className="border-b border-ork-line bg-ork-ink/[0.02] text-[12.5px] text-ork-graphite">
            <tr>
              <th className="px-4 py-3 font-semibold">Oportunidad</th>
              <th className="px-3 py-3 font-semibold">Contacto</th>
              <th className="px-3 py-3 font-semibold">Etapa</th>
              <th className="px-3 py-3 text-right font-semibold">Valor</th>
              <th className="px-3 py-3 font-semibold">Cierre esperado</th>
              <th className="px-4 py-3 font-semibold">Responsable</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ork-line">
            {deals.map((d) => (
              <tr key={d.id} className="hover:bg-ork-ink/[0.02]">
                <td className="px-4 py-3">
                  <button type="button" onClick={() => onOpen(d.id)} className="ork-focus rounded text-left font-semibold text-ork-ink hover:text-ork-blue">
                    {d.title}
                  </button>
                  {d.company && <p className="text-[12px] text-ork-mist">{d.company}</p>}
                </td>
                <td className="px-3 py-3 text-ork-graphite">{d.contact?.full_name ?? "—"}</td>
                <td className="px-3 py-3">
                  <select
                    aria-label={`Etapa de ${d.title}`}
                    value={d.stage_id}
                    onChange={(e) => onMove(d, e.target.value)}
                    className="ork-focus h-8 rounded-lg border border-ork-line bg-white px-2 text-[13px]"
                  >
                    {stages.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-3 py-3 text-right font-semibold text-ork-graphite">{money(d.value, d.currency)}</td>
                <td className="px-3 py-3 text-ork-graphite">{d.expected_close ? new Date(d.expected_close + "T00:00").toLocaleDateString("es") : "—"}</td>
                <td className="px-4 py-3 text-ork-graphite">{d.owner?.name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// --- Tareas -------------------------------------------------------------------------------

function TasksView({ onOpenDeal, isManager }: { onOpenDeal: (id: string) => void; isManager: boolean }) {
  const client = useQueryClient();
  const [team, setTeam] = useState(false);
  const [done, setDone] = useState(false);
  const tasks = useQuery({
    queryKey: ["crm-tasks", team, done],
    queryFn: () => api<ActivityItem[]>(`/api/crm/tasks?equipo=${team}&todas=${done}`),
  });
  const toggle = useMutation({
    mutationFn: (id: string) => api(`/api/crm/activities/${id}/done`, { method: "POST" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["crm-tasks"] });
      client.invalidateQueries({ queryKey: ["crm-deals"] });
    },
  });
  return (
    <Card>
      <div className="mb-3 flex flex-wrap gap-4 text-[13px] text-ork-graphite">
        {isManager && (
          <label className="flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-ork-blue" checked={team} onChange={(e) => setTeam(e.target.checked)} /> Tareas de todo el equipo
          </label>
        )}
        <label className="flex items-center gap-2">
          <input type="checkbox" className="size-4 accent-ork-blue" checked={done} onChange={(e) => setDone(e.target.checked)} /> Mostrar completadas
        </label>
      </div>
      {tasks.isLoading ? (
        <Spinner />
      ) : !tasks.data?.length ? (
        <p className="py-8 text-center text-[13.5px] text-ork-mist">No tienes tareas pendientes. Créalas desde una oportunidad o un contacto.</p>
      ) : (
        <ul className="divide-y divide-ork-line">
          {tasks.data.map((t) => {
            const overdue = !t.done_at && t.due_at && new Date(t.due_at) < new Date();
            return (
              <li key={t.id} className="flex items-start gap-3 py-3">
                <button type="button" onClick={() => toggle.mutate(t.id)} aria-label={t.done_at ? "Marcar como pendiente" : "Marcar como hecha"} className="ork-focus mt-0.5 rounded-full text-ork-mist hover:text-ok">
                  {t.done_at ? <CheckCircle2 className="size-5 text-ok" /> : <Circle className="size-5" />}
                </button>
                <div className="min-w-0 flex-1">
                  <p className={cx("text-[14px] text-ork-ink", t.done_at && "text-ork-mist line-through")}>{t.body}</p>
                  <p className="text-[12px] text-ork-mist">
                    {t.due_at && <span className={cx(overdue && "font-semibold text-bad")}>Vence {formatWhen(t.due_at)}</span>}
                    {t.contact_name && ` · ${t.contact_name}`}
                    {t.deal_title && (
                      <>
                        {" · "}
                        {t.deal_id ? (
                          <button type="button" className="font-semibold text-ork-blue hover:underline" onClick={() => onOpenDeal(t.deal_id!)}>
                            {t.deal_title}
                          </button>
                        ) : (
                          t.deal_title
                        )}
                      </>
                    )}
                    {t.user && ` · ${t.user.name}`}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

// --- Línea de tiempo (también se usa en la ficha del contacto) ------------------------------

const ACTIVITY_ICON: Record<string, ReactNode> = {
  note: <MessageSquare className="size-3.5" aria-hidden />,
  call: <Phone className="size-3.5" aria-hidden />,
  meeting: <Users className="size-3.5" aria-hidden />,
  email: <Mail className="size-3.5" aria-hidden />,
  task: <CalendarClock className="size-3.5" aria-hidden />,
  stage: <KanbanSquare className="size-3.5" aria-hidden />,
  system: <Plus className="size-3.5" aria-hidden />,
};

export function Timeline({ contactId, dealId }: { contactId?: string; dealId?: string }) {
  const client = useQueryClient();
  const key = ["crm-timeline", contactId ?? null, dealId ?? null];
  const params = dealId ? `oportunidad=${dealId}` : `contacto=${contactId}`;
  const items = useQuery({ queryKey: key, queryFn: () => api<ActivityItem[]>(`/api/crm/timeline?${params}`) });
  const [type, setType] = useState<"note" | "call" | "meeting" | "email" | "task">("note");
  const [body, setBody] = useState("");
  const [due, setDue] = useState("");
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["crm-timeline"] });
    client.invalidateQueries({ queryKey: ["crm-deal"] });
    client.invalidateQueries({ queryKey: ["crm-tasks"] });
    client.invalidateQueries({ queryKey: ["crm-deals"] });
  };
  const add = useMutation({
    mutationFn: () =>
      api("/api/crm/activities", {
        method: "POST",
        json: { type, body, contact_id: dealId ? null : contactId, deal_id: dealId ?? null, due_at: type === "task" && due ? new Date(due).toISOString() : null },
      }),
    onSuccess: () => {
      setBody("");
      setDue("");
      refresh();
    },
  });
  const toggle = useMutation({ mutationFn: (id: string) => api(`/api/crm/activities/${id}/done`, { method: "POST" }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/crm/activities/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const types = [
    ["note", "Nota"],
    ["call", "Llamada"],
    ["meeting", "Reunión"],
    ["email", "Correo"],
    ["task", "Tarea"],
  ] as const;
  return (
    <div>
      <form
        className="rounded-xl border border-ork-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <div className="mb-2 flex flex-wrap gap-1">
          {types.map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setType(value)}
              className={cx("ork-focus inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12px] font-semibold", type === value ? "bg-ork-blue/10 text-ork-blue" : "text-ork-graphite hover:bg-ork-ink/5")}
            >
              {ACTIVITY_ICON[value]} {label}
            </button>
          ))}
        </div>
        <textarea
          aria-label="Detalle de la actividad"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={2}
          placeholder={type === "task" ? "¿Qué hay que hacer?" : type === "call" ? "Resumen de la llamada" : type === "meeting" ? "Acuerdos de la reunión" : "Escribe una nota"}
          className="ork-focus w-full resize-y rounded-lg border border-ork-line px-3 py-2 text-[13.5px]"
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          {type === "task" ? (
            <label className="flex items-center gap-2 text-[12.5px] text-ork-graphite">
              Vence
              <input type="datetime-local" required value={due} onChange={(e) => setDue(e.target.value)} className="ork-focus h-8 rounded-lg border border-ork-line px-2 text-[12.5px]" />
            </label>
          ) : (
            <span />
          )}
          <Button size="sm" type="submit" loading={add.isPending} disabled={!body.trim()}>
            Registrar
          </Button>
        </div>
        {add.error && <p className="mt-2 text-[12.5px] text-bad">{add.error.message}</p>}
      </form>
      <ol className="mt-4 space-y-3">
        {items.isLoading && <Spinner />}
        {items.data?.length === 0 && <p className="text-[13px] text-ork-mist">Todavía no hay actividad registrada.</p>}
        {items.data?.map((a) => (
          <li key={a.id} className="flex gap-3">
            <span className={cx("mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full", a.type === "stage" || a.type === "system" ? "bg-ork-ink/5 text-ork-mist" : "bg-ork-blue/10 text-ork-blue")}>
              {ACTIVITY_ICON[a.type]}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] text-ork-mist">
                <strong className="font-semibold text-ork-graphite">{a.label}</strong> · {a.user?.name ?? "ORKESTA"} · {formatWhen(a.created_at)}
              </p>
              <p className={cx("whitespace-pre-wrap text-[13.5px] text-ork-ink", a.done_at && "text-ork-mist line-through")}>{a.body}</p>
              {a.type === "task" && (
                <div className="mt-1 flex items-center gap-3 text-[12px]">
                  {a.due_at && <span className={cx(!a.done_at && new Date(a.due_at) < new Date() ? "font-semibold text-bad" : "text-ork-mist")}>Vence {formatWhen(a.due_at)}</span>}
                  <button type="button" className="font-semibold text-ork-blue hover:underline" onClick={() => toggle.mutate(a.id)}>
                    {a.done_at ? "Reabrir" : "Marcar como hecha"}
                  </button>
                </div>
              )}
            </div>
            {a.type !== "stage" && a.type !== "system" && (
              <button type="button" aria-label="Borrar actividad" onClick={() => remove.mutate(a.id)} className="ork-focus h-7 rounded p-1 text-ork-mist hover:text-bad">
                <Trash2 className="size-3.5" />
              </button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

// --- Ficha de la oportunidad ----------------------------------------------------------------

function DealDrawer({ id, onClose, stages, currencies, onMove }: { id: string | null; onClose: () => void; stages: Stage[]; currencies: string[]; onMove: (d: Deal, s: string) => void }) {
  const client = useQueryClient();
  const deal = useQuery({ queryKey: ["crm-deal", id], queryFn: () => api<Deal>(`/api/crm/deals/${id}`), enabled: Boolean(id) });
  const d = deal.data;
  const [form, setForm] = useState({ title: "", value: "", currency: "USD", expected_close: "" });
  useEffect(() => {
    if (d) setForm({ title: d.title, value: String(d.value), currency: d.currency, expected_close: d.expected_close ?? "" });
  }, [d]);
  const save = useMutation({
    mutationFn: () =>
      api<Deal>(`/api/crm/deals/${id}`, {
        method: "PATCH",
        json: { title: form.title, value: Number(form.value || 0), currency: form.currency, expected_close: form.expected_close || null },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["crm-deal"] });
      client.invalidateQueries({ queryKey: ["crm-deals"] });
      client.invalidateQueries({ queryKey: ["crm-stages"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/crm/deals/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["crm-deals"] });
      client.invalidateQueries({ queryKey: ["crm-stages"] });
      onClose();
    },
  });
  const dirty = d && (form.title !== d.title || Number(form.value || 0) !== d.value || form.currency !== d.currency || (form.expected_close || null) !== d.expected_close);
  return (
    <Dialog.Root open={Boolean(id)} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ork-ink/40" />
        <Dialog.Content className="fixed inset-y-0 right-0 z-50 flex w-[min(100vw,560px)] flex-col bg-white shadow-[var(--shadow-pop)]">
          <div className="flex items-start justify-between gap-3 border-b border-ork-line p-5">
            <div className="min-w-0">
              <Dialog.Title className="truncate text-[18px] font-bold text-ork-ink">{d?.title ?? "Oportunidad"}</Dialog.Title>
              <Dialog.Description className="text-[13px] text-ork-mist">{d ? [d.company, d.owner?.name].filter(Boolean).join(" · ") || "Sin empresa" : "Cargando…"}</Dialog.Description>
            </div>
            <Dialog.Close className="ork-focus rounded-lg p-1 text-ork-mist hover:bg-ork-ink/5" aria-label="Cerrar">
              <X className="size-5" />
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-5">
            {deal.isLoading && <Spinner />}
            {deal.error && <Alert>{deal.error.message}</Alert>}
            {d && (
              <div className="flex flex-col gap-5">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={KIND_TONE[d.status]}>{d.status === "open" ? "Abierta" : d.status === "won" ? "Ganada" : "Perdida"}</Badge>
                  <select
                    aria-label="Etapa"
                    value={d.stage_id}
                    onChange={(e) => onMove(d, e.target.value)}
                    className="ork-focus h-9 rounded-lg border border-ork-line bg-white px-2 text-[13.5px]"
                  >
                    {stages.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name} ({s.probability} %)
                      </option>
                    ))}
                  </select>
                  {d.lost_reason && <span className="text-[12.5px] text-bad">Motivo: {d.lost_reason}</span>}
                </div>
                <form
                  className="grid grid-cols-2 gap-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    save.mutate();
                  }}
                >
                  <TextField label="Nombre" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="col-span-2" />
                  <TextField label="Valor" inputMode="decimal" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value.replace(/[^\d.]/g, "") })} />
                  <SelectField label="Moneda" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
                    {currencies.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </SelectField>
                  <TextField label="Cierre esperado" type="date" value={form.expected_close} onChange={(e) => setForm({ ...form, expected_close: e.target.value })} />
                  <div className="flex items-end justify-end">
                    <Button type="submit" size="sm" disabled={!dirty} loading={save.isPending}>
                      Guardar cambios
                    </Button>
                  </div>
                  {save.error && <p className="col-span-2 text-[12.5px] text-bad">{save.error.message}</p>}
                </form>
                {d.contact && (
                  <div className="rounded-xl border border-ork-line p-3">
                    <p className="text-[12px] font-semibold uppercase tracking-wide text-ork-mist">Contacto</p>
                    <div className="mt-2 flex items-center gap-3">
                      <Avatar name={d.contact.full_name} email={d.contact.email ?? ""} size={36} />
                      <div className="min-w-0">
                        <Link to={`/contactos?q=${encodeURIComponent(d.contact.full_name)}`} className="font-semibold text-ork-ink hover:text-ork-blue">
                          {d.contact.full_name}
                        </Link>
                        <p className="truncate text-[12.5px] text-ork-mist">{[d.contact.job_title, d.contact.email, d.contact.phone].filter(Boolean).join(" · ")}</p>
                      </div>
                    </div>
                  </div>
                )}
                <div>
                  <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ork-mist">Actividad</p>
                  <Timeline dealId={d.id} />
                </div>
                <div className="border-t border-ork-line pt-4">
                  <Button
                    size="sm"
                    variant="danger"
                    icon={<Trash2 className="size-3.5" aria-hidden />}
                    loading={remove.isPending}
                    onClick={() => window.confirm("¿Eliminar esta oportunidad y su actividad?") && remove.mutate()}
                  >
                    Eliminar oportunidad
                  </Button>
                  {remove.error && <p className="mt-2 text-[12.5px] text-bad">{remove.error.message}</p>}
                </div>
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// --- Modales ------------------------------------------------------------------------------

type ContactHit = { id: string; full_name: string; company: string | null; job_title: string | null };

export function NewDealModal({
  open,
  onOpenChange,
  stages,
  currencies,
  onCreated,
  contact,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  stages: Stage[];
  currencies: string[];
  onCreated?: (d: Deal) => void;
  contact?: ContactHit;
}) {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<ContactHit | null>(contact ?? null);
  const [form, setForm] = useState({ title: "", value: "", currency: "USD", stage_id: "", expected_close: "" });
  useEffect(() => {
    if (open) {
      setPicked(contact ?? null);
      setForm({ title: "", value: "", currency: "USD", stage_id: "", expected_close: "" });
      setSearch("");
    }
  }, [open, contact]);
  const hits = useQuery({
    queryKey: ["contact-search", search],
    queryFn: () => api<{ items: ContactHit[] }>(`/api/contacts?q=${encodeURIComponent(search)}&tamano=25`),
    enabled: open && !picked && search.trim().length >= 2,
  });
  const create = useMutation({
    mutationFn: () =>
      api<Deal>("/api/crm/deals", {
        method: "POST",
        json: {
          title: form.title || null,
          contact_id: picked?.id ?? null,
          value: Number(form.value || 0),
          currency: form.currency,
          stage_id: form.stage_id || null,
          expected_close: form.expected_close || null,
        },
      }),
    onSuccess: (d) => {
      client.invalidateQueries({ queryKey: ["crm-deals"] });
      client.invalidateQueries({ queryKey: ["crm-stages"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      onOpenChange(false);
      onCreated?.(d);
    },
  });
  const openStages = stages.filter((s) => s.kind === "open");
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Nueva oportunidad" description="Asóciala a un contacto para ver todo su historial en un solo lugar.">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        {create.error && <Alert>{create.error.message}</Alert>}
        {picked ? (
          <div className="flex items-center justify-between rounded-xl border border-ork-line px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-[14px] font-semibold text-ork-ink">{picked.full_name}</p>
              <p className="truncate text-[12px] text-ork-mist">{[picked.job_title, picked.company].filter(Boolean).join(" · ")}</p>
            </div>
            {!contact && (
              <button type="button" className="text-[12.5px] font-semibold text-ork-blue" onClick={() => setPicked(null)}>
                Cambiar
              </button>
            )}
          </div>
        ) : (
          <div>
            <TextField label="Contacto" placeholder="Busca por nombre o empresa" value={search} onChange={(e) => setSearch(e.target.value)} />
            {hits.data && (
              <ul className="mt-1 max-h-44 overflow-y-auto rounded-xl border border-ork-line">
                {hits.data.items.length === 0 && <li className="px-3 py-2 text-[12.5px] text-ork-mist">Sin resultados</li>}
                {hits.data.items.map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => setPicked(c)} className="w-full px-3 py-2 text-left text-[13px] hover:bg-ork-ink/[0.03]">
                      <span className="font-semibold text-ork-ink">{c.full_name}</span>
                      <span className="text-ork-mist"> · {[c.job_title, c.company].filter(Boolean).join(" · ")}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-[12px] text-ork-mist">Opcional: también puedes crearla sin contacto.</p>
          </div>
        )}
        <TextField label="Nombre de la oportunidad" placeholder="Si lo dejas vacío usamos la empresa del contacto" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <div className="grid grid-cols-2 gap-3">
          <TextField label="Valor" inputMode="decimal" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value.replace(/[^\d.]/g, "") })} />
          <SelectField label="Moneda" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
            {currencies.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </SelectField>
          <SelectField label="Etapa" value={form.stage_id} onChange={(e) => setForm({ ...form, stage_id: e.target.value })}>
            <option value="">{openStages[0]?.name ?? "Primera etapa"}</option>
            {openStages.slice(1).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </SelectField>
          <TextField label="Cierre esperado" type="date" value={form.expected_close} onChange={(e) => setForm({ ...form, expected_close: e.target.value })} />
        </div>
        <Button type="submit" loading={create.isPending}>
          Crear oportunidad
        </Button>
      </form>
    </Modal>
  );
}

function LostReasonModal({ target, onClose, onConfirm }: { target: { deal: Deal } | null; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  useEffect(() => setReason(""), [target]);
  return (
    <Modal open={Boolean(target)} onOpenChange={(o) => !o && onClose()} title="Marcar como perdida" description={target ? `«${target.deal.title}»` : undefined}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          onConfirm(reason);
        }}
      >
        <TextField label="Motivo" placeholder="Presupuesto, eligió a la competencia, sin respuesta…" value={reason} onChange={(e) => setReason(e.target.value)} />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" variant="danger">
            Marcar como perdida
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function StagesModal({ open, onOpenChange, stages }: { open: boolean; onOpenChange: (o: boolean) => void; stages: Stage[] }) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const done = (r: unknown) => {
    setError(null);
    client.setQueryData(["crm-stages"], (old: { stages: Stage[]; currencies: string[] } | undefined) => ({ currencies: old?.currencies ?? ["USD"], ...(r as { stages: Stage[] }) }));
    client.invalidateQueries({ queryKey: ["crm-deals"] });
  };
  const fail = (e: Error) => setError(e.message);
  const add = useMutation({ mutationFn: () => api("/api/crm/stages", { method: "POST", json: { name, probability: 50 } }), onSuccess: (r) => (setName(""), done(r)), onError: fail });
  const patch = useMutation({
    mutationFn: ({ id, ...body }: { id: string; name?: string; probability?: number }) => api(`/api/crm/stages/${id}`, { method: "PATCH", json: body }),
    onSuccess: done,
    onError: fail,
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/crm/stages/${id}`, { method: "DELETE" }), onSuccess: done, onError: fail });
  const order = useMutation({ mutationFn: (ids: string[]) => api("/api/crm/stages/order", { method: "POST", json: { ids } }), onSuccess: done, onError: fail });
  const swap = (i: number, j: number) => {
    const ids = stages.map((s) => s.id);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    order.mutate(ids);
  };
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Etapas del pipeline" description="Renombra, ordena o ajusta la probabilidad de cierre de cada etapa.">
      <div className="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
        {error && <Alert>{error}</Alert>}
        {stages.map((s, i) => (
          <div key={s.id} className="flex items-center gap-2 rounded-xl border border-ork-line p-2">
            <div className="flex flex-col">
              <button type="button" aria-label="Subir" disabled={i === 0} onClick={() => swap(i, i - 1)} className="rounded p-0.5 text-ork-mist hover:text-ork-ink disabled:opacity-30">
                <ArrowUp className="size-3.5" />
              </button>
              <button type="button" aria-label="Bajar" disabled={i === stages.length - 1} onClick={() => swap(i, i + 1)} className="rounded p-0.5 text-ork-mist hover:text-ork-ink disabled:opacity-30">
                <ArrowDown className="size-3.5" />
              </button>
            </div>
            <input
              aria-label="Nombre de la etapa"
              defaultValue={s.name}
              onBlur={(e) => e.target.value.trim() !== s.name && patch.mutate({ id: s.id, name: e.target.value })}
              className="ork-focus h-9 min-w-0 flex-1 rounded-lg border border-ork-line px-2 text-[13.5px]"
            />
            <label className="flex items-center gap-1 text-[12px] text-ork-mist">
              <input
                aria-label="Probabilidad"
                type="number"
                min={0}
                max={100}
                defaultValue={s.probability}
                onBlur={(e) => Number(e.target.value) !== s.probability && patch.mutate({ id: s.id, probability: Number(e.target.value) })}
                className="ork-focus h-9 w-16 rounded-lg border border-ork-line px-2 text-[13px]"
              />
              %
            </label>
            <Badge tone={KIND_TONE[s.kind]}>{s.kind === "open" ? "Abierta" : s.kind === "won" ? "Ganada" : "Perdida"}</Badge>
            <button type="button" aria-label={`Eliminar ${s.name}`} onClick={() => remove.mutate(s.id)} className="ork-focus rounded p-1 text-ork-mist hover:text-bad">
              <Trash2 className="size-4" />
            </button>
          </div>
        ))}
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <input
          aria-label="Nueva etapa"
          placeholder="Nueva etapa (p. ej. Demo técnica)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="ork-focus h-10 flex-1 rounded-xl border border-ork-line px-3 text-[14px]"
        />
        <Button type="submit" loading={add.isPending} disabled={name.trim().length < 2}>
          Agregar
        </Button>
      </form>
    </Modal>
  );
}

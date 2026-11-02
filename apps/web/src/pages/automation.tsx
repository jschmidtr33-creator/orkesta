import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, Pencil, Plus, Sparkles, Trash2, X, XCircle, Zap } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert, Badge, Button, Card, CardHeader, cx, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
import { api } from "../lib/api";
import { formatWhen } from "./dashboard";

type Option = { value: string; label: string; status?: string };
type Action = { type: string; title?: string; text?: string; due_hours?: number; stage_id?: string; value?: number; status?: string; list_id?: string; campaign_id?: string };
type Rule = {
  id: string;
  name: string;
  enabled: boolean;
  trigger: string;
  conditions: Record<string, string | number>;
  actions: Action[];
  run_count: number;
  last_run_at: string | null;
};
type Catalog = {
  items: Rule[];
  triggers: Record<string, { label: string; group: string; conditions: string[] }>;
  actions: Record<string, { label: string; fields: string[] }>;
  recipes: { key: string; name: string; trigger: string; installed: boolean }[];
  options: { stages: Option[]; campaigns: Option[]; lists: Option[]; statuses: Option[]; sources: Option[] };
  can_edit: boolean;
};
type RunRow = {
  id: string;
  rule: string;
  trigger: string;
  status: "ok" | "error" | "skipped";
  contact: { id: string; full_name: string } | null;
  results: { type?: string; ok: boolean; detail: string }[];
  created_at: string;
};

const VARS = "Puedes usar {{nombre}}, {{empresa}}, {{cargo}}.";

export function AutomationPage() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["automations"], queryFn: () => api<Catalog>("/api/automations") });
  const runs = useQuery({ queryKey: ["automation-runs"], queryFn: () => api<RunRow[]>("/api/automations/runs"), refetchInterval: 20000 });
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["automations"] });
    client.invalidateQueries({ queryKey: ["automation-runs"] });
  };
  const recipe = useMutation({ mutationFn: (key: string) => api(`/api/automations/recipes/${key}`, { method: "POST" }), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: (r: Rule) => api(`/api/automations/${r.id}`, { method: "PATCH", json: { enabled: !r.enabled } }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/automations/${id}`, { method: "DELETE" }), onSuccess: refresh });
  const c = q.data;
  if (q.isLoading) return <Spinner />;
  if (!c) return <Alert>{q.error?.message ?? "No pudimos cargar las automatizaciones."}</Alert>;
  return (
    <div>
      <PageHeader
        title="Automatización"
        description="Reglas que trabajan por ti: cuando pasa algo con un contacto, una campaña o una oportunidad, ORKESTA hace lo que definas."
        action={
          c.can_edit && (
            <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setEditing("new")}>
              Nueva regla
            </Button>
          )
        }
      />
      {(recipe.error || toggle.error || remove.error) && (
        <div className="mb-4">
          <Alert>{(recipe.error ?? toggle.error ?? remove.error)?.message}</Alert>
        </div>
      )}
      {editing && <RuleEditor catalog={c} rule={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refresh} />}

      <Card className="mb-5">
        <CardHeader title="Recetas listas" description="Actívalas con un clic y ajústalas después." />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {c.recipes.map((r) => (
            <div key={r.key} className="flex flex-col justify-between gap-3 rounded-xl border border-ork-line p-4">
              <div className="flex items-start gap-2.5">
                <Sparkles className="mt-0.5 size-4 shrink-0 text-ork-violet" aria-hidden />
                <div>
                  <p className="text-[14px] font-semibold text-ork-ink">{r.name}</p>
                  <p className="text-[12px] text-ork-mist">{c.triggers[r.trigger]?.group}</p>
                </div>
              </div>
              {r.installed ? (
                <Badge tone="ok">
                  <CheckCircle2 className="size-3.5" aria-hidden /> Activa
                </Badge>
              ) : (
                c.can_edit && (
                  <Button size="sm" variant="secondary" loading={recipe.isPending && recipe.variables === r.key} onClick={() => recipe.mutate(r.key)}>
                    Activar
                  </Button>
                )
              )}
            </div>
          ))}
        </div>
      </Card>

      <Card className="mb-5">
        <CardHeader title={`Mis reglas (${c.items.length})`} description="Se ejecutan solas, en segundo plano, apenas ocurre el evento." />
        {c.items.length === 0 ? (
          <p className="py-6 text-center text-[13.5px] text-ork-mist">Aún no tienes reglas. Activa una receta o crea la tuya.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {c.items.map((r) => (
              <li key={r.id} className={cx("rounded-xl border border-ork-line p-4", !r.enabled && "opacity-60")}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[14.5px] font-semibold text-ork-ink">{r.name}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[13px] text-ork-graphite">
                      <Zap className="size-3.5 text-ork-blue" aria-hidden />
                      <span>
                        Cuando: <strong>{describeTrigger(c, r)}</strong>
                      </span>
                      <ArrowRight className="size-3.5 text-ork-mist" aria-hidden />
                      <span>{r.actions.map((a) => describeAction(c, a)).join(" · ")}</span>
                    </p>
                    <p className="mt-1 text-[12px] text-ork-mist">
                      {r.run_count} ejecuciones{r.last_run_at ? ` · última ${formatWhen(r.last_run_at)}` : ""}
                    </p>
                  </div>
                  {c.can_edit && (
                    <div className="flex items-center gap-1">
                      <Switch on={r.enabled} onClick={() => toggle.mutate(r)} label={r.enabled ? "Desactivar" : "Activar"} />
                      <Button size="sm" variant="ghost" aria-label="Editar" icon={<Pencil className="size-4" aria-hidden />} onClick={() => setEditing(r)} />
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label="Eliminar"
                        icon={<Trash2 className="size-4" aria-hidden />}
                        onClick={() => window.confirm(`¿Eliminar «${r.name}»?`) && remove.mutate(r.id)}
                      />
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-0">
        <div className="px-5 pt-5">
          <h2 className="text-[15px] font-bold text-ork-ink">Historial</h2>
          <p className="text-[13px] text-ork-mist">Las últimas 100 ejecuciones y lo que hizo cada una.</p>
        </div>
        {(runs.data ?? []).length === 0 ? (
          <p className="px-5 py-8 text-center text-[13px] text-ork-mist">Todavía no se ha ejecutado ninguna regla.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[13px]">
              <thead className="border-y border-ork-line text-[11.5px] uppercase tracking-wide text-ork-mist">
                <tr>
                  <th className="px-5 py-2 font-semibold">Cuándo</th>
                  <th className="py-2 font-semibold">Regla</th>
                  <th className="py-2 font-semibold">Contacto</th>
                  <th className="px-5 py-2 font-semibold">Resultado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ork-line">
                {(runs.data ?? []).map((r) => (
                  <tr key={r.id} className="align-top">
                    <td className="whitespace-nowrap px-5 py-2.5 text-ork-mist">{formatWhen(r.created_at)}</td>
                    <td className="py-2.5 pr-3 font-semibold text-ork-ink">{r.rule}</td>
                    <td className="py-2.5 pr-3">
                      {r.contact ? (
                        <Link to={`/contactos?abrir=${r.contact.id}`} className="text-ork-blue hover:underline">
                          {r.contact.full_name}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-5 py-2.5">
                      <ul className="flex flex-col gap-0.5">
                        {r.results.map((x, i) => (
                          <li key={i} className={cx("flex items-start gap-1", x.ok ? "text-ork-graphite" : "text-bad")}>
                            {x.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-ok" aria-hidden /> : <XCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />}
                            {x.detail}
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function Switch({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      className={cx("ork-focus relative h-6 w-11 rounded-full transition", on ? "bg-ok" : "bg-ork-ink/20")}
    >
      <span className={cx("absolute top-0.5 size-5 rounded-full bg-ork-card shadow transition-all", on ? "left-[22px]" : "left-0.5")} />
    </button>
  );
}

const label = (opts: Option[], v: unknown) => opts.find((o) => o.value === v)?.label ?? "—";

function describeTrigger(c: Catalog, r: Rule): string {
  const t = c.triggers[r.trigger]?.label ?? r.trigger;
  const parts: string[] = [];
  if (r.conditions.campaign_id) parts.push(`«${label(c.options.campaigns, r.conditions.campaign_id)}»`);
  if (r.conditions.min_opens) parts.push(`${r.conditions.min_opens} veces`);
  if (r.conditions.stage_id) parts.push(`«${label(c.options.stages, r.conditions.stage_id)}»`);
  if (r.conditions.source) parts.push(`desde ${label(c.options.sources, r.conditions.source)}`);
  if (r.trigger === "campaign.finished") parts.push(`y pasan ${r.conditions.wait_days ?? 3} días`);
  return [t, ...parts].join(" ");
}

function describeAction(c: Catalog, a: Action): string {
  switch (a.type) {
    case "create_task":
      return `Tarea «${a.title}» (${a.due_hours} h)`;
    case "create_deal":
      return `Crear oportunidad en «${label(c.options.stages, a.stage_id)}»`;
    case "move_deal":
      return `Mover a «${label(c.options.stages, a.stage_id)}»`;
    case "set_status":
      return `Estado: ${label(c.options.statuses, a.status)}`;
    case "add_to_list":
      return `Lista «${label(c.options.lists, a.list_id)}»`;
    case "add_to_campaign":
      return `Campaña «${label(c.options.campaigns, a.campaign_id)}»`;
    case "add_note":
      return "Nota en el historial";
    case "notify_email":
      return "Avisarme por correo";
    default:
      return c.actions[a.type]?.label ?? a.type;
  }
}

function RuleEditor({ catalog: c, rule, onClose, onSaved }: { catalog: Catalog; rule: Rule | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(rule?.name ?? "");
  const [trigger, setTrigger] = useState(rule?.trigger ?? "campaign.replied");
  const [conditions, setConditions] = useState<Record<string, string | number>>(rule?.conditions ?? {});
  const [actions, setActions] = useState<Action[]>(rule?.actions ?? [{ type: "create_task", title: "Seguimiento a {{nombre}}", due_hours: 24 }]);
  const save = useMutation({
    mutationFn: () =>
      api(rule ? `/api/automations/${rule.id}` : "/api/automations", {
        method: rule ? "PUT" : "POST",
        json: { name, trigger, conditions, actions, enabled: rule?.enabled ?? true },
      }),
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  const groups = Object.entries(c.triggers).reduce<Record<string, [string, string][]>>((acc, [k, t]) => {
    (acc[t.group] ??= []).push([k, t.label]);
    return acc;
  }, {});
  const fields = c.triggers[trigger]?.conditions ?? [];
  const setCond = (k: string, v: string | number) => setConditions((s) => ({ ...s, [k]: v }));
  const setAction = (i: number, patch: Partial<Action>) => setActions((s) => s.map((a, n) => (n === i ? { ...a, ...patch } : a)));
  return (
    <Card className="mb-5 border-ork-blue/40">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[16px] font-bold text-ork-ink">{rule ? "Editar regla" : "Nueva regla"}</h2>
          <p className="text-[13px] text-ork-mist">Elige qué la activa y qué debe hacer ORKESTA.</p>
        </div>
        <button type="button" onClick={onClose} className="ork-focus rounded-lg p-1 text-ork-mist hover:bg-ork-ink/5" aria-label="Cerrar">
          <X className="size-5" />
        </button>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="flex flex-col gap-3 rounded-xl bg-ork-surface p-4">
          <p className="text-[12px] font-bold uppercase tracking-wide text-ork-blue">1 · Cuando</p>
          <SelectField
            label="Evento"
            value={trigger}
            onChange={(e) => {
              setTrigger(e.target.value);
              setConditions({});
            }}
          >
            {Object.entries(groups).map(([g, items]) => (
              <optgroup key={g} label={g}>
                {items.map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </optgroup>
            ))}
          </SelectField>
          {fields.includes("campaign_id") && (
            <SelectField label="Campaña" value={String(conditions.campaign_id ?? "")} onChange={(e) => setCond("campaign_id", e.target.value)}>
              <option value="">Cualquier campaña</option>
              {c.options.campaigns.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
          )}
          {fields.includes("min_opens") && (
            <TextField label="Número de aperturas" type="number" min={1} max={30} value={String(conditions.min_opens ?? 1)} onChange={(e) => setCond("min_opens", Number(e.target.value))} hint="Se activa una sola vez, cuando llega a ese número." />
          )}
          {fields.includes("wait_days") && (
            <TextField label="Días de espera después del último correo" type="number" min={0} max={30} value={String(conditions.wait_days ?? 3)} onChange={(e) => setCond("wait_days", Number(e.target.value))} hint="Si responde antes, la regla no se ejecuta." />
          )}
          {fields.includes("stage_id") && (
            <SelectField label="Etapa" value={String(conditions.stage_id ?? "")} onChange={(e) => setCond("stage_id", e.target.value)}>
              <option value="">Cualquier etapa</option>
              {c.options.stages.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
          )}
          {fields.includes("source") && (
            <SelectField label="Origen" value={String(conditions.source ?? "")} onChange={(e) => setCond("source", e.target.value)}>
              <option value="">Cualquier origen</option>
              {c.options.sources.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
          )}
        </div>
        <div className="flex flex-col gap-3 rounded-xl bg-ork-surface p-4">
          <p className="text-[12px] font-bold uppercase tracking-wide text-ork-violet">2 · Entonces</p>
          {actions.map((a, i) => (
            <div key={i} className="rounded-lg border border-ork-line bg-ork-card p-3">
              <div className="flex items-end gap-2">
                <SelectField label={`Acción ${i + 1}`} className="flex-1" value={a.type} onChange={(e) => setAction(i, { type: e.target.value })}>
                  {Object.entries(c.actions).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v.label}
                    </option>
                  ))}
                </SelectField>
                {actions.length > 1 && (
                  <Button size="sm" variant="ghost" aria-label="Quitar acción" icon={<Trash2 className="size-4" aria-hidden />} onClick={() => setActions((s) => s.filter((_, n) => n !== i))} />
                )}
              </div>
              <ActionFields c={c} a={a} onChange={(patch) => setAction(i, patch)} />
            </div>
          ))}
          {actions.length < 8 && (
            <Button size="sm" variant="secondary" icon={<Plus className="size-4" aria-hidden />} onClick={() => setActions((s) => [...s, { type: "create_task", title: "", due_hours: 24 }])}>
              Agregar acción
            </Button>
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-end justify-between gap-3">
        <TextField label="Nombre de la regla (opcional)" className="min-w-[280px] flex-1" value={name} onChange={(e) => setName(e.target.value)} placeholder={c.triggers[trigger]?.label} />
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            {rule ? "Guardar cambios" : "Crear regla"}
          </Button>
        </div>
      </div>
      {save.error && (
        <div className="mt-3">
          <Alert>{save.error.message}</Alert>
        </div>
      )}
    </Card>
  );
}

function ActionFields({ c, a, onChange }: { c: Catalog; a: Action; onChange: (p: Partial<Action>) => void }) {
  const select = (labelText: string, key: "stage_id" | "list_id" | "campaign_id" | "status", opts: Option[], empty: string) => (
    <SelectField label={labelText} value={a[key] ?? ""} onChange={(e) => onChange({ [key]: e.target.value })}>
      <option value="">{empty}</option>
      {opts.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
          {o.status && o.status !== "active" ? ` (${o.status === "draft" ? "borrador" : o.status === "paused" ? "en pausa" : "terminada"})` : ""}
        </option>
      ))}
    </SelectField>
  );
  return (
    <div className="mt-2 flex flex-col gap-2">
      {a.type === "create_task" && (
        <div className="grid gap-2 sm:grid-cols-[1fr_110px]">
          <TextField label="Tarea" value={a.title ?? ""} onChange={(e) => onChange({ title: e.target.value })} hint={VARS} />
          <TextField label="Vence en (h)" type="number" min={0} max={720} value={String(a.due_hours ?? 24)} onChange={(e) => onChange({ due_hours: Number(e.target.value) })} />
        </div>
      )}
      {(a.type === "add_note" || a.type === "notify_email") && (
        <TextField label={a.type === "add_note" ? "Nota" : "Mensaje del aviso"} value={a.text ?? ""} onChange={(e) => onChange({ text: e.target.value })} hint={a.type === "notify_email" ? `Llega a tu correo desde tu Gmail conectado. ${VARS}` : VARS} />
      )}
      {a.type === "create_deal" && (
        <div className="grid gap-2 sm:grid-cols-[1fr_130px]">
          {select("Etapa", "stage_id", c.options.stages, "Primera etapa")}
          <TextField label="Valor (USD)" type="number" min={0} value={String(a.value ?? 0)} onChange={(e) => onChange({ value: Number(e.target.value) })} />
        </div>
      )}
      {a.type === "move_deal" && select("Etapa", "stage_id", c.options.stages, "Elige la etapa")}
      {a.type === "set_status" && select("Estado", "status", c.options.statuses, "Elige el estado")}
      {a.type === "add_to_list" && select("Lista", "list_id", c.options.lists, "Elige la lista")}
      {a.type === "add_to_campaign" && select("Campaña", "campaign_id", c.options.campaigns.filter((o) => o.status === "active" || o.status === "paused"), "Elige una campaña activa")}
      {a.type === "stop_sequences" && <p className="text-[12.5px] text-ork-mist">No recibirá más correos de ninguna secuencia en curso.</p>}
    </div>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Eye, Mail, MousePointerClick, Pause, Play, Plus, Reply, Rocket, Send, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert, Badge, Button, Card, CardHeader, cx, Modal, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
import type { Editor } from "@tiptap/react";
import { HtmlFrame, RichEditor, toHtml } from "../components/RichEditor";
import { api } from "../lib/api";
import { useLists, useMeta } from "./contacts";
import { formatWhen } from "./dashboard";

type Stats = {
  recipients: number;
  by_status: Record<string, number>;
  contacted: number;
  emails_sent: number;
  opened: number;
  clicked: number;
  replied: number;
  bounced: number;
  unsubscribed: number;
  open_rate: number | null;
  click_rate: number | null;
  reply_rate: number | null;
  bounce_rate: number | null;
};
type Step = { subject: string; body: string; delay_days: number };
type Settings = { timezone: string; days: number[]; start_hour: number; end_hour: number; track_opens: boolean; track_clicks: boolean };
type Audience = { list_id?: string | null; filters?: Record<string, string>; contact_ids?: string[]; include_estimated?: boolean };
type Campaign = {
  id: string;
  name: string;
  status: "draft" | "active" | "paused" | "completed";
  mailbox: { id: string; email: string; status: string } | null;
  launched_at: string | null;
  next_window: string | null;
  created_at: string;
  steps_count: number;
  stats: Stats;
  steps?: Step[];
  settings?: Settings;
  audience?: Audience;
};
type Mailbox = { id: string; email: string; display_name: string; status: string; error: string | null; daily_limit: number; signature: string; sent_today: number };
type AudiencePreview = { total: number; eligible: number; no_email: number; invalid: number; suppressed: number; estimated: number; excluded_estimated: number; too_many: boolean };
type Preview = { contact: { full_name: string; email: string } | null; from: string | null; subject: string; body: string; html: string; missing: string[]; threaded: boolean };
type Recipient = {
  id: string;
  contact_id: string;
  full_name: string;
  company_name: string | null;
  job_title: string | null;
  email: string;
  email_status: string;
  status: string;
  step: number;
  sent_count: number;
  next_send_at: string | null;
  opened: boolean;
  open_count: number;
  clicked: boolean;
  replied_at: string | null;
  gmail_url: string | null;
  error: string | null;
};

const STATUS: Record<Campaign["status"], { label: string; tone: "neutral" | "ok" | "warn" | "blue" }> = {
  draft: { label: "Borrador", tone: "neutral" },
  active: { label: "Enviando", tone: "ok" },
  paused: { label: "En pausa", tone: "warn" },
  completed: { label: "Terminada", tone: "blue" },
};
const RECIPIENT: Record<string, { label: string; tone: "neutral" | "ok" | "warn" | "blue" | "bad" | "violet" }> = {
  pending: { label: "En cola", tone: "neutral" },
  active: { label: "En secuencia", tone: "blue" },
  replied: { label: "Respondió", tone: "ok" },
  completed: { label: "Secuencia completa", tone: "violet" },
  bounced: { label: "Rebotó", tone: "bad" },
  unsubscribed: { label: "Se dio de baja", tone: "warn" },
  failed: { label: "Falló", tone: "bad" },
  skipped: { label: "Detenido", tone: "neutral" },
};
const DAYS = ["L", "M", "X", "J", "V", "S", "D"];
const TIMEZONES = [
  ["America/Bogota", "Colombia, Perú, Ecuador, Panamá"],
  ["America/Mexico_City", "México"],
  ["America/Costa_Rica", "Costa Rica y Centroamérica"],
  ["America/Santiago", "Chile"],
  ["America/Argentina/Buenos_Aires", "Argentina"],
  ["America/Sao_Paulo", "Brasil"],
  ["America/New_York", "EE. UU. (Este)"],
  ["Europe/Madrid", "España"],
];
const pct = (v: number | null) => (v === null ? "—" : `${v} %`);

function useCampaigns() {
  return useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<{ items: Campaign[]; variables: Record<string, string>; gmail_enabled: boolean }>("/api/campaigns"),
    refetchInterval: 30000,
  });
}

export function useMailboxes() {
  return useQuery({ queryKey: ["mailboxes"], queryFn: () => api<{ enabled: boolean; items: Mailbox[] }>("/api/mailboxes") });
}

export function CampaignsPage() {
  const list = useCampaigns();
  const boxes = useMailboxes();
  const [creating, setCreating] = useState(false);
  const items = list.data?.items ?? [];
  const totals = items.reduce(
    (acc, c) => ({ sent: acc.sent + c.stats.emails_sent, replied: acc.replied + c.stats.replied, active: acc.active + (c.status === "active" ? 1 : 0) }),
    { sent: 0, replied: 0, active: 0 },
  );
  const noMailbox = boxes.data && boxes.data.items.length === 0;
  return (
    <div>
      <PageHeader
        title="Campañas"
        description="Secuencias de correos desde tu Gmail, con seguimiento de aperturas y clics. Cuando alguien responde, su secuencia se detiene sola."
        action={
          <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setCreating(true)}>
            Nueva campaña
          </Button>
        }
      />
      {noMailbox && (
        <div className="mb-5">
          <Card className="flex flex-wrap items-center justify-between gap-4 border-ork-blue/30 bg-ork-blue/5">
            <div className="flex items-start gap-3">
              <Mail className="mt-0.5 size-5 text-ork-blue" aria-hidden />
              <div>
                <p className="text-[14.5px] font-semibold text-ork-ink">Conecta tu Gmail para enviar</p>
                <p className="text-[13px] text-ork-mist">
                  {boxes.data?.enabled
                    ? "Los correos salen desde tu propio buzón: mejor entrega y las respuestas llegan a tu bandeja."
                    : "Falta configurar el acceso a Google en el servidor (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y PUBLIC_BASE_URL)."}
                </p>
              </div>
            </div>
            {boxes.data?.enabled && (
              <a href="/api/mail/google/start">
                <Button>Conectar Gmail</Button>
              </a>
            )}
          </Card>
        </div>
      )}
      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <Kpi label="Campañas enviando" value={totals.active} />
        <Kpi label="Correos enviados" value={totals.sent} />
        <Kpi label="Respuestas" value={totals.replied} />
      </div>
      {list.isLoading ? (
        <Spinner />
      ) : items.length === 0 ? (
        <Card className="py-12 text-center">
          <Send className="mx-auto size-8 text-ork-mist" aria-hidden />
          <p className="mt-3 text-[15px] font-semibold text-ork-ink">Todavía no tienes campañas</p>
          <p className="mt-1 text-[13.5px] text-ork-mist">Crea una, elige a quién escribirle y define la secuencia de correos.</p>
        </Card>
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[760px] text-left text-[13.5px]">
            <thead className="border-b border-ork-line text-[12px] uppercase tracking-wide text-ork-mist">
              <tr>
                <th className="px-5 py-3 font-semibold">Campaña</th>
                <th className="py-3 font-semibold">Estado</th>
                <th className="py-3 text-right font-semibold">Contactos</th>
                <th className="py-3 text-right font-semibold">Enviados</th>
                <th className="py-3 text-right font-semibold">Apertura</th>
                <th className="py-3 text-right font-semibold">Clics</th>
                <th className="px-5 py-3 text-right font-semibold">Respuesta</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ork-line">
              {items.map((c) => (
                <tr key={c.id} className="hover:bg-ork-surface/60">
                  <td className="px-5 py-3">
                    <Link to={`/campanas/${c.id}`} className="font-semibold text-ork-ink hover:text-ork-blue">
                      {c.name}
                    </Link>
                    <p className="text-[12px] text-ork-mist">
                      {c.steps_count} correos · {c.mailbox?.email ?? "sin buzón"}
                    </p>
                  </td>
                  <td className="py-3">
                    <Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge>
                  </td>
                  <td className="py-3 text-right">{c.stats.recipients}</td>
                  <td className="py-3 text-right">{c.stats.emails_sent}</td>
                  <td className="py-3 text-right">{pct(c.stats.open_rate)}</td>
                  <td className="py-3 text-right">{pct(c.stats.click_rate)}</td>
                  <td className="px-5 py-3 text-right font-semibold text-ok">{pct(c.stats.reply_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <NewCampaignModal open={creating} onOpenChange={setCreating} />
    </div>
  );
}

function Kpi({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card className="py-4">
      <p className="text-[12.5px] font-semibold text-ork-mist">{label}</p>
      <p className="mt-1 text-[24px] font-bold text-ork-ink">{value}</p>
      {hint && <p className="text-[12px] text-ork-mist">{hint}</p>}
    </Card>
  );
}

function NewCampaignModal({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [name, setName] = useState("");
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: () => api<Campaign>("/api/campaigns", { method: "POST", json: { name } }),
    onSuccess: (c) => navigate(`/campanas/${c.id}`),
  });
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Nueva campaña" description="Arrancamos con una secuencia de 3 correos que puedes editar.">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        {create.error && <Alert>{create.error.message}</Alert>}
        <TextField label="Nombre" placeholder="Ej.: Bancos Colombia · Q4" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <Button type="submit" loading={create.isPending} disabled={name.trim().length < 2}>
          Crear y editar
        </Button>
      </form>
    </Modal>
  );
}

// --- Detalle / editor ---------------------------------------------------------------------

export function CampaignDetail() {
  const { id } = useParams();
  const client = useQueryClient();
  const key = ["campaign", id];
  const q = useQuery({ queryKey: key, queryFn: () => api<Campaign>(`/api/campaigns/${id}`), refetchInterval: 30000 });
  const action = useMutation({
    mutationFn: (verb: "launch" | "pause" | "resume") => api<Campaign>(`/api/campaigns/${id}/${verb}`, { method: "POST" }),
    onSuccess: (data) => {
      client.setQueryData(key, data);
      client.invalidateQueries({ queryKey: ["campaigns"] });
      client.invalidateQueries({ queryKey: ["recipients", id] });
    },
  });
  const [confirm, setConfirm] = useState(false);
  const c = q.data;
  if (q.isLoading) return <Spinner />;
  if (!c) return <Alert>{q.error?.message ?? "No encontramos la campaña."}</Alert>;
  const draft = c.status === "draft";
  return (
    <div>
      <Link to="/campanas" className="mb-3 inline-flex items-center gap-1 text-[13px] font-semibold text-ork-mist hover:text-ork-ink">
        <ArrowLeft className="size-4" aria-hidden /> Campañas
      </Link>
      <PageHeader
        title={c.name}
        description={
          draft
            ? "Define la secuencia, a quién va y cuándo se envía. Luego lánzala."
            : `Lanzada ${c.launched_at ? formatWhen(c.launched_at) : ""} · desde ${c.mailbox?.email ?? "—"}`
        }
        action={
          <div className="flex items-center gap-2">
            <Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge>
            {draft && (
              <Button icon={<Rocket className="size-4" aria-hidden />} onClick={() => setConfirm(true)}>
                Revisar y lanzar
              </Button>
            )}
            {c.status === "active" && (
              <Button variant="secondary" icon={<Pause className="size-4" aria-hidden />} loading={action.isPending} onClick={() => action.mutate("pause")}>
                Pausar
              </Button>
            )}
            {c.status === "paused" && (
              <Button icon={<Play className="size-4" aria-hidden />} loading={action.isPending} onClick={() => action.mutate("resume")}>
                Reanudar
              </Button>
            )}
          </div>
        }
      />
      {action.error && !confirm && (
        <div className="mb-4">
          <Alert>{action.error.message}</Alert>
        </div>
      )}
      {c.next_window && (
        <div className="mb-4">
          <Alert tone="blue">
            Fuera del horario de envío. El próximo correo sale el{" "}
            {new Date(c.next_window).toLocaleString("es", { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit" })}
            . Puedes cambiar los días y horas en «Envío».
          </Alert>
        </div>
      )}
      {c.mailbox?.status === "error" && (
        <div className="mb-4">
          <Alert>
            El buzón {c.mailbox.email} perdió el permiso de Google. <Link to="/configuracion/integraciones" className="underline">Reconéctalo</Link> para seguir enviando.
          </Alert>
        </div>
      )}
      {!draft && <CampaignStats c={c} />}
      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex flex-col gap-5">
          <StepsEditor c={c} />
          {!draft && <Recipients campaignId={c.id} />}
        </div>
        <div className="flex flex-col gap-5">
          <SendingCard c={c} />
          <AudienceCard c={c} />
        </div>
      </div>
      <LaunchModal c={c} open={confirm} onOpenChange={setConfirm} launch={() => action.mutate("launch", { onSuccess: () => setConfirm(false) })} pending={action.isPending} error={action.error?.message} />
    </div>
  );
}

function CampaignStats({ c }: { c: Campaign }) {
  const s = c.stats;
  return (
    <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Kpi label="Contactos" value={s.recipients} hint={`${s.contacted} ya contactados`} />
      <Kpi label="Correos enviados" value={s.emails_sent} />
      <Kpi label="Apertura" value={pct(s.open_rate)} hint={`${s.opened} personas`} />
      <Kpi label="Clics" value={pct(s.click_rate)} hint={`${s.clicked} personas`} />
      <Kpi label="Respuesta" value={pct(s.reply_rate)} hint={`${s.replied} respondieron`} />
      <Kpi label="Rebotes y bajas" value={s.bounced + s.unsubscribed} hint={`${s.bounced} rebotes · ${s.unsubscribed} bajas`} />
    </div>
  );
}

function useSaveCampaign(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (json: Record<string, unknown>) => api<Campaign>(`/api/campaigns/${id}`, { method: "PATCH", json }),
    onSuccess: (data) => {
      client.setQueryData(["campaign", id], data);
      client.invalidateQueries({ queryKey: ["campaign-preview", id] });
    },
  });
}

function StepsEditor({ c }: { c: Campaign }) {
  const [steps, setSteps] = useState<Step[]>(c.steps ?? []);
  const [dirty, setDirty] = useState(false);
  const [previewing, setPreviewing] = useState<number | null>(null);
  const save = useSaveCampaign(c.id);
  const vars = useCampaigns().data?.variables ?? {};
  const focused = useRef<{ index: number; el?: HTMLInputElement; editor?: Editor } | null>(null);
  const caret = useRef<{ el: HTMLInputElement; pos: number } | null>(null);
  useLayoutEffect(() => {
    const c = caret.current;
    if (!c) return;
    caret.current = null;
    c.el.focus();
    c.el.setSelectionRange(c.pos, c.pos);
  });
  useEffect(() => {
    if (!dirty) setSteps(c.steps ?? []);
  }, [c.steps, dirty]);
  const locked = c.status !== "draft";
  const update = (i: number, patch: Partial<Step>) => {
    setSteps((s) => s.map((step, n) => (n === i ? { ...step, ...patch } : step)));
    setDirty(true);
  };
  const insert = (name: string) => {
    const f = focused.current;
    if (!f) return;
    const token = `{{${name}}}`;
    if (f.editor) {
      f.editor.chain().focus().insertContent(token).run();
      return;
    }
    const el = f.el!;
    const value = steps[f.index].subject;
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    caret.current = { el, pos: start + token.length };
    update(f.index, { subject: value.slice(0, start) + token + value.slice(end) });
  };
  return (
    <Card>
      <CardHeader
        title="Secuencia de correos"
        description="Los seguimientos sin asunto salen como respuesta en el mismo hilo («Re: …»). Si la persona responde, no recibe los siguientes."
        action={
          dirty && (
            <Button size="sm" loading={save.isPending} onClick={() => save.mutate({ steps }, { onSuccess: () => setDirty(false) })}>
              Guardar cambios
            </Button>
          )
        }
      />
      {save.error && (
        <div className="mb-3">
          <Alert>{save.error.message}</Alert>
        </div>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[12.5px] text-ork-mist">Insertar dato:</span>
        {Object.entries(vars).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => insert(k)}
            className="ork-focus rounded-full border border-ork-line px-2.5 py-0.5 text-[12px] font-semibold text-ork-graphite hover:border-ork-blue/40 hover:text-ork-blue"
          >
            {label}
          </button>
        ))}
        <span className="ml-1 text-[12px] text-ork-mist">
          · Valor alterno si falta: <code className="rounded bg-ork-ink/5 px-1">{"{{nombre|Hola}}"}</code>
        </span>
      </div>
      <ol className="flex flex-col gap-4">
        {steps.map((step, i) => (
          <li key={i} className="rounded-xl border border-ork-line p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="ork-gradient inline-flex size-6 items-center justify-center rounded-full text-[12px] font-bold text-white">{i + 1}</span>
                {i === 0 ? (
                  <span className="text-[13px] font-semibold text-ork-ink">Primer correo · apenas se lanza</span>
                ) : (
                  <label className="flex items-center gap-1.5 text-[13px] font-semibold text-ork-ink">
                    Esperar
                    <input
                      type="number"
                      min={0}
                      max={60}
                      value={step.delay_days}
                      onChange={(e) => update(i, { delay_days: Number(e.target.value) })}
                      className="ork-focus h-8 w-16 rounded-lg border border-ork-line px-2 text-center"
                    />
                    días sin respuesta
                  </label>
                )}
              </div>
              <div className="flex items-center gap-1">
                <Button size="sm" variant="ghost" icon={<Eye className="size-4" aria-hidden />} onClick={() => setPreviewing(i)} disabled={dirty}>
                  Vista previa
                </Button>
                {!locked && steps.length > 1 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="Quitar correo"
                    icon={<Trash2 className="size-4" aria-hidden />}
                    onClick={() => {
                      setSteps((s) => s.filter((_, n) => n !== i));
                      setDirty(true);
                    }}
                  />
                )}
              </div>
            </div>
            <input
              value={step.subject}
              placeholder={i === 0 ? "Asunto" : "Sin asunto = respuesta en el mismo hilo"}
              onFocus={(e) => (focused.current = { index: i, el: e.currentTarget })}
              onChange={(e) => update(i, { subject: e.target.value })}
              className="ork-focus mb-2 h-10 w-full rounded-lg border border-ork-line px-3 text-[14px] font-semibold text-ork-ink placeholder:font-normal placeholder:text-ork-mist"
            />
            <RichEditor
              value={step.body}
              onChange={(html) => update(i, { body: html })}
              onFocus={(editor) => (focused.current = { index: i, editor })}
            />
          </li>
        ))}
      </ol>
      {steps.length < 6 && (
        <Button
          className="mt-4"
          size="sm"
          variant="secondary"
          icon={<Plus className="size-4" aria-hidden />}
          onClick={() => {
            setSteps((s) => [...s, { subject: "", body: "Hola {{nombre|}}, ", delay_days: 3 }]);
            setDirty(true);
          }}
        >
          Agregar seguimiento
        </Button>
      )}
      <PreviewModal campaignId={c.id} step={previewing} onClose={() => setPreviewing(null)} />
    </Card>
  );
}

function PreviewModal({ campaignId, step, onClose }: { campaignId: string; step: number | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["campaign-preview", campaignId, step],
    queryFn: () => api<Preview>(`/api/campaigns/${campaignId}/preview`, { method: "POST", json: { step } }),
    enabled: step !== null,
  });
  const p = q.data;
  return (
    <Modal open={step !== null} onOpenChange={(v) => !v && onClose()} title={`Vista previa · correo ${(step ?? 0) + 1}`} description={p?.contact ? `Como lo verá ${p.contact.full_name}` : "Con datos de ejemplo"}>
      {q.isLoading ? (
        <Spinner />
      ) : q.error ? (
        <Alert>{q.error.message}</Alert>
      ) : p ? (
        <div className="flex flex-col gap-3 text-[13.5px]">
          {p.missing.length > 0 && (
            <Alert tone="blue">
              A este contacto le falta: {p.missing.join(", ")}. Usa un valor alterno, p. ej. {"{{" + p.missing[0] + "|…}}"}.
            </Alert>
          )}
          <div className="rounded-xl border border-ork-line">
            <div className="border-b border-ork-line px-4 py-2.5 text-[12.5px] text-ork-mist">
              <p>De: {p.from ?? "tu buzón"}</p>
              <p>Para: {p.contact?.email ?? "contacto@empresa.com"}</p>
              <p className="mt-1 text-[14px] font-semibold text-ork-ink">{p.subject}</p>
              {p.threaded && <p className="text-[12px]">Se envía como respuesta en el hilo del primer correo.</p>}
            </div>
            <div className="max-h-[60vh] overflow-y-auto">
              <HtmlFrame html={p.html} />
            </div>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

function SendingCard({ c }: { c: Campaign }) {
  const boxes = useMailboxes();
  const save = useSaveCampaign(c.id);
  const s = c.settings!;
  const set = (patch: Partial<Settings>) => save.mutate({ settings: { ...s, ...patch } });
  return (
    <Card>
      <CardHeader title="Envío" description="Horario hábil del destinatario y pausas de 1 a 3 minutos entre correos, como lo haría una persona." />
      <div className="flex flex-col gap-4">
        {boxes.data && boxes.data.items.length === 0 ? (
          <Alert tone="blue">
            Primero <Link to="/configuracion/integraciones" className="font-semibold underline">conecta tu Gmail</Link>.
          </Alert>
        ) : (
          <SelectField label="Enviar desde" value={c.mailbox?.id ?? ""} onChange={(e) => save.mutate({ mailbox_id: e.target.value || null })}>
            <option value="">Elige un buzón</option>
            {(boxes.data?.items ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.email} · hasta {m.daily_limit} al día
              </option>
            ))}
          </SelectField>
        )}
        <SelectField label="Zona horaria" value={s.timezone} onChange={(e) => set({ timezone: e.target.value })}>
          {TIMEZONES.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </SelectField>
        <div>
          <p className="mb-1.5 text-[13px] font-semibold text-ork-ink">Días</p>
          <div className="flex gap-1">
            {DAYS.map((d, i) => {
              const day = i + 1;
              const on = s.days.includes(day);
              return (
                <button
                  key={d}
                  type="button"
                  aria-pressed={on}
                  onClick={() => set({ days: on ? s.days.filter((x) => x !== day) : [...s.days, day] })}
                  className={cx(
                    "ork-focus size-9 rounded-lg border text-[13px] font-semibold",
                    on ? "border-ork-blue bg-ork-blue text-white" : "border-ork-line text-ork-graphite",
                  )}
                >
                  {d}
                </button>
              );
            })}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <SelectField label="Desde" value={s.start_hour} onChange={(e) => set({ start_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {h}:00
              </option>
            ))}
          </SelectField>
          <SelectField label="Hasta" value={s.end_hour} onChange={(e) => set({ end_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => h + 1).map((h) => (
              <option key={h} value={h}>
                {h}:00
              </option>
            ))}
          </SelectField>
        </div>
        <label className="flex items-center gap-2 text-[13.5px] text-ork-graphite">
          <input type="checkbox" checked={s.track_opens} onChange={(e) => set({ track_opens: e.target.checked })} /> Medir aperturas
        </label>
        <label className="flex items-center gap-2 text-[13.5px] text-ork-graphite">
          <input type="checkbox" checked={s.track_clicks} onChange={(e) => set({ track_clicks: e.target.checked })} /> Medir clics en enlaces
        </label>
        {save.error && <Alert>{save.error.message}</Alert>}
      </div>
    </Card>
  );
}

function AudienceCard({ c }: { c: Campaign }) {
  const lists = useLists();
  const meta = useMeta();
  const save = useSaveCampaign(c.id);
  const a = c.audience ?? {};
  const locked = c.status !== "draft";
  const preview = useQuery({
    queryKey: ["audience", c.id, a],
    queryFn: () => api<AudiencePreview>("/api/campaigns/audience", { method: "POST", json: { audience: a } }),
    enabled: !locked,
  });
  const setAudience = (patch: Partial<Audience>) => save.mutate({ audience: { ...a, ...patch } });
  const setFilter = (k: string, v: string) => setAudience({ filters: { ...(a.filters ?? {}), [k]: v } });
  const p = preview.data;
  if (locked)
    return (
      <Card>
        <CardHeader title="Audiencia" description="Se fijó al lanzar la campaña." />
        <p className="text-[13.5px] text-ork-graphite">{c.stats.recipients} contactos en la secuencia.</p>
      </Card>
    );
  return (
    <Card>
      <CardHeader title="Audiencia" description="Solo se escribe a contactos con correo; se excluyen inválidos, rebotes y bajas." />
      <div className="flex flex-col gap-3">
        {a.contact_ids && a.contact_ids.length > 0 ? (
          <Alert tone="blue">
            {a.contact_ids.length} contactos elegidos desde Contactos.{" "}
            <button className="font-semibold underline" onClick={() => setAudience({ contact_ids: [] })}>
              Usar filtros
            </button>
          </Alert>
        ) : (
          <>
            <SelectField label="Lista" value={a.list_id ?? ""} onChange={(e) => setAudience({ list_id: e.target.value || null })}>
              <option value="">Todos los contactos</option>
              {(lists.data ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.contacts})
                </option>
              ))}
            </SelectField>
            <SelectField label="País" value={a.filters?.pais ?? ""} onChange={(e) => setFilter("pais", e.target.value)}>
              <option value="">Todos</option>
              {(meta.data?.countries ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
            <SelectField label="Industria" value={a.filters?.industria ?? ""} onChange={(e) => setFilter("industria", e.target.value)}>
              <option value="">Todas</option>
              {(meta.data?.industries ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
            <TextField label="Que contenga" placeholder="Empresa, cargo, ciudad…" defaultValue={a.filters?.q ?? ""} onBlur={(e) => e.target.value !== (a.filters?.q ?? "") && setFilter("q", e.target.value)} />
          </>
        )}
        <label className="flex items-start gap-2 text-[13.5px] text-ork-graphite">
          <input type="checkbox" className="mt-1" checked={a.include_estimated ?? true} onChange={(e) => setAudience({ include_estimated: e.target.checked })} />
          <span>
            Incluir correos estimados <span className="text-ork-mist">(deducidos del patrón de la empresa; pueden rebotar)</span>
          </span>
        </label>
        {p && (
          <div className="rounded-xl bg-ork-surface p-3 text-[13px]">
            <p className="text-[20px] font-bold text-ork-ink">{p.eligible}</p>
            <p className="text-ork-graphite">recibirán la campaña{p.estimated ? ` (${p.estimated} con correo estimado)` : ""}</p>
            <p className="mt-1 text-[12px] text-ork-mist">
              Fuera: {p.no_email} sin correo · {p.invalid} inválidos · {p.suppressed} bajas o rebotes
              {p.excluded_estimated ? ` · ${p.excluded_estimated} estimados` : ""}
            </p>
            {p.too_many && <p className="mt-1 text-[12px] text-warn">Máximo 5.000 por campaña; se tomarán los primeros.</p>}
          </div>
        )}
      </div>
    </Card>
  );
}

function LaunchModal({ c, open, onOpenChange, launch, pending, error }: { c: Campaign; open: boolean; onOpenChange: (v: boolean) => void; launch: () => void; pending: boolean; error?: string }) {
  const boxes = useMailboxes();
  const preview = useQuery({
    queryKey: ["audience", c.id, c.audience],
    queryFn: () => api<AudiencePreview>("/api/campaigns/audience", { method: "POST", json: { audience: c.audience } }),
    enabled: open,
  });
  const box = boxes.data?.items.find((m) => m.id === c.mailbox?.id);
  const eligible = preview.data?.eligible ?? 0;
  const days = box ? Math.ceil(eligible / Math.max(1, box.daily_limit)) : 0;
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Lanzar campaña" description="Revisa antes de enviar. Puedes pausarla cuando quieras.">
      <div className="flex flex-col gap-3 text-[13.5px] text-ork-graphite">
        {error && <Alert>{error}</Alert>}
        <ul className="flex flex-col gap-2">
          <li className="flex items-center gap-2">
            <Mail className="size-4 text-ork-blue" aria-hidden /> Desde <strong>{c.mailbox?.email ?? "— elige un buzón —"}</strong>
          </li>
          <li className="flex items-center gap-2">
            <Send className="size-4 text-ork-blue" aria-hidden /> {c.steps?.length ?? 0} correos en la secuencia
          </li>
          <li className="flex items-center gap-2">
            <Reply className="size-4 text-ork-blue" aria-hidden /> {eligible} contactos
            {box && eligible > 0 && ` · primer correo a todos en unos ${days} día${days === 1 ? "" : "s"} hábil${days === 1 ? "" : "es"} (${box.daily_limit} por día)`}
          </li>
        </ul>
        {(preview.data?.estimated ?? 0) > 0 && (
          <Alert tone="blue">
            <TriangleAlert className="mr-1 inline size-4" aria-hidden />
            {preview.data?.estimated} correos son estimados. Los que reboten se marcan como inválidos y no se vuelven a usar.
          </Alert>
        )}
        <Button icon={<Rocket className="size-4" aria-hidden />} loading={pending} disabled={eligible === 0} onClick={launch}>
          Lanzar ahora
        </Button>
      </div>
    </Modal>
  );
}

function Recipients({ campaignId }: { campaignId: string }) {
  const [status, setStatus] = useState("");
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ["recipients", campaignId, status],
    queryFn: () => api<{ total: number; items: Recipient[] }>(`/api/campaigns/${campaignId}/recipients?status=${status}`),
    refetchInterval: 30000,
  });
  const stop = useMutation({
    mutationFn: ({ id, replied }: { id: string; replied: boolean }) =>
      api(`/api/campaigns/${campaignId}/recipients/${id}/stop?replied=${replied}`, { method: "POST" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["recipients", campaignId] });
      client.invalidateQueries({ queryKey: ["campaign", campaignId] });
    },
  });
  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5">
        <h2 className="text-[15px] font-bold text-ork-ink">Destinatarios ({q.data?.total ?? 0})</h2>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="ork-focus h-9 rounded-lg border border-ork-line px-2 text-[13px]">
          <option value="">Todos</option>
          {Object.entries(RECIPIENT).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </select>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[820px] text-left text-[13px] [&_td]:align-top">
          <thead className="border-y border-ork-line text-[11.5px] uppercase tracking-wide text-ork-mist">
            <tr>
              <th className="px-5 py-2 font-semibold">Contacto</th>
              <th className="py-2 font-semibold">Estado</th>
              <th className="py-2 font-semibold">Enviados</th>
              <th className="py-2 font-semibold">Actividad</th>
              <th className="px-5 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-ork-line">
            {(q.data?.items ?? []).map((r) => (
              <tr key={r.id}>
                <td className="px-5 py-2.5">
                  <p className="font-semibold text-ork-ink">{r.full_name}</p>
                  <p className="text-[12px] text-ork-mist">
                    {r.email}
                    {r.email_status === "estimado" && " · estimado"}
                    {r.company_name && ` · ${r.company_name}`}
                  </p>
                </td>
                <td className="whitespace-nowrap py-2.5 pr-3">
                  <Badge tone={RECIPIENT[r.status]?.tone ?? "neutral"}>{RECIPIENT[r.status]?.label ?? r.status}</Badge>
                  {r.error && <p className="mt-0.5 max-w-[220px] text-[11.5px] text-bad">{r.error}</p>}
                </td>
                <td className="whitespace-nowrap py-2.5 pr-3 text-ork-graphite">
                  {r.sent_count}
                  {r.next_send_at && ["pending", "active"].includes(r.status) && <p className="text-[11.5px] text-ork-mist">Próximo: {formatWhen(r.next_send_at)}</p>}
                </td>
                <td className="py-2.5">
                  <span className="flex items-center gap-2 text-ork-mist">
                    <span className={cx("flex items-center gap-0.5", r.opened && "text-ork-blue")} title="Abrió">
                      <Eye className="size-3.5" aria-hidden /> {r.open_count}
                    </span>
                    <span className={cx(r.clicked && "text-ork-violet")} title="Hizo clic">
                      <MousePointerClick className="size-3.5" aria-hidden />
                    </span>
                    {r.replied_at && <Reply className="size-3.5 text-ok" aria-label="Respondió" />}
                  </span>
                  {r.gmail_url && (
                    <a href={r.gmail_url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-[11.5px] font-semibold text-ork-blue hover:underline">
                      Ver en Gmail ↗
                    </a>
                  )}
                </td>
                <td className="px-5 py-2.5 text-right">
                  {["pending", "active"].includes(r.status) && (
                    <div className="flex justify-end gap-1 whitespace-nowrap">
                      <Button size="sm" variant="ghost" onClick={() => stop.mutate({ id: r.id, replied: true })}>
                        Ya respondió
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => stop.mutate({ id: r.id, replied: false })}>
                        Detener
                      </Button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {q.data?.items.length === 0 && <p className="px-5 py-6 text-center text-[13px] text-ork-mist">Nadie en este estado.</p>}
      </div>
    </Card>
  );
}

// --- Configuración → Integraciones: buzones de Gmail --------------------------------------

export function GmailCard() {
  const boxes = useMailboxes();
  const client = useQueryClient();
  const params = new URLSearchParams(window.location.search);
  const result = params.get("gmail");
  const patch = useMutation({
    mutationFn: ({ id, ...json }: { id: string; daily_limit?: number; signature?: string }) => api(`/api/mailboxes/${id}`, { method: "PATCH", json }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["mailboxes"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/mailboxes/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["mailboxes"] }),
  });
  const enabled = boxes.data?.enabled;
  return (
    <Card>
      <CardHeader
        title="Gmail para campañas"
        description="Cada vendedor conecta su propio buzón. ORKESTA envía en su nombre y lee solo los hilos de las campañas para detectar respuestas y rebotes."
        action={
          enabled ? (
            <a href="/api/mail/google/start">
              <Button size="sm" icon={<Mail className="size-4" aria-hidden />}>
                {boxes.data?.items.length ? "Conectar otro" : "Conectar Gmail"}
              </Button>
            </a>
          ) : (
            <Badge tone="warn">Pendiente de configuración</Badge>
          )
        }
      />
      {result === "conectado" && <Alert tone="ok">Gmail conectado. Ya puedes lanzar campañas.</Alert>}
      {result === "error" && <Alert>Google no completó la conexión. Inténtalo de nuevo y acepta los permisos de envío y lectura.</Alert>}
      {result === "cancelado" && <Alert tone="blue">Cancelaste la conexión con Google.</Alert>}
      {!enabled && (
        <p className="text-[13px] text-ork-mist">
          En Render agrega GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y PUBLIC_BASE_URL, y en Google Cloud la URI de redirección
          <code className="mx-1 rounded bg-ork-ink/5 px-1">/api/mail/google/callback</code>.
        </p>
      )}
      <ul className="mt-3 flex flex-col gap-3">
        {(boxes.data?.items ?? []).map((m) => (
          <li key={m.id} className="rounded-xl border border-ork-line p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-[14px] font-semibold text-ork-ink">{m.email}</p>
                <p className="text-[12.5px] text-ork-mist">
                  {m.sent_today} enviados hoy de {m.daily_limit}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {m.status === "active" ? <Badge tone="ok">Conectado</Badge> : <Badge tone="bad">Reconectar</Badge>}
                <Button size="sm" variant="ghost" loading={remove.isPending} onClick={() => remove.mutate(m.id)}>
                  Desconectar
                </Button>
              </div>
            </div>
            {m.error && <p className="mt-2 text-[12.5px] text-bad">{m.error}</p>}
            <div className="mt-3 grid gap-3">
              <TextField
                label="Límite diario"
                type="number"
                min={1}
                max={400}
                defaultValue={m.daily_limit}
                hint="Gmail gratis: ≤ 80. Workspace: ≤ 300."
                className="max-w-[220px]"
                onBlur={(e) => Number(e.target.value) !== m.daily_limit && patch.mutate({ id: m.id, daily_limit: Number(e.target.value) })}
              />
              <SignatureEditor value={m.signature} saving={patch.isPending} onSave={(signature) => patch.mutate({ id: m.id, signature })} />
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function SignatureEditor({ value, onSave, saving }: { value: string; onSave: (html: string) => void; saving: boolean }) {
  const [draft, setDraft] = useState(value);
  const [mode, setMode] = useState<"visual" | "html">(/<table/i.test(value) ? "html" : "visual");
  useEffect(() => setDraft(value), [value]);
  const changed = draft !== value;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13px] font-semibold text-ork-ink">Firma</p>
        <div className="flex rounded-lg border border-ork-line p-0.5 text-[12.5px] font-semibold">
          {(
            [
              ["visual", "Editor (texto e imágenes)"],
              ["html", "Pegar HTML"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setMode(k)}
              className={cx("rounded-md px-2.5 py-1", mode === k ? "bg-ork-blue text-white" : "text-ork-graphite")}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {mode === "visual" ? (
        <RichEditor value={draft} onChange={setDraft} minHeight={110} allowSource={false} />
      ) : (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            rows={6}
            placeholder="Pega el HTML de tu firma (en Gmail: Configuración → Firma; o desde tu generador de firmas)."
            className="ork-focus rounded-lg border border-ork-line px-3 py-2 font-mono text-[12.5px]"
          />
          <p className="text-[12px] text-ork-mist">Para una firma solo con imagen, usa «Editor» → imagen y sube el archivo.</p>
        </>
      )}
      {draft.trim() && (
        <div className="rounded-lg border border-dashed border-ork-line">
          <p className="px-3 pt-2 text-[11.5px] font-semibold uppercase tracking-wide text-ork-mist">Así se verá</p>
          <HtmlFrame html={toHtml(draft)} minHeight={60} />
        </div>
      )}
      <div className="flex justify-end">
        <Button size="sm" disabled={!changed} loading={saving} onClick={() => onSave(draft)}>
          Guardar firma
        </Button>
      </div>
    </div>
  );
}

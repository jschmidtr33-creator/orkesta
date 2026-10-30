import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  Check,
  ChevronDown,
  Database,
  ExternalLink,
  Globe,
  Mail,
  Newspaper,
  Phone,
  Sparkles,
  Square,
  UserPlus,
  Wand2,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Avatar, Badge, Button, Card, CardHeader, cx, Modal, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
import { api } from "../lib/api";
import { useLists, type Option } from "./contacts";
import { formatWhen } from "./dashboard";

type Options = {
  countries: Option[];
  industries: Option[];
  sizes: Option[];
  levels: Option[];
  areas: Option[];
  limits: number[];
};
type Filters = {
  country: string;
  industries: string[];
  sizes: string[];
  levels: string[];
  areas: string[];
  city: string;
  keywords: string;
  companies: string[];
  include_base: boolean;
  limit: number;
};
type SourceRef = { type: string; label: string; url: string; date?: string; headline?: string };
type Provenance = Record<string, { label: string; url: string; confidence: number }>;
type Run = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled";
  filters: Filters;
  summary: string;
  progress: { step?: string; done?: number; total?: number; log?: { at: string; text: string }[] };
  totals: Record<string, number>;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};
type Item<T> = { id: string; key: string; data: T; provenance: Provenance; sources: SourceRef[]; score: number; saved_contact_id: string | null };
type PersonData = {
  full_name: string;
  title: string;
  level_label: string;
  area_label: string;
  company: string;
  domain: string;
  email: string;
  email_status: "" | "publicado" | "estimado";
  phone: string;
  linkedin: string;
  city: string;
};
type CompanyData = {
  name: string;
  domain: string;
  website: string;
  employees: number | null;
  size: string | null;
  city: string;
  industry: string;
  description: string;
  linkedin: string;
  from_base: boolean;
  emails: string[];
  phones: string[];
  people_found: number;
  site_status: string;
};
type RunDetail = Run & { companies: Item<CompanyData>[]; people: Item<PersonData>[] };

const EMPTY: Filters = {
  country: "CO",
  industries: [],
  sizes: [],
  levels: [],
  areas: [],
  city: "",
  keywords: "",
  companies: [],
  include_base: true,
  limit: 25,
};
const ACTIVE = new Set(["queued", "running"]);
const STATUS: Record<Run["status"], { label: string; tone: "neutral" | "blue" | "ok" | "bad" | "warn" }> = {
  queued: { label: "En cola", tone: "neutral" },
  running: { label: "Investigando", tone: "blue" },
  completed: { label: "Completada", tone: "ok" },
  failed: { label: "Falló", tone: "bad" },
  canceled: { label: "Cancelada", tone: "warn" },
};
const SOURCE_META: Record<string, { label: string; icon: ReactNode }> = {
  wikidata: { label: "Wikidata", icon: <Database className="size-3" aria-hidden /> },
  sitio: { label: "Sitio web", icon: <Globe className="size-3" aria-hidden /> },
  noticia: { label: "Noticia", icon: <Newspaper className="size-3" aria-hidden /> },
  base: { label: "Tu base", icon: <Building2 className="size-3" aria-hidden /> },
  patron: { label: "Patrón", icon: <Wand2 className="size-3" aria-hidden /> },
};

function useOptions() {
  return useQuery({ queryKey: ["research-options"], queryFn: () => api<Options>("/api/research/options"), staleTime: Infinity });
}

export function ProspectingPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("corrida");
  const client = useQueryClient();
  const options = useOptions();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const runs = useQuery({ queryKey: ["research-runs"], queryFn: () => api<Run[]>("/api/research/runs") });

  const start = useMutation({
    mutationFn: (f: Filters) => api<Run>("/api/research/runs", { method: "POST", json: f }),
    onSuccess: (run) => {
      client.invalidateQueries({ queryKey: ["research-runs"] });
      setParams({ corrida: run.id });
      if (window.matchMedia("(max-width: 1023px)").matches) {
        setTimeout(() => document.getElementById("resultados")?.scrollIntoView({ behavior: "smooth" }), 100);
      }
    },
  });

  const reuse = (run: Run) => {
    setFilters({ ...EMPTY, ...run.filters });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <>
      <PageHeader
        title="Prospección con IA"
        description="Describe a tu cliente ideal y el agente investiga por ti: catálogo abierto de Wikidata, sitios oficiales de cada empresa y noticias recientes. Cada dato muestra su fuente."
      />
      <div className="grid items-start gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
        <FilterPanel
          options={options.data}
          filters={filters}
          setFilters={setFilters}
          onSubmit={() => start.mutate(filters)}
          loading={start.isPending}
          error={start.error?.message}
        />
        <div id="resultados" className="min-w-0 scroll-mt-4">
          {runId ? (
            <RunView runId={runId} onReuse={reuse} onBack={() => setParams({})} />
          ) : (
            <RecentRuns runs={runs.data} loading={runs.isLoading} onOpen={(id) => setParams({ corrida: id })} />
          )}
        </div>
      </div>
    </>
  );
}

// --- Filtros --------------------------------------------------------------------------------

function Chips({ options, selected, onChange, max }: { options: Option[]; selected: string[]; onChange: (v: string[]) => void; max?: string }) {
  return (
    <div className={cx("flex flex-wrap gap-1.5", max && `${max} overflow-y-auto pr-1`)}>
      {options.map((o) => {
        const on = selected.includes(o.value);
        return (
          <button
            type="button"
            key={o.value}
            aria-pressed={on}
            onClick={() => onChange(on ? selected.filter((v) => v !== o.value) : [...selected, o.value])}
            className={cx(
              "ork-focus inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[12.5px] font-medium transition",
              on ? "border-ork-blue bg-ork-blue/10 text-ork-blue" : "border-ork-line bg-white text-ork-graphite hover:border-ork-mist",
            )}
          >
            {on && <Check className="size-3" aria-hidden />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function Group({ title, hint, children, open: initial = true, count }: { title: string; hint?: string; children: ReactNode; open?: boolean; count?: number }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="border-t border-ork-line pt-3">
      <button type="button" onClick={() => setOpen(!open)} className="ork-focus flex w-full items-center justify-between rounded-md text-left" aria-expanded={open}>
        <span className="text-[13px] font-semibold text-ork-ink">
          {title}
          {count ? <span className="ml-1.5 rounded-full bg-ork-blue/10 px-1.5 text-[11px] text-ork-blue">{count}</span> : null}
        </span>
        <ChevronDown className={cx("size-4 text-ork-mist transition", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div className="mt-2.5">
          {hint && <p className="mb-2 text-[12px] text-ork-mist">{hint}</p>}
          {children}
        </div>
      )}
    </div>
  );
}

function FilterPanel({
  options,
  filters,
  setFilters,
  onSubmit,
  loading,
  error,
}: {
  options?: Options;
  filters: Filters;
  setFilters: (f: Filters) => void;
  onSubmit: () => void;
  loading: boolean;
  error?: string;
}) {
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setFilters({ ...filters, [key]: value });
  const [companiesText, setCompaniesText] = useState(filters.companies.join("\n"));
  useEffect(() => setCompaniesText(filters.companies.join("\n")), [filters.companies]);
  const named = filters.companies.length > 0;
  if (!options) return <Card><Spinner /></Card>;
  return (
    <Card className="lg:sticky lg:top-4">
      <form
        className="flex flex-col gap-3.5"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        <div className="flex items-center gap-2">
          <span className="ork-gradient inline-flex size-8 items-center justify-center rounded-lg text-white">
            <Sparkles className="size-4" aria-hidden />
          </span>
          <div>
            <h2 className="text-[15px] font-bold text-ork-ink">Cliente ideal</h2>
            <p className="text-[12px] text-ork-mist">Combina los filtros que necesites.</p>
          </div>
        </div>
        <SelectField label="País" value={filters.country} onChange={(e) => set("country", e.target.value)}>
          {options.countries.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </SelectField>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="empresas" className="text-[13px] font-semibold text-ork-ink">
            Empresas específicas <span className="font-normal text-ork-mist">(opcional)</span>
          </label>
          <textarea
            id="empresas"
            rows={2}
            value={companiesText}
            placeholder={"Una por línea: nombre o dominio\nEj.: Bancolombia, ecopetrol.com"}
            onChange={(e) => setCompaniesText(e.target.value)}
            onBlur={() =>
              set(
                "companies",
                companiesText
                  .split(/[\n,;]+/)
                  .map((s) => s.trim())
                  .filter(Boolean)
                  .slice(0, 25),
              )
            }
            className="ork-focus rounded-xl border border-ork-line bg-white px-3.5 py-2.5 text-[14px] text-ork-ink placeholder:text-ork-mist hover:border-ork-mist"
          />
          {named && <p className="text-[12px] text-ork-mist">Con empresas escritas se ignoran industria, ciudad y tamaño.</p>}
        </div>
        <Group title="Industria" count={filters.industries.length} open={!named}>
          <Chips options={options.industries} selected={filters.industries} onChange={(v) => set("industries", v)} max="max-h-44" />
        </Group>
        <Group title="Cargo de los decisores" count={filters.levels.length + filters.areas.length} hint="Sin selección se buscan todos los niveles de decisión.">
          <p className="mb-1.5 text-[12px] font-semibold text-ork-graphite">Nivel</p>
          <Chips options={options.levels} selected={filters.levels} onChange={(v) => set("levels", v)} />
          <p className="mb-1.5 mt-3 text-[12px] font-semibold text-ork-graphite">Área</p>
          <Chips options={options.areas} selected={filters.areas} onChange={(v) => set("areas", v)} max="max-h-36" />
        </Group>
        <Group title="Ubicación y tamaño" count={filters.sizes.length + (filters.city ? 1 : 0)} open={false}>
          <TextField label="Ciudad" placeholder="Ej.: Medellín" value={filters.city} onChange={(e) => set("city", e.target.value)} />
          <p className="mb-1.5 mt-3 text-[12px] font-semibold text-ork-graphite">Empleados</p>
          <Chips options={options.sizes} selected={filters.sizes} onChange={(v) => set("sizes", v)} />
        </Group>
        <Group title="Más opciones" open={false}>
          <TextField label="Palabras clave" placeholder="Ej.: fintech, exportadora" value={filters.keywords} onChange={(e) => set("keywords", e.target.value)} />
          <label className="mt-3 flex items-start gap-2 text-[13px] text-ork-graphite">
            <input type="checkbox" className="mt-0.5 size-4 accent-ork-blue" checked={filters.include_base} onChange={(e) => set("include_base", e.target.checked)} />
            Incluir empresas de mi base que cumplan los filtros
          </label>
          <p className="mb-1.5 mt-3 text-[12px] font-semibold text-ork-graphite">Empresas a investigar</p>
          <div className="inline-flex rounded-xl border border-ork-line p-0.5">
            {options.limits.map((n) => (
              <button
                type="button"
                key={n}
                onClick={() => set("limit", n)}
                className={cx("ork-focus rounded-lg px-3 py-1 text-[13px] font-semibold", filters.limit === n ? "ork-gradient text-white" : "text-ork-graphite")}
              >
                {n}
              </button>
            ))}
          </div>
        </Group>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" size="lg" loading={loading} icon={<Sparkles className="size-4" aria-hidden />}>
          Investigar
        </Button>
        <button type="button" className="text-[12.5px] font-semibold text-ork-mist hover:text-ork-ink" onClick={() => setFilters(EMPTY)}>
          Limpiar filtros
        </button>
      </form>
    </Card>
  );
}

// --- Corridas recientes ---------------------------------------------------------------------

function RecentRuns({ runs, loading, onOpen }: { runs?: Run[]; loading: boolean; onOpen: (id: string) => void }) {
  return (
    <div className="flex flex-col gap-5">
      <Card className="relative overflow-hidden">
        <div className="ork-glow pointer-events-none absolute inset-0 opacity-25" aria-hidden />
        <div className="relative">
          <h2 className="text-[17px] font-bold text-ork-ink">Cómo investiga el agente</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {[
              { icon: <Database className="size-4" />, title: "Wikidata", text: "Catálogo abierto de empresas, tamaño, sede y directivos vigentes." },
              { icon: <Globe className="size-4" />, title: "Sitios oficiales", text: "Páginas de equipo y directivos, correos y teléfonos publicados." },
              { icon: <Newspaper className="size-4" />, title: "Noticias", text: "Nombramientos de los últimos 3 meses en medios de todo el mundo." },
            ].map((s) => (
              <div key={s.title} className="rounded-xl border border-ork-line bg-white/80 p-3.5">
                <span className="inline-flex size-7 items-center justify-center rounded-lg bg-ork-blue/10 text-ork-blue">{s.icon}</span>
                <p className="mt-2 text-[13.5px] font-bold text-ork-ink">{s.title}</p>
                <p className="mt-0.5 text-[12.5px] text-ork-mist">{s.text}</p>
              </div>
            ))}
          </div>
          <p className="mt-4 text-[12.5px] text-ork-mist">
            Los correos publicados se marcan como tales; si el sitio revela el formato de la empresa, los demás se marcan como estimados hasta validarlos. Nada se inventa.
          </p>
        </div>
      </Card>
      <Card>
        <CardHeader title="Investigaciones recientes" />
        {loading ? (
          <Spinner />
        ) : !runs?.length ? (
          <p className="py-6 text-center text-[13.5px] text-ork-mist">Aún no has investigado. Elige un país y una industria, y presiona «Investigar».</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {runs.map((r) => (
              <li key={r.id}>
                <button onClick={() => onOpen(r.id)} className="ork-focus flex w-full flex-wrap items-center justify-between gap-2 rounded-lg px-1 py-3 text-left hover:bg-ork-ink/[0.02]">
                  <div className="min-w-0">
                    <p className="truncate text-[14px] font-semibold text-ork-ink">{r.summary}</p>
                    <p className="text-[12px] text-ork-mist">
                      {formatWhen(r.created_at)}
                      {r.status === "completed" && ` · ${r.totals.companies ?? 0} empresas · ${r.totals.people ?? 0} decisores`}
                    </p>
                  </div>
                  <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// --- Resultados -----------------------------------------------------------------------------

function SourceChips({ sources }: { sources: SourceRef[] }) {
  const groups = new Map<string, SourceRef>();
  for (const s of sources) if (!groups.has(s.type)) groups.set(s.type, s);
  return (
    <div className="flex flex-wrap gap-1">
      {[...groups.values()].map((s) => {
        const meta = SOURCE_META[s.type] ?? { label: s.type, icon: null };
        const chip = (
          <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-ork-ink/5 px-1.5 py-0.5 text-[11px] font-semibold text-ork-graphite">
            {meta.icon}
            {meta.label}
          </span>
        );
        return s.url ? (
          <a key={s.type} href={s.url} target="_blank" rel="noreferrer noopener" title={s.label} className="hover:opacity-80">
            {chip}
          </a>
        ) : (
          <span key={s.type} title={s.label}>
            {chip}
          </span>
        );
      })}
    </div>
  );
}

function EmailCell({ data }: { data: PersonData }) {
  if (!data.email) return <span className="text-[12.5px] text-ork-mist">No publicado</span>;
  return (
    <div className="min-w-0">
      <p className="truncate text-[13px] text-ork-ink">{data.email}</p>
      <Badge tone={data.email_status === "publicado" ? "ok" : "warn"}>{data.email_status === "publicado" ? "Publicado" : "Estimado"}</Badge>
    </div>
  );
}

const FIELD_NAMES: Record<string, string> = { full_name: "Nombre", title: "Cargo", email: "Correo", linkedin: "Perfil público" };

function PersonDetail({ item }: { item: Item<PersonData> }) {
  return (
    <div className="grid gap-4 bg-ork-ink/[0.02] px-4 py-4 sm:grid-cols-2">
      <div>
        <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ork-mist">Procedencia de cada dato</p>
        <dl className="space-y-2">
          {Object.entries(item.provenance).map(([field, p]) => (
            <div key={field}>
              <dt className="text-[12px] font-semibold text-ork-graphite">
                {FIELD_NAMES[field] ?? field} <span className="font-normal text-ork-mist">· confianza {p.confidence}%</span>
              </dt>
              <dd className="text-[12.5px] text-ork-mist">
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-ork-blue hover:underline">
                    {p.label} <ExternalLink className="size-3" aria-hidden />
                  </a>
                ) : (
                  p.label
                )}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="space-y-2 text-[13px]">
        <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ork-mist">Datos de contacto</p>
        {item.data.phone && (
          <p className="flex items-center gap-2 text-ork-graphite">
            <Phone className="size-3.5" aria-hidden /> {item.data.phone} <span className="text-ork-mist">(conmutador de la empresa)</span>
          </p>
        )}
        {item.data.email && (
          <p className="flex items-center gap-2 text-ork-graphite">
            <Mail className="size-3.5" aria-hidden /> {item.data.email}
          </p>
        )}
        {item.data.linkedin && (
          <a href={item.data.linkedin} target="_blank" rel="noreferrer noopener" className="flex items-center gap-2 text-ork-blue hover:underline">
            <ExternalLink className="size-3.5" aria-hidden /> Perfil profesional público
          </a>
        )}
        {item.sources
          .filter((s) => s.type === "noticia")
          .map((s) => (
            <a key={s.url} href={s.url} target="_blank" rel="noreferrer noopener" className="block rounded-lg border border-ork-line bg-white p-2.5 hover:border-ork-blue/40">
              <span className="text-[12.5px] font-semibold text-ork-ink">{s.headline}</span>
              <span className="mt-0.5 block text-[11.5px] text-ork-mist">{s.label}</span>
            </a>
          ))}
      </div>
    </div>
  );
}

function RunView({ runId, onReuse, onBack }: { runId: string; onReuse: (r: Run) => void; onBack: () => void }) {
  const client = useQueryClient();
  const detail = useQuery({
    queryKey: ["research-run", runId],
    queryFn: () => api<RunDetail>(`/api/research/runs/${runId}`),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 2000 : false),
  });
  const run = detail.data;
  const [tab, setTab] = useState<"people" | "companies">("people");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const status = run?.status;
  useEffect(() => {
    if (status && !ACTIVE.has(status)) client.invalidateQueries({ queryKey: ["research-runs"] });
  }, [status, client]);
  useEffect(() => setSelected(new Set()), [runId]);

  const cancel = useMutation({
    mutationFn: () => api(`/api/research/runs/${runId}/cancel`, { method: "POST" }),
    onSuccess: () => detail.refetch(),
  });
  const unsaved = useMemo(() => run?.people.filter((p) => !p.saved_contact_id) ?? [], [run]);

  if (detail.isLoading) return <Card><Spinner /></Card>;
  if (detail.error || !run) return <Alert>{detail.error?.message ?? "No encontramos esta investigación."}</Alert>;

  const active = ACTIVE.has(run.status);
  const total = run.progress.total ?? 0;
  const done = run.progress.done ?? 0;
  const pct = run.status === "completed" ? 100 : total ? Math.max(6, Math.round((done / total) * 100)) : 4;
  const allChecked = unsaved.length > 0 && unsaved.every((p) => selected.has(p.id));

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <button onClick={onBack} className="text-[12.5px] font-semibold text-ork-mist hover:text-ork-ink">
              ← Investigaciones
            </button>
            <h2 className="mt-1 text-[17px] font-bold text-ork-ink">{run.summary}</h2>
            <p className="text-[12.5px] text-ork-mist">{formatWhen(run.created_at)}</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={STATUS[run.status].tone}>{STATUS[run.status].label}</Badge>
            {active ? (
              <Button size="sm" variant="secondary" icon={<Square className="size-3.5" aria-hidden />} loading={cancel.isPending} onClick={() => cancel.mutate()}>
                Detener
              </Button>
            ) : (
              <Button size="sm" variant="secondary" onClick={() => onReuse(run)}>
                Reutilizar filtros
              </Button>
            )}
          </div>
        </div>
        {(active || run.status === "completed") && (
          <div className="mt-4">
            <div className="h-2 overflow-hidden rounded-full bg-ork-ink/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div className={cx("ork-gradient h-full rounded-full transition-all duration-700", active && "animate-pulse")} style={{ width: `${pct}%` }} />
            </div>
            {active && (
              <ul className="mt-3 space-y-1 text-[12.5px] text-ork-graphite" aria-live="polite">
                {(run.progress.log ?? []).slice(-5).map((l, i) => (
                  <li key={l.at + i} className="flex gap-2">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-ork-blue" aria-hidden />
                    {l.text}
                  </li>
                ))}
                {!run.progress.log?.length && <li className="text-ork-mist">En cola, comenzando en unos segundos…</li>}
              </ul>
            )}
          </div>
        )}
        {run.status === "failed" && <div className="mt-3"><Alert>{run.error}</Alert></div>}
        {run.status === "completed" && (
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Empresas", run.totals.companies ?? 0],
              ["Decisores", run.totals.people ?? 0],
              ["Correos publicados", run.totals.with_email ?? 0],
              ["Correos estimados", run.totals.estimated_emails ?? 0],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-ork-line p-3">
                <p className="text-[20px] font-bold text-ork-ink">{value}</p>
                <p className="text-[12px] text-ork-mist">{label}</p>
              </div>
            ))}
          </div>
        )}
      </Card>

      {saved && <Alert tone="ok">{saved}</Alert>}

      <Card className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ork-line px-5 pt-3">
          <div className="flex gap-1" role="tablist">
            {(
              [
                ["people", "Decisores", run.people.length],
                ["companies", "Empresas", run.companies.length],
              ] as const
            ).map(([value, label, count]) => (
              <button
                key={value}
                role="tab"
                aria-selected={tab === value}
                onClick={() => setTab(value)}
                className={cx(
                  "ork-focus -mb-px border-b-2 px-3 pb-2.5 text-[14px] font-semibold",
                  tab === value ? "border-ork-blue text-ork-blue" : "border-transparent text-ork-mist hover:text-ork-ink",
                )}
              >
                {label} <span className="ml-1 rounded-full bg-ork-ink/5 px-1.5 text-[11.5px]">{count}</span>
              </button>
            ))}
          </div>
          {tab === "people" && selected.size > 0 && (
            <Button size="sm" className="mb-2" icon={<UserPlus className="size-3.5" aria-hidden />} onClick={() => setSaving(true)}>
              Guardar {selected.size} en Contactos
            </Button>
          )}
        </div>

        {tab === "people" ? (
          run.people.length === 0 ? (
            <p className="px-5 py-10 text-center text-[13.5px] text-ork-mist">
              {active ? "Los decisores aparecerán aquí a medida que el agente los encuentre." : "No encontramos decisores públicos con estos filtros. Prueba con más empresas o sin filtro de área."}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left">
                <thead className="bg-ork-ink/[0.02] text-[11.5px] font-semibold uppercase tracking-wide text-ork-mist">
                  <tr>
                    <th className="w-10 px-4 py-2.5">
                      <input
                        type="checkbox"
                        aria-label="Seleccionar todos"
                        className="size-4 accent-ork-blue"
                        checked={allChecked}
                        onChange={() => setSelected(allChecked ? new Set() : new Set(unsaved.map((p) => p.id)))}
                      />
                    </th>
                    <th className="px-2 py-2.5">Decisor</th>
                    <th className="px-2 py-2.5">Empresa</th>
                    <th className="px-2 py-2.5">Correo</th>
                    <th className="px-4 py-2.5">Fuentes</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ork-line">
                  {run.people.map((p) => (
                    <Fragment key={p.id}>
                      <tr className="cursor-pointer hover:bg-ork-ink/[0.02]" onClick={() => setOpen(open === p.id ? null : p.id)}>
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          {p.saved_contact_id ? (
                            <Check className="size-4 text-ok" aria-label="Ya está en Contactos" />
                          ) : (
                            <input
                              type="checkbox"
                              aria-label={`Seleccionar a ${p.data.full_name}`}
                              className="size-4 accent-ork-blue"
                              checked={selected.has(p.id)}
                              onChange={() => {
                                const next = new Set(selected);
                                if (next.has(p.id)) next.delete(p.id);
                                else next.add(p.id);
                                setSelected(next);
                              }}
                            />
                          )}
                        </td>
                        <td className="px-2 py-3">
                          <div className="flex items-center gap-2.5">
                            <Avatar name={p.data.full_name} email="" size={32} />
                            <div className="min-w-0">
                              <p className="truncate text-[14px] font-semibold text-ork-ink">{p.data.full_name}</p>
                              <p className="truncate text-[12.5px] text-ork-mist">{p.data.title || "Cargo no publicado"}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-2 py-3">
                          <p className="text-[13.5px] text-ork-ink">{p.data.company}</p>
                          {p.data.domain && <p className="text-[12px] text-ork-mist">{p.data.domain}</p>}
                        </td>
                        <td className="max-w-[220px] px-2 py-3">
                          <EmailCell data={p.data} />
                        </td>
                        <td className="px-4 py-3">
                          <SourceChips sources={p.sources} />
                        </td>
                      </tr>
                      {open === p.id && (
                        <tr>
                          <td colSpan={5} className="p-0">
                            <PersonDetail item={p} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : run.companies.length === 0 ? (
          <p className="px-5 py-10 text-center text-[13.5px] text-ork-mist">{active ? "Buscando empresas…" : "Ninguna empresa cumple los filtros."}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left">
              <thead className="bg-ork-ink/[0.02] text-[11.5px] font-semibold uppercase tracking-wide text-ork-mist">
                <tr>
                  <th className="px-5 py-2.5">Empresa</th>
                  <th className="px-2 py-2.5">Industria</th>
                  <th className="px-2 py-2.5">Tamaño</th>
                  <th className="px-2 py-2.5">Contacto general</th>
                  <th className="px-2 py-2.5">Decisores</th>
                  <th className="px-5 py-2.5">Fuentes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ork-line">
                {run.companies.map((c) => (
                  <tr key={c.id}>
                    <td className="px-5 py-3">
                      <p className="text-[14px] font-semibold text-ork-ink">{c.data.name}</p>
                      <p className="text-[12px] text-ork-mist">
                        {c.data.website ? (
                          <a href={c.data.website} target="_blank" rel="noreferrer noopener" className="hover:text-ork-blue hover:underline">
                            {c.data.domain}
                          </a>
                        ) : (
                          c.data.domain
                        )}
                        {c.data.city && ` · ${c.data.city}`}
                      </p>
                    </td>
                    <td className="px-2 py-3 text-[13px] text-ork-graphite">{c.data.industry || "—"}</td>
                    <td className="px-2 py-3 text-[13px] text-ork-graphite">{c.data.employees ? `${c.data.employees.toLocaleString("es")} empleados` : "—"}</td>
                    <td className="px-2 py-3 text-[12.5px] text-ork-graphite">
                      {c.data.emails[0] && <p>{c.data.emails[0]}</p>}
                      {c.data.phones[0] && <p>{c.data.phones[0]}</p>}
                      {!c.data.emails.length && !c.data.phones.length && <span className="text-ork-mist">{c.data.site_status === "sin_acceso" ? "Sitio sin acceso" : "—"}</span>}
                    </td>
                    <td className="px-2 py-3">
                      <Badge tone={c.data.people_found ? "blue" : "neutral"}>{c.data.people_found}</Badge>
                    </td>
                    <td className="px-5 py-3">
                      <SourceChips sources={c.sources} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <SaveModal
        open={saving}
        onOpenChange={setSaving}
        runId={runId}
        ids={[...selected]}
        onDone={(message) => {
          setSaved(message);
          setSelected(new Set());
          detail.refetch();
        }}
      />
    </div>
  );
}

function SaveModal({ open, onOpenChange, runId, ids, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; runId: string; ids: string[]; onDone: (m: string) => void }) {
  const client = useQueryClient();
  const lists = useLists();
  const [listId, setListId] = useState("");
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api<{ created: number; merged: number; added_to_list: number; list: { name: string } | null }>(`/api/research/runs/${runId}/save`, {
        method: "POST",
        json: { ids, list_id: listId || null, new_list: listId ? "" : name },
      }),
    onSuccess: (r) => {
      client.invalidateQueries({ queryKey: ["lists"] });
      client.invalidateQueries({ queryKey: ["contacts"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      const parts = [`${r.created} contactos nuevos`];
      if (r.merged) parts.push(`${r.merged} ya existían y se completaron`);
      if (r.list) parts.push(`agregados a «${r.list.name}»`);
      onDone(parts.join(" · ") + ".");
      onOpenChange(false);
      setName("");
      setListId("");
    },
  });
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Guardar en Contactos" description={`${ids.length} decisores. Si alguno ya existe, solo se completan sus campos vacíos.`}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        {save.error && <Alert>{save.error.message}</Alert>}
        <SelectField label="Agregar también a una lista" value={listId} onChange={(e) => setListId(e.target.value)}>
          <option value="">Lista nueva (opcional)</option>
          {lists.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name} ({l.contacts})
            </option>
          ))}
        </SelectField>
        {!listId && <TextField label="Nombre de la lista nueva" placeholder="Déjalo vacío para no crear lista" value={name} onChange={(e) => setName(e.target.value)} />}
        <div className="flex items-center justify-between gap-2">
          <Link to="/contactos" className="text-[13px] font-semibold text-ork-mist hover:text-ork-ink">
            Ver Contactos
          </Link>
          <Button type="submit" loading={save.isPending}>
            Guardar
          </Button>
        </div>
      </form>
    </Modal>
  );
}

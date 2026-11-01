import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpDown,
  Building2,
  Copy,
  Download,
  Eraser,
  Save,
  Users,
  Check,
  ChevronDown,
  Database,
  ExternalLink,
  Globe,
  Mail,
  Newspaper,
  Phone,
  Search,
  Sparkles,
  Square,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Card, CardHeader, cx, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
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
  web_search: boolean;
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
  first_name: string;
  last_name: string;
  location: string;
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
  pages_read?: number;
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

function useOptions() {
  return useQuery({ queryKey: ["research-options"], queryFn: () => api<Options>("/api/research/options"), staleTime: Infinity });
}

type Tab = "filtros" | "empresas" | "personas";

export function ProspectingPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("corrida");
  const tab = ((params.get("vista") as Tab | null) ?? (runId ? "personas" : "filtros")) as Tab;
  const client = useQueryClient();
  const options = useOptions();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const runs = useQuery({ queryKey: ["research-runs"], queryFn: () => api<Run[]>("/api/research/runs") });
  const detail = useQuery({
    queryKey: ["research-run", runId],
    queryFn: () => api<RunDetail>(`/api/research/runs/${runId}`),
    enabled: Boolean(runId),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 2000 : false),
  });
  const run = runId ? detail.data : undefined;
  const status = run?.status;
  useEffect(() => {
    if (status && !ACTIVE.has(status)) client.invalidateQueries({ queryKey: ["research-runs"] });
  }, [status, client]);

  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params);
    next.set("vista", t);
    setParams(next, { replace: true });
  };
  const open = (id: string) => setParams({ corrida: id, vista: "personas" });

  const start = useMutation({
    mutationFn: (f: Filters) => api<Run>("/api/research/runs", { method: "POST", json: f }),
    onSuccess: (created) => {
      client.invalidateQueries({ queryKey: ["research-runs"] });
      open(created.id);
    },
  });

  const reuse = (r: Run) => {
    setFilters({ ...EMPTY, ...r.filters });
    setTab("filtros");
  };

  const tabs: { value: Tab; label: string; icon: ReactNode; count?: number }[] = [
    { value: "filtros", label: "Filtros de búsqueda", icon: <Search className="size-4" aria-hidden /> },
    { value: "empresas", label: "Empresas", icon: <Building2 className="size-4" aria-hidden />, count: run?.companies.length },
    { value: "personas", label: "Personas", icon: <Users className="size-4" aria-hidden />, count: run?.people.length },
  ];

  return (
    <>
      <PageHeader
        title="Búsqueda de contactos"
        description="ORKESTA busca en Google perfiles públicos de LinkedIn que cumplan tus filtros (sin entrar a LinkedIn), identifica la empresa de cada lead y te entrega su correo de una vez."
      />
      {options.data && !options.data.web_search && (
        <div className="mb-5">
          <Alert tone="blue">
            <strong>Activa el buscador de Google para encontrar leads en volumen.</strong> Crea una clave gratis (2.500 búsquedas, sin tarjeta) en serper.dev y
            agrégala en Render como <code>SERPER_API_KEY</code>.
          </Alert>
        </div>
      )}
      {runId && <RunHeader run={run} loading={detail.isLoading} error={detail.error?.message} onReuse={reuse} onClose={() => setParams({})} />}

      <div className="ork-card overflow-hidden p-0">
        <div className="flex overflow-x-auto border-b border-ork-line" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.value}
              role="tab"
              aria-selected={tab === t.value}
              onClick={() => setTab(t.value)}
              className={cx(
                "ork-focus flex shrink-0 items-center gap-2 px-5 py-3.5 text-[14px] font-semibold transition",
                tab === t.value ? "bg-ork-blue text-white" : "text-ork-graphite hover:bg-ork-ink/[0.03]",
              )}
            >
              {t.icon}
              {t.label}
              {t.count !== undefined && <span className={cx("text-[13px]", tab === t.value ? "text-white/80" : "text-ork-mist")}>({t.count})</span>}
            </button>
          ))}
        </div>
        <div className={cx(tab === "filtros" ? "p-5" : "p-0")}>
          {tab === "filtros" && (
            <div className="grid items-start gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
              <FilterPanel
                options={options.data}
                filters={filters}
                setFilters={setFilters}
                onSubmit={() => start.mutate(filters)}
                loading={start.isPending}
                error={start.error?.message}
              />
              <RecentRuns runs={runs.data} loading={runs.isLoading} onOpen={open} />
            </div>
          )}
          {tab !== "filtros" && !runId && (
            <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">
              Configura los filtros y presiona «Investigar», o abre una búsqueda reciente.
            </p>
          )}
          {tab === "personas" && run && <PeopleTable run={run} onChanged={() => detail.refetch()} />}
          {tab === "empresas" && run && <CompaniesTable run={run} />}
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
          <p className="mb-1.5 mt-3 text-[12px] font-semibold text-ork-graphite">
            {options.web_search && !named ? "Leads a buscar" : "Empresas a investigar"}
          </p>
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
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              { icon: <Database className="size-4" />, title: "Wikidata", text: "Catálogo abierto de empresas, tamaño, sede y directivos vigentes." },
              { icon: <Globe className="size-4" />, title: "Sitios oficiales", text: "Páginas de equipo y directivos, correos y teléfonos publicados." },
              { icon: <Newspaper className="size-4" />, title: "Noticias", text: "Nombramientos de los últimos 3 meses en medios de todo el mundo." },
              { icon: <Search className="size-4" />, title: "Buscador de Google", text: "Perfiles públicos de decisores y empresas en Google Maps, a gran escala y bajo costo." },
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

function EmailCell({ data, confidence }: { data: PersonData; confidence?: number }) {
  if (!data.email) return <span className="text-[12.5px] text-ork-mist">{data.domain ? "Sin formato" : "Sin dominio"}</span>;
  const label =
    data.email_status === "publicado" ? "Publicado" : (confidence ?? 0) >= 40 ? "Estimado · formato visto" : "Estimado · por validar";
  return (
    <div className="min-w-0">
      <p className="truncate text-[13px] text-ork-ink">{data.email}</p>
      <Badge tone={data.email_status === "publicado" ? "ok" : (confidence ?? 0) >= 40 ? "violet" : "warn"}>{label}</Badge>
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

function RunHeader({
  run,
  loading,
  error,
  onReuse,
  onClose,
}: {
  run?: RunDetail;
  loading: boolean;
  error?: string;
  onReuse: (r: Run) => void;
  onClose: () => void;
}) {
  const cancel = useMutation({ mutationFn: (id: string) => api(`/api/research/runs/${id}/cancel`, { method: "POST" }) });
  if (loading) return <Card className="mb-4"><Spinner /></Card>;
  if (error || !run) return <div className="mb-4"><Alert>{error ?? "No encontramos esta búsqueda."}</Alert></div>;
  const active = ACTIVE.has(run.status);
  const total = run.progress.total ?? 0;
  const done = run.progress.done ?? 0;
  const pct = run.status === "completed" ? 100 : total ? Math.max(6, Math.round((done / total) * 100)) : 4;
  const t = run.totals;
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[15px] font-bold text-ork-ink">{run.summary}</p>
          <p className="text-[12.5px] text-ork-mist">
            {formatWhen(run.created_at)}
            {run.status === "completed" &&
              ` · ${t.people ?? 0} leads · ${t.companies ?? 0} empresas · ${(t.with_email ?? 0) + (t.estimated_emails ?? 0)} con correo`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={STATUS[run.status].tone}>{STATUS[run.status].label}</Badge>
          {active ? (
            <Button size="sm" variant="secondary" icon={<Square className="size-3.5" aria-hidden />} loading={cancel.isPending} onClick={() => cancel.mutate(run.id)}>
              Detener
            </Button>
          ) : (
            <Button size="sm" variant="secondary" onClick={() => onReuse(run)}>
              Reutilizar filtros
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      </div>
      {active && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-ork-ink/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="ork-gradient h-full animate-pulse rounded-full transition-all duration-700" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-2 text-[12.5px] text-ork-graphite" aria-live="polite">
            {run.progress.log?.at(-1)?.text ?? "En cola, comenzando en unos segundos…"}
          </p>
        </div>
      )}
      {run.status === "failed" && <div className="mt-3"><Alert>{run.error}</Alert></div>}
      {run.status === "completed" && <SourceSummary totals={t} />}
    </Card>
  );
}

type SortKey = "full_name" | "title" | "company" | "location" | "email";

function exportCsv(rows: Item<PersonData>[]) {
  const head = ["Nombre", "Apellido", "Cargo", "Empresa", "Sitio web", "Ubicación", "Correo", "Estado del correo", "Perfil público"];
  const safe = (v: string) => `"${(/^[=+\-@]/.test(v) ? `'${v}` : v).replace(/"/g, '""')}"`;
  const body = rows.map((r) =>
    [r.data.first_name, r.data.last_name, r.data.title, r.data.company, r.data.domain, r.data.location, r.data.email, r.data.email_status, r.data.linkedin]
      .map((v) => safe(v ?? ""))
      .join(","),
  );
  const blob = new Blob(["﻿" + [head.join(","), ...body].join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ORKESTA_leads_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function PeopleTable({ run, onChanged }: { run: RunDetail; onChanged: () => void }) {
  const client = useQueryClient();
  const lists = useLists();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 } | null>(null);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [listId, setListId] = useState("");
  const [newList, setNewList] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => {
    setSelected(new Set());
    setPage(1);
  }, [run.id]);

  const rows = useMemo(() => {
    const words = query.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").split(/\s+/).filter(Boolean);
    let out = run.people.filter((p) => {
      if (!words.length) return true;
      const hay = [p.data.full_name, p.data.title, p.data.company, p.data.location, p.data.email]
        .join(" ")
        .toLowerCase()
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "");
      return words.every((w) => hay.includes(w));
    });
    if (sort) out = [...out].sort((a, b) => sort.dir * String(a.data[sort.key] ?? "").localeCompare(String(b.data[sort.key] ?? ""), "es"));
    return out;
  }, [run.people, query, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const current = Math.min(page, pages);
  const visible = rows.slice((current - 1) * size, current * size);
  const allChecked = visible.length > 0 && visible.every((p) => selected.has(p.id));

  const remove = useMutation({
    mutationFn: () => api<{ removed: number }>(`/api/research/runs/${run.id}/remove`, { method: "POST", json: { ids: [...selected] } }),
    onSuccess: (r) => {
      setMessage({ tone: "ok", text: `${r.removed} persona(s) quitadas de los resultados.` });
      setSelected(new Set());
      onChanged();
    },
  });
  const save = useMutation({
    mutationFn: () =>
      api<{ created: number; merged: number; list: { name: string } | null }>(`/api/research/runs/${run.id}/save`, {
        method: "POST",
        json: { ids: [...selected], list_id: listId && listId !== "nueva" ? listId : null, new_list: listId === "nueva" ? newList : "" },
      }),
    onSuccess: (r) => {
      const parts = [`${r.created} guardadas en Contactos`];
      if (r.merged) parts.push(`${r.merged} ya existían y se completaron`);
      if (r.list) parts.push(`en la lista «${r.list.name}»`);
      setMessage({ tone: "ok", text: parts.join(" · ") + "." });
      setSelected(new Set());
      setNewList("");
      client.invalidateQueries({ queryKey: ["lists"] });
      client.invalidateQueries({ queryKey: ["contacts"] });
      onChanged();
    },
    onError: (e) => setMessage({ tone: "bad", text: e.message }),
  });

  const toggleSort = (key: SortKey) =>
    setSort((s) => (s?.key === key ? (s.dir === 1 ? { key, dir: -1 } : null) : { key, dir: 1 }));
  const header = (key: SortKey, label: string, className = "") => (
    <th className={cx("px-3 py-3 font-semibold", className)}>
      <button type="button" onClick={() => toggleSort(key)} className="ork-focus inline-flex items-center gap-1 rounded hover:text-ork-ink">
        {label}
        <ArrowUpDown className={cx("size-3.5", sort?.key === key ? "text-ork-blue" : "text-ork-mist")} aria-hidden />
      </button>
    </th>
  );
  const copy = async (email: string) => {
    try {
      await navigator.clipboard.writeText(email);
      setCopied(email);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* el navegador no permitió copiar */
    }
  };

  if (run.people.length === 0) {
    return (
      <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">
        {ACTIVE.has(run.status) ? "Los leads aparecerán aquí a medida que el buscador los encuentre." : "No aparecieron leads con estos filtros. Prueba con más niveles de cargo o sin filtro de área."}
      </p>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        <Button size="sm" variant="secondary" disabled={!selected.size} loading={remove.isPending} icon={<Eraser className="size-3.5" aria-hidden />} onClick={() => remove.mutate()}>
          Quitar persona(s)
        </Button>
        <Button
          size="sm"
          disabled={!selected.size || (listId === "nueva" && newList.trim().length < 2)}
          loading={save.isPending}
          icon={<Save className="size-3.5" aria-hidden />}
          onClick={() => save.mutate()}
        >
          Guardar personas{selected.size ? ` (${selected.size})` : ""}
        </Button>
        <select
          aria-label="Lista donde guardar"
          value={listId}
          onChange={(e) => setListId(e.target.value)}
          className="ork-focus h-8 min-w-[200px] rounded-xl border border-ork-line bg-white px-3 text-[13px] text-ork-ink"
        >
          <option value="">Selecciona una lista (opcional)</option>
          {lists.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name} ({l.contacts})
            </option>
          ))}
          <option value="nueva">＋ Crear lista nueva…</option>
        </select>
        {listId === "nueva" && (
          <input
            aria-label="Nombre de la lista nueva"
            placeholder="Nombre de la lista"
            value={newList}
            onChange={(e) => setNewList(e.target.value)}
            className="ork-focus h-8 rounded-xl border border-ork-line bg-white px-3 text-[13px]"
          />
        )}
        <div className="ml-auto flex items-center gap-2">
          <label className="relative">
            <span className="sr-only">Búsqueda general</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ork-mist" aria-hidden />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(1);
              }}
              placeholder="Búsqueda general"
              className="ork-focus h-8 w-[220px] rounded-xl border border-ork-line bg-white pl-8 pr-3 text-[13px]"
            />
          </label>
          <Button size="sm" variant="ghost" icon={<Download className="size-3.5" aria-hidden />} onClick={() => exportCsv(selected.size ? rows.filter((r) => selected.has(r.id)) : rows)}>
            Exportar
          </Button>
        </div>
      </div>
      {message && (
        <div className="px-4 pb-3">
          <Alert tone={message.tone}>{message.text}</Alert>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-[13.5px]">
          <thead className="border-y border-ork-line bg-ork-ink/[0.02] text-[12.5px] text-ork-graphite">
            <tr>
              <th className="w-10 py-3 pl-4">
                <input
                  type="checkbox"
                  aria-label="Seleccionar todos en esta página"
                  className="size-4 accent-ork-blue"
                  checked={allChecked}
                  onChange={() => {
                    const next = new Set(selected);
                    visible.forEach((p) => (allChecked ? next.delete(p.id) : next.add(p.id)));
                    setSelected(next);
                  }}
                />
              </th>
              {header("full_name", "Nombre")}
              {header("title", "Cargo")}
              {header("company", "Empresa")}
              {header("location", "Ubicación")}
              {header("email", "Correo", "pr-4")}
            </tr>
          </thead>
          <tbody className="divide-y divide-ork-line">
            {visible.map((p) => (
              <Fragment key={p.id}>
                <tr className={cx("align-top hover:bg-ork-ink/[0.02]", selected.has(p.id) && "bg-ork-blue/[0.04]")}>
                  <td className="py-3.5 pl-4">
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
                  </td>
                  <td className="px-3 py-3.5">
                    <button type="button" onClick={() => setOpenRow(openRow === p.id ? null : p.id)} className="ork-focus rounded text-left font-semibold text-ork-ink hover:text-ork-blue">
                      {p.data.full_name}
                    </button>
                    <div className="mt-1 flex items-center gap-2">
                      {p.data.linkedin && (
                        <a href={p.data.linkedin} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 rounded-md bg-ork-blue/10 px-1.5 py-0.5 text-[11.5px] font-semibold text-ork-blue hover:bg-ork-blue/15">
                          <ExternalLink className="size-3" aria-hidden /> Perfil
                        </a>
                      )}
                      {p.saved_contact_id && <Badge tone="ok">En Contactos</Badge>}
                    </div>
                  </td>
                  <td className="max-w-[300px] px-3 py-3.5 text-ork-graphite">{p.data.title || "—"}</td>
                  <td className="px-3 py-3.5">
                    <p className="max-w-[220px] truncate text-ork-ink" title={p.data.company}>
                      {p.data.company}
                    </p>
                    {p.data.domain && (
                      <a href={`https://${p.data.domain}`} target="_blank" rel="noreferrer noopener" className="mt-1 inline-flex items-center gap-1 text-[12px] text-ork-mist hover:text-ork-blue">
                        <Globe className="size-3" aria-hidden /> {p.data.domain}
                      </a>
                    )}
                  </td>
                  <td className="px-3 py-3.5 text-ork-graphite">{p.data.location || "—"}</td>
                  <td className="px-3 py-3.5 pr-4">
                    {p.data.email ? (
                      <div className="flex items-start gap-1.5">
                        <EmailCell data={p.data} confidence={p.provenance.email?.confidence} />
                        <button type="button" onClick={() => copy(p.data.email)} className="ork-focus mt-0.5 rounded p-1 text-ork-mist hover:bg-ork-ink/5 hover:text-ork-ink" aria-label={`Copiar ${p.data.email}`}>
                          {copied === p.data.email ? <Check className="size-3.5 text-ok" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
                        </button>
                      </div>
                    ) : (
                      <EmailCell data={p.data} />
                    )}
                  </td>
                </tr>
                {openRow === p.id && (
                  <tr>
                    <td colSpan={6} className="p-0">
                      <PersonDetail item={p} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2 border-t border-ork-line px-4 py-3 text-[13px]">
        <PagerButton label="Primera página" disabled={current <= 1} onClick={() => setPage(1)}>
          «
        </PagerButton>
        <PagerButton label="Anterior" disabled={current <= 1} onClick={() => setPage(current - 1)}>
          ‹
        </PagerButton>
        {Array.from({ length: pages }, (_, i) => i + 1)
          .filter((n) => Math.abs(n - current) <= 2 || n === 1 || n === pages)
          .map((n) => (
            <PagerButton key={n} label={`Página ${n}`} active={n === current} onClick={() => setPage(n)}>
              {n}
            </PagerButton>
          ))}
        <PagerButton label="Siguiente" disabled={current >= pages} onClick={() => setPage(current + 1)}>
          ›
        </PagerButton>
        <PagerButton label="Última página" disabled={current >= pages} onClick={() => setPage(pages)}>
          »
        </PagerButton>
        <select
          aria-label="Filas por página"
          value={size}
          onChange={(e) => {
            setSize(Number(e.target.value));
            setPage(1);
          }}
          className="ork-focus ml-2 h-8 rounded-lg border border-ork-line bg-white px-2 text-[13px]"
        >
          {[25, 50, 100].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <span className="text-ork-mist">{rows.length} personas</span>
      </div>
    </div>
  );
}

function PagerButton({ children, label, onClick, disabled, active }: { children: ReactNode; label: string; onClick: () => void; disabled?: boolean; active?: boolean }) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-current={active ? "page" : undefined}
      disabled={disabled}
      onClick={onClick}
      className={cx(
        "ork-focus inline-flex size-8 items-center justify-center rounded-lg font-semibold disabled:opacity-40",
        active ? "bg-ork-blue/10 text-ork-blue" : "text-ork-graphite hover:bg-ork-ink/5",
      )}
    >
      {children}
    </button>
  );
}

function CompaniesTable({ run }: { run: RunDetail }) {
  if (run.companies.length === 0) {
    return <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">{ACTIVE.has(run.status) ? "Buscando…" : "Sin empresas."}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-left text-[13.5px]">
        <thead className="border-b border-ork-line bg-ork-ink/[0.02] text-[12.5px] text-ork-graphite">
          <tr>
            <th className="px-5 py-3 font-semibold">Empresa</th>
            <th className="px-3 py-3 font-semibold">Sitio web</th>
            <th className="px-3 py-3 font-semibold">Industria</th>
            <th className="px-3 py-3 font-semibold">Contacto general</th>
            <th className="px-5 py-3 font-semibold">Leads</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ork-line">
          {run.companies.map((c) => (
            <tr key={c.id}>
              <td className="px-5 py-3 font-semibold text-ork-ink">{c.data.name}</td>
              <td className="px-3 py-3">
                {c.data.domain ? (
                  <a href={c.data.website || `https://${c.data.domain}`} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-ork-blue hover:underline">
                    <Globe className="size-3.5" aria-hidden /> {c.data.domain}
                  </a>
                ) : (
                  <span className="text-ork-mist">No identificado</span>
                )}
              </td>
              <td className="px-3 py-3 text-ork-graphite">{c.data.industry || "—"}</td>
              <td className="px-3 py-3 text-[12.5px] text-ork-graphite">
                {c.data.emails[0] && <p>{c.data.emails[0]}</p>}
                {c.data.phones[0] && <p>{c.data.phones[0]}</p>}
                {!c.data.emails.length && !c.data.phones.length && "—"}
              </td>
              <td className="px-5 py-3">
                <Badge tone={c.data.people_found ? "blue" : "neutral"}>{c.data.people_found}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SourceSummary({ totals }: { totals: Record<string, number> }) {
  const sources = [
    ["Wikidata", totals.source_wikidata ?? 0],
    ["Sitios oficiales", totals.source_sitio ?? 0],
    ["Noticias", totals.source_noticia ?? 0],
    ["Perfiles públicos (buscador)", totals.source_perfil ?? 0],
  ] as const;
  return (
    <div className="mt-3 space-y-2">
      <p className="text-[12.5px] text-ork-mist">
        Hallazgos por fuente:{" "}
        {sources.map(([label, n], i) => (
          <span key={label}>
            {i > 0 && " · "}
            {label} <strong className="text-ork-graphite">{n}</strong>
          </span>
        ))}
        {totals.serp_rejected ? ` · ${totals.serp_rejected} resultados descartados (otra empresa o cargo anterior)` : ""}
      </p>
      {(totals.people ?? 0) < 5 && (
        <p className="text-[12.5px] text-ork-mist">
          ¿Pocos decisores? Revisa qué fuente falla en{" "}
          <Link to="/configuracion/integraciones" className="font-semibold text-ork-blue hover:underline">
            Configuración → Integraciones → Probar conexiones
          </Link>
          .
        </p>
      )}
      {totals.serp_fatal_clave ? (
        <Alert>La clave de Serper no es válida: el buscador de Google no se usó. Revisa SERPER_API_KEY en Render.</Alert>
      ) : totals.serp_fatal_creditos ? (
        <Alert>Se acabaron los créditos de Serper: el buscador se usó parcialmente. Recarga en serper.dev.</Alert>
      ) : null}
    </div>
  );
}

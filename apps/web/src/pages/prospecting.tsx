import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bookmark,
  BookmarkCheck,
  Building2,
  Check,
  ChevronDown,
  Copy,
  Download,
  Eraser,
  ExternalLink,
  Globe,
  Mail,
  MapPin,
  Minus,
  Phone,
  Plus,
  Save,
  Search,
  Square,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Card, CardHeader, cx, PageHeader, Spinner, Tip } from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { useLists, type Option } from "./contacts";
import { formatWhen } from "./dashboard";

type Options = {
  countries: Option[];
  sector_groups: { label: string; options: Option[] }[];
  functions: Option[];
  seniority: Option[];
  default_seniority: string[];
  headcount: Option[];
  web_search: boolean;
};
type Filters = {
  mode: "leads" | "accounts";
  country: string;
  cities: string[];
  sectors: string[];
  exclude_sectors: string[];
  sizes: string[];
  seniority: string[];
  functions: string[];
  exclude_functions: string[];
  titles: string[];
  exclude_titles: string[];
  companies: string[];
  exclude_companies: string[];
  first_name: string;
  last_name: string;
  keywords: string;
  include_base: boolean;
  limit: number;
};
type SourceRef = { type: string; label: string; url: string; date?: string; headline?: string };
type Provenance = Record<string, { label: string; url: string; confidence: number }>;
type Run = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled";
  filters: Partial<Filters>;
  summary: string;
  saved_name: string | null;
  saved_at: string | null;
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
  location?: string;
  saved?: boolean;
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
  mode: "leads",
  country: "CO",
  cities: [],
  sectors: [],
  exclude_sectors: [],
  sizes: [],
  seniority: [],
  functions: [],
  exclude_functions: [],
  titles: [],
  exclude_titles: [],
  companies: [],
  exclude_companies: [],
  first_name: "",
  last_name: "",
  keywords: "",
  include_base: true,
  limit: 25,
};
const ACTIVE = new Set(["queued", "running"]);
const STATUS: Record<Run["status"], { label: string; tone: "neutral" | "blue" | "ok" | "bad" | "warn" }> = {
  queued: { label: "En cola", tone: "neutral" },
  running: { label: "Buscando", tone: "blue" },
  completed: { label: "Completada", tone: "ok" },
  failed: { label: "Falló", tone: "bad" },
  canceled: { label: "Detenida", tone: "warn" },
};

function useOptions() {
  return useQuery({ queryKey: ["research-options"], queryFn: () => api<Options>("/api/research/options"), staleTime: Infinity });
}

const fold = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

type Tab = "filtros" | "empresas" | "personas";

export function ProspectingPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("corrida");
  const tab = ((params.get("vista") as Tab | null) ?? (runId ? "personas" : "filtros")) as Tab;
  const client = useQueryClient();
  const { session } = useSession();
  const staff = Boolean(session?.user.platform_admin);
  const options = useOptions();
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const saved = useQuery({ queryKey: ["research-runs"], queryFn: () => api<Run[]>("/api/research/runs") });
  const detail = useQuery({
    queryKey: ["research-run", runId],
    queryFn: () => api<RunDetail>(`/api/research/runs/${runId}`),
    enabled: Boolean(runId),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 2000 : false),
  });
  const run = runId ? detail.data : undefined;

  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params);
    next.set("vista", t);
    setParams(next, { replace: true });
  };
  const open = (id: string, vista: Tab = "personas") => setParams({ corrida: id, vista });

  const start = useMutation({
    mutationFn: (f: Filters) => api<Run>("/api/research/runs", { method: "POST", json: f }),
    onSuccess: (created, f) => open(created.id, f.mode === "accounts" ? "empresas" : "personas"),
  });
  const employees = (company: string, country: string) => {
    const next = { ...EMPTY, country, companies: [company] };
    setFilters(next);
    start.mutate(next);
  };

  const reuse = (r: Run) => {
    setFilters({ ...EMPTY, ...r.filters, cities: r.filters.cities ?? ((r.filters as { city?: string }).city ? [(r.filters as { city?: string }).city!] : []) });
    setTab("filtros");
  };

  const tabs: { value: Tab; label: string; icon: ReactNode; count?: number }[] = [
    { value: "filtros", label: "Filtros de búsqueda", icon: <Search className="size-4" aria-hidden /> },
    { value: "personas", label: "Posibles clientes", icon: <Users className="size-4" aria-hidden />, count: run?.people.length },
    { value: "empresas", label: "Cuentas", icon: <Building2 className="size-4" aria-hidden />, count: run?.companies.length },
  ];

  return (
    <>
      <PageHeader title="Búsqueda de contactos" description="Encuentra posibles clientes con los mismos filtros de Sales Navigator y obtén su correo de contacto." />
      {staff && options.data && !options.data.web_search && (
        <div className="mb-5">
          <Alert tone="blue">
            <strong>Solo visible para administración:</strong> falta la clave <code>SERPER_API_KEY</code> en Render para buscar leads en volumen.
          </Alert>
        </div>
      )}
      {runId && (
        <RunHeader
          run={run}
          loading={detail.isLoading}
          error={detail.error?.message}
          staff={staff}
          onReuse={reuse}
          onClose={() => setParams({})}
          onSaved={() => {
            client.invalidateQueries({ queryKey: ["research-runs"] });
            detail.refetch();
          }}
        />
      )}

      <div className="ork-card overflow-hidden p-0">
        <div className="flex overflow-x-auto border-b border-ork-line" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.value}
              role="tab"
              aria-selected={tab === t.value}
              onClick={() => setTab(t.value)}
              className={cx(
                "ork-focus relative flex shrink-0 items-center gap-2 px-5 py-3.5 text-[14px] font-semibold transition",
                tab === t.value ? "text-ork-blue" : "text-ork-graphite hover:bg-ork-ink/[0.03]",
              )}
            >
              {t.icon}
              {t.label}
              {t.count !== undefined && <span className="text-[13px] text-ork-mist">({t.count})</span>}
              {tab === t.value && <span className="ork-gradient absolute inset-x-3 bottom-0 h-[3px] rounded-t-full" aria-hidden />}
            </button>
          ))}
        </div>
        <div className={cx(tab === "filtros" ? "p-4 sm:p-5" : "p-0")}>
          {tab === "filtros" && (
            <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
              <FilterPanel
                options={options.data}
                filters={filters}
                setFilters={setFilters}
                onSubmit={() => start.mutate(filters)}
                loading={start.isPending}
                error={start.error?.message}
              />
              <SavedSearches runs={saved.data} loading={saved.isLoading} onOpen={(id, mode) => open(id, mode === "accounts" ? "empresas" : "personas")} />
            </div>
          )}
          {tab !== "filtros" && !runId && (
            <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">Configura los filtros y presiona «Buscar», o abre una búsqueda guardada.</p>
          )}
          {tab === "personas" && run && <PeopleTable run={run} onChanged={() => detail.refetch()} />}
          {tab === "empresas" && run && <CompaniesTable run={run} onChanged={() => detail.refetch()} onEmployees={(name) => employees(name, run.filters.country ?? "CO")} busy={start.isPending} />}
        </div>
      </div>
    </>
  );
}

// --- Filtros al estilo Sales Navigator ------------------------------------------------------

function Pill({ label, excluded, onRemove }: { label: string; excluded?: boolean; onRemove: () => void }) {
  return (
    <span
      className={cx(
        "inline-flex max-w-full items-center gap-1 rounded-full border py-0.5 pl-2.5 pr-1 text-[12.5px] font-semibold",
        excluded ? "border-bad/30 bg-bad/[0.07] text-bad" : "border-ork-blue/30 bg-ork-blue/[0.08] text-ork-blue",
      )}
    >
      {excluded ? <Minus className="size-3 shrink-0" aria-hidden /> : <Check className="size-3 shrink-0" aria-hidden />}
      <span className={cx("truncate", excluded && "line-through decoration-1")}>{label}</span>
      <button type="button" onClick={onRemove} aria-label={`Quitar ${label}`} className="ork-focus rounded-full p-0.5 hover:bg-black/5">
        <X className="size-3" aria-hidden />
      </button>
    </span>
  );
}

/** Fila de filtro: título, píldoras elegidas y un «+» que despliega el selector (como LinkedIn). */
function FilterRow({ title, count, pills, children, hint }: { title: string; count: number; pills: ReactNode; children: ReactNode; hint?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-ork-line py-3 last:border-b-0">
      <button type="button" onClick={() => setOpen(!open)} className="ork-focus flex w-full items-center justify-between gap-2 rounded-md text-left" aria-expanded={open}>
        <span className="text-[13.5px] font-semibold text-ork-ink">
          {title}
          {count > 0 && <span className="ml-1.5 rounded-full bg-ork-blue px-1.5 py-px text-[11px] font-bold text-white">{count}</span>}
        </span>
        <span className="inline-flex size-6 items-center justify-center rounded-full text-ork-graphite hover:bg-ork-ink/5">
          {open ? <Minus className="size-4" aria-hidden /> : <Plus className="size-4" aria-hidden />}
        </span>
      </button>
      {count > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{pills}</div>}
      {open && (
        <div className="mt-2.5">
          {hint && <p className="mb-2 text-[12px] text-ork-mist">{hint}</p>}
          {children}
        </div>
      )}
    </div>
  );
}

function IncludeExclude({ onInclude, onExclude, label }: { onInclude: () => void; onExclude?: () => void; label: string }) {
  return (
    <span className="flex shrink-0 gap-1">
      <button type="button" onClick={onInclude} aria-label={`Incluir ${label}`} className="ork-focus inline-flex items-center gap-1 rounded-full border border-ork-line px-2 py-0.5 text-[11.5px] font-semibold text-ork-blue hover:border-ork-blue hover:bg-ork-blue/5">
        <Plus className="size-3" aria-hidden /> Incluir
      </button>
      {onExclude && (
        <button type="button" onClick={onExclude} aria-label={`Excluir ${label}`} className="ork-focus inline-flex items-center gap-1 rounded-full border border-ork-line px-2 py-0.5 text-[11.5px] font-semibold text-ork-graphite hover:border-bad hover:text-bad">
          <Minus className="size-3" aria-hidden /> Excluir
        </button>
      )}
    </span>
  );
}

/** Selector con búsqueda sobre una lista de opciones (agrupadas o no). */
function OptionPicker({
  groups,
  include,
  exclude,
  onChange,
  allowExclude,
  placeholder,
}: {
  groups: { label: string; options: Option[] }[];
  include: string[];
  exclude: string[];
  onChange: (include: string[], exclude: string[]) => void;
  allowExclude?: boolean;
  placeholder: string;
}) {
  const [q, setQ] = useState("");
  const shown = groups
    .map((g) => ({ ...g, options: g.options.filter((o) => !include.includes(o.value) && !exclude.includes(o.value) && (!q || fold(o.label).includes(fold(q)) || fold(g.label).includes(fold(q)))) }))
    .filter((g) => g.options.length);
  return (
    <div className="rounded-xl border border-ork-line">
      <label className="relative block border-b border-ork-line">
        <span className="sr-only">{placeholder}</span>
        <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-ork-mist" aria-hidden />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} className="h-9 w-full rounded-t-xl bg-transparent pl-8 pr-3 text-[13px] outline-none" />
      </label>
      <div className="max-h-64 overflow-y-auto py-1">
        {shown.length === 0 && <p className="px-3 py-3 text-[12.5px] text-ork-mist">Sin coincidencias.</p>}
        {shown.map((g) => (
          <div key={g.label}>
            {groups.length > 1 && (
              <div className="sticky top-0 flex items-center justify-between gap-2 bg-ork-surface px-3 py-1.5">
                <span className="text-[11.5px] font-bold uppercase tracking-wide text-ork-graphite">{g.label}</span>
                <button type="button" className="text-[11.5px] font-semibold text-ork-blue hover:underline" onClick={() => onChange([...include, ...g.options.map((o) => o.value)], exclude)}>
                  Incluir todo
                </button>
              </div>
            )}
            {g.options.map((o) => (
              <div key={o.value} className="flex items-center justify-between gap-2 px-3 py-1.5 hover:bg-ork-ink/[0.02]">
                <span className="min-w-0 text-[13px] text-ork-ink">{o.label}</span>
                <IncludeExclude
                  label={o.label}
                  onInclude={() => onChange([...include, o.value], exclude)}
                  onExclude={allowExclude ? () => onChange(include, [...exclude, o.value]) : undefined}
                />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Texto libre: escribe y presiona Enter para agregar (Incluir) o usa «Excluir». */
function TextPicker({ placeholder, onAdd, allowExclude, max }: { placeholder: string; onAdd: (value: string, excluded: boolean) => void; allowExclude?: boolean; max: number }) {
  const [value, setValue] = useState("");
  const add = (excluded: boolean) => {
    const clean = value.trim().replace(/\s+/g, " ").slice(0, max);
    if (!clean) return;
    onAdd(clean, excluded);
    setValue("");
  };
  return (
    <div className="flex items-center gap-2">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(false);
          }
        }}
        placeholder={placeholder}
        className="ork-focus h-9 min-w-0 flex-1 rounded-xl border border-ork-line bg-ork-card px-3 text-[13px] placeholder:text-ork-mist"
      />
      <IncludeExclude label={value || placeholder} onInclude={() => add(false)} onExclude={allowExclude ? () => add(true) : undefined} />
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-ork-line bg-ork-card px-4 pt-3">
      <h3 className="text-[12px] font-bold uppercase tracking-[0.12em] text-ork-mist">{title}</h3>
      <div>{children}</div>
    </section>
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
  const set = (patch: Partial<Filters>) => setFilters({ ...filters, ...patch });
  const labels = useMemo(() => {
    const map = new Map<string, string>();
    options?.sector_groups.forEach((g) => g.options.forEach((o) => map.set(`s:${o.value}`, o.label)));
    options?.functions.forEach((o) => map.set(`f:${o.value}`, o.label));
    options?.seniority.forEach((o) => map.set(`n:${o.value}`, o.label));
    options?.headcount.forEach((o) => map.set(`h:${o.value}`, o.label));
    return map;
  }, [options]);
  if (!options) return <Card><Spinner /></Card>;

  const without = (list: string[], v: string) => list.filter((x) => x !== v);
  const textPills = (key: "titles" | "companies" | "cities", exKey?: "exclude_titles" | "exclude_companies") => (
    <>
      {filters[key].map((v) => <Pill key={`i${v}`} label={v} onRemove={() => set({ [key]: without(filters[key], v) })} />)}
      {exKey && filters[exKey].map((v) => <Pill key={`e${v}`} label={v} excluded onRemove={() => set({ [exKey]: without(filters[exKey], v) })} />)}
    </>
  );
  const addText = (key: "titles" | "companies" | "cities", exKey: "exclude_titles" | "exclude_companies" | null, top: number) => (value: string, excluded: boolean) => {
    const target = excluded && exKey ? exKey : key;
    if (filters[target].some((v) => fold(v) === fold(value)) || filters[target].length >= top) return;
    set({ [target]: [...filters[target], value] });
  };
  const total =
    filters.cities.length + filters.sectors.length + filters.exclude_sectors.length + filters.sizes.length + filters.seniority.length +
    filters.functions.length + filters.exclude_functions.length + filters.titles.length + filters.exclude_titles.length +
    filters.companies.length + filters.exclude_companies.length + (filters.first_name ? 1 : 0) + (filters.last_name ? 1 : 0) + (filters.keywords ? 1 : 0);
  const accounts = filters.mode === "accounts";
  const sizesRow = (title: string) => (
    <FilterRow
      title={title}
      count={filters.sizes.length}
      pills={filters.sizes.map((v) => <Pill key={v} label={`${labels.get(`h:${v}`) ?? v} empleados`} onRemove={() => set({ sizes: without(filters.sizes, v) })} />)}
      hint="Se aplica a las empresas cuyo tamaño se conoce."
    >
      <div className="grid grid-cols-2 gap-1.5">
        {options.headcount.map((o) => {
          const on = filters.sizes.includes(o.value);
          return (
            <label key={o.value} className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-[13px] text-ork-ink hover:bg-ork-ink/[0.03]">
              <input type="checkbox" className="size-4 accent-ork-blue" checked={on} onChange={() => set({ sizes: on ? without(filters.sizes, o.value) : [...filters.sizes, o.value] })} />
              {o.label}
            </label>
          );
        })}
      </div>
    </FilterRow>
  );
  const sectorRow = (title: string) => (
    <FilterRow
      title={title}
      count={filters.sectors.length + filters.exclude_sectors.length}
      pills={
        <>
          {filters.sectors.map((v) => <Pill key={`i${v}`} label={labels.get(`s:${v}`) ?? v} onRemove={() => set({ sectors: without(filters.sectors, v) })} />)}
          {filters.exclude_sectors.map((v) => <Pill key={`e${v}`} label={labels.get(`s:${v}`) ?? v} excluded onRemove={() => set({ exclude_sectors: without(filters.exclude_sectors, v) })} />)}
        </>
      }
    >
      <OptionPicker
        groups={options.sector_groups}
        include={filters.sectors}
        exclude={filters.exclude_sectors}
        onChange={(sectors, exclude_sectors) => set({ sectors, exclude_sectors })}
        allowExclude
        placeholder="Agregar industrias"
      />
    </FilterRow>
  );

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="inline-flex self-start rounded-xl border border-ork-line bg-ork-card p-1" role="tablist" aria-label="Tipo de búsqueda">
        {(
          [
            ["leads", "Posible cliente"],
            ["accounts", "Cuentas"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={filters.mode === value}
            onClick={() => set({ mode: value })}
            className={cx("ork-focus rounded-lg px-4 py-1.5 text-[13.5px] font-semibold transition", filters.mode === value ? "ork-gradient text-white shadow-sm" : "text-ork-graphite hover:text-ork-ink")}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-3 rounded-2xl border border-ork-line bg-ork-surface/60 p-3 sm:flex-row sm:items-center">
        <label className="relative flex-1">
          <span className="sr-only">Palabras clave</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ork-mist" aria-hidden />
          <input
            value={filters.keywords}
            onChange={(e) => set({ keywords: e.target.value.slice(0, 80) })}
            placeholder="Buscar por palabras clave"
            className="ork-focus h-11 w-full rounded-xl border border-ork-line bg-ork-card pl-9 pr-3 text-[14px] placeholder:text-ork-mist"
          />
        </label>
        <select
          aria-label="País"
          value={filters.country}
          onChange={(e) => set({ country: e.target.value })}
          className="ork-focus h-11 rounded-xl border border-ork-line bg-ork-card px-3 text-[14px] text-ork-ink sm:w-48"
        >
          {options.countries.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <Button type="submit" size="lg" loading={loading} icon={<Search className="size-4" aria-hidden />}>
          Buscar
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13px] text-ork-graphite">
          {total ? (
            <>
              <strong className="text-ork-ink">{total}</strong> filtro(s) aplicados
            </>
          ) : (
            "Elige los filtros de tu cliente ideal."
          )}
        </p>
        <div className="flex items-center gap-3">
          {total > 0 && (
            <button type="button" className="text-[12.5px] font-semibold text-ork-blue hover:underline" onClick={() => setFilters({ ...EMPTY, country: filters.country, limit: filters.limit })}>
              Borrar todo
            </button>
          )}
        </div>
      </div>

      {accounts ? (
        <div className="grid items-start gap-4 lg:grid-cols-2">
          <Section title="Características de la empresa">
            {sizesRow("Empleados en la empresa")}
            <FilterRow title="Ubicación de la sede central" count={filters.cities.length} pills={textPills("cities")} hint="El país se elige arriba. Agrega ciudades o regiones para acotar (hasta 5).">
              <TextPicker placeholder="Ej.: Bogotá" onAdd={addText("cities", null, 5)} max={80} />
            </FilterRow>
            {sectorRow("Industria")}
          </Section>
        </div>
      ) : (
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          <Section title="Empresa">
            <FilterRow
              title="Empresa actual"
              count={filters.companies.length + filters.exclude_companies.length}
              pills={textPills("companies", "exclude_companies")}
              hint="Escribe el nombre o el dominio y presiona Enter. Con empresas incluidas se buscan los decisores de esas empresas."
            >
              <TextPicker placeholder="Ej.: Bancolombia o ecopetrol.com" onAdd={addText("companies", "exclude_companies", 25)} allowExclude max={120} />
            </FilterRow>
            <FilterRow
              title="Plantilla de la empresa"
              count={filters.sizes.length}
              pills={filters.sizes.map((v) => <Pill key={v} label={`${labels.get(`h:${v}`) ?? v} empleados`} onRemove={() => set({ sizes: without(filters.sizes, v) })} />)}
              hint="Se aplica a las empresas cuyo tamaño se conoce."
            >
              <div className="grid grid-cols-2 gap-1.5">
                {options.headcount.map((o) => {
                  const on = filters.sizes.includes(o.value);
                  return (
                    <label key={o.value} className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-[13px] text-ork-ink hover:bg-ork-ink/[0.03]">
                      <input type="checkbox" className="size-4 accent-ork-blue" checked={on} onChange={() => set({ sizes: on ? without(filters.sizes, o.value) : [...filters.sizes, o.value] })} />
                      {o.label}
                    </label>
                  );
                })}
              </div>
            </FilterRow>
          </Section>

          <Section title="Rol">
            <FilterRow
              title="Función"
              count={filters.functions.length + filters.exclude_functions.length}
              pills={
                <>
                  {filters.functions.map((v) => <Pill key={`i${v}`} label={labels.get(`f:${v}`) ?? v} onRemove={() => set({ functions: without(filters.functions, v) })} />)}
                  {filters.exclude_functions.map((v) => <Pill key={`e${v}`} label={labels.get(`f:${v}`) ?? v} excluded onRemove={() => set({ exclude_functions: without(filters.exclude_functions, v) })} />)}
                </>
              }
            >
              <OptionPicker
                groups={[{ label: "Función", options: options.functions }]}
                include={filters.functions}
                exclude={filters.exclude_functions}
                onChange={(functions, exclude_functions) => set({ functions, exclude_functions })}
                allowExclude
                placeholder="Agregar funciones"
              />
            </FilterRow>
            <FilterRow
              title="Cargo actual"
              count={filters.titles.length + filters.exclude_titles.length}
              pills={textPills("titles", "exclude_titles")}
              hint="Escribe un cargo y presiona Enter. Ej.: Gerente de Compras."
            >
              <TextPicker placeholder="Agregar cargos" onAdd={addText("titles", "exclude_titles", 10)} allowExclude max={80} />
            </FilterRow>
            <FilterRow
              title="Nivel de responsabilidad"
              count={filters.seniority.length}
              pills={filters.seniority.map((v) => <Pill key={v} label={labels.get(`n:${v}`) ?? v} onRemove={() => set({ seniority: without(filters.seniority, v) })} />)}
              hint="Sin selección se buscan los niveles de decisión: desde Gestión inicial hasta Propietario / Socio."
            >
              <OptionPicker
                groups={[{ label: "Nivel", options: options.seniority }]}
                include={filters.seniority}
                exclude={[]}
                onChange={(seniority) => set({ seniority })}
                placeholder="Agregar niveles"
              />
            </FilterRow>
          </Section>
        </div>

        <div className="flex flex-col gap-4">
          <Section title="Personal">
            <FilterRow
              title="Geografía"
              count={filters.cities.length}
              pills={textPills("cities")}
              hint="El país se elige arriba. Agrega ciudades o regiones para acotar (hasta 5)."
            >
              <TextPicker placeholder="Ej.: Medellín" onAdd={addText("cities", null, 5)} max={80} />
            </FilterRow>
            <FilterRow
              title="Sector"
              count={filters.sectors.length + filters.exclude_sectors.length}
              pills={
                <>
                  {filters.sectors.map((v) => <Pill key={`i${v}`} label={labels.get(`s:${v}`) ?? v} onRemove={() => set({ sectors: without(filters.sectors, v) })} />)}
                  {filters.exclude_sectors.map((v) => <Pill key={`e${v}`} label={labels.get(`s:${v}`) ?? v} excluded onRemove={() => set({ exclude_sectors: without(filters.exclude_sectors, v) })} />)}
                </>
              }
            >
              <OptionPicker
                groups={options.sector_groups}
                include={filters.sectors}
                exclude={filters.exclude_sectors}
                onChange={(sectors, exclude_sectors) => set({ sectors, exclude_sectors })}
                allowExclude
                placeholder="Agregar sectores"
              />
            </FilterRow>
            <FilterRow
              title="Nombre"
              count={filters.first_name ? 1 : 0}
              pills={filters.first_name && <Pill label={filters.first_name} onRemove={() => set({ first_name: "" })} />}
            >
              <input
                value={filters.first_name}
                onChange={(e) => set({ first_name: e.target.value.slice(0, 60) })}
                placeholder="Nombre"
                className="ork-focus h-9 w-full rounded-xl border border-ork-line bg-ork-card px-3 text-[13px] placeholder:text-ork-mist"
              />
            </FilterRow>
            <FilterRow
              title="Apellido"
              count={filters.last_name ? 1 : 0}
              pills={filters.last_name && <Pill label={filters.last_name} onRemove={() => set({ last_name: "" })} />}
            >
              <input
                value={filters.last_name}
                onChange={(e) => set({ last_name: e.target.value.slice(0, 60) })}
                placeholder="Apellido"
                className="ork-focus h-9 w-full rounded-xl border border-ork-line bg-ork-card px-3 text-[13px] placeholder:text-ork-mist"
              />
            </FilterRow>
          </Section>
        </div>
      </div>
      )}
      {error && <Alert>{error}</Alert>}
    </form>
  );
}

// --- Búsquedas guardadas --------------------------------------------------------------------

function SavedSearches({ runs, loading, onOpen }: { runs?: Run[]; loading: boolean; onOpen: (id: string, mode?: string) => void }) {
  const client = useQueryClient();
  const forget = useMutation({
    mutationFn: (id: string) => api(`/api/research/runs/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["research-runs"] }),
  });
  return (
    <Card className="xl:sticky xl:top-4">
      <CardHeader title="Búsquedas guardadas" />
      {loading ? (
        <Spinner />
      ) : !runs?.length ? (
        <div className="py-6 text-center">
          <Bookmark className="mx-auto size-6 text-ork-mist" aria-hidden />
          <p className="mt-2 text-[13px] text-ork-mist">Aún no guardas búsquedas. Después de buscar, usa «Guardar búsqueda» para tenerla aquí.</p>
        </div>
      ) : (
        <ul className="divide-y divide-ork-line">
          {runs.map((r) => (
            <li key={r.id} className="flex items-start gap-2 py-2.5">
              <button onClick={() => onOpen(r.id, r.filters.mode)} className="ork-focus min-w-0 flex-1 rounded-lg px-1 text-left hover:text-ork-blue">
                <p className="truncate text-[13.5px] font-semibold text-ork-ink">{r.saved_name}</p>
                <p className="truncate text-[12px] text-ork-mist">{r.summary}</p>
                <p className="text-[11.5px] text-ork-mist">
                  {formatWhen(r.saved_at ?? r.created_at)} · {r.filters.mode === "accounts" ? `${r.totals.companies ?? 0} cuentas` : `${r.totals.people ?? 0} posibles clientes`}
                </p>
              </button>
              <button
                type="button"
                aria-label={`Borrar la búsqueda ${r.saved_name}`}
                onClick={() => forget.mutate(r.id)}
                className="ork-focus rounded-lg p-1.5 text-ork-mist hover:bg-bad/10 hover:text-bad"
              >
                <Trash2 className="size-4" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- Resultados -----------------------------------------------------------------------------

function EmailCell({ data, confidence, why }: { data: PersonData; confidence?: number; why?: string }) {
  if (!data.email) return <span className="text-[12.5px] text-ork-mist">{data.domain ? "El dominio no recibe correo" : "Sin sitio web identificado"}</span>;
  const c = confidence ?? 0;
  const label =
    data.email_status === "publicado" ? "Publicado" : c >= 70 ? "Formato confirmado" : c >= 40 ? "Estimado · formato visto" : "Estimado · por validar";
  return (
    <div className="min-w-0">
      <p className="truncate text-[13px] text-ork-ink">{data.email}</p>
      <Tip label={why || label} className="max-w-full">
        <Badge tone={data.email_status === "publicado" || c >= 70 ? "ok" : c >= 40 ? "violet" : "warn"}>{label}</Badge>
      </Tip>
    </div>
  );
}


function PersonDetail({ item }: { item: Item<PersonData> }) {
  const news = item.sources.filter((src) => src.type === "noticia");
  return (
    <div className="grid gap-4 bg-ork-surface/70 px-5 py-4 sm:grid-cols-2">
      <div className="space-y-2 text-[13px]">
        <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ork-mist">Datos de contacto</p>
        {item.data.email && (
          <p className="flex items-center gap-2 text-ork-graphite">
            <Mail className="size-3.5" aria-hidden /> {item.data.email}
          </p>
        )}
        {item.data.phone && (
          <p className="flex items-center gap-2 text-ork-graphite">
            <Phone className="size-3.5" aria-hidden /> {item.data.phone} <span className="text-ork-mist">(conmutador de la empresa)</span>
          </p>
        )}
        {item.data.domain && (
          <a href={`https://${item.data.domain}`} target="_blank" rel="noreferrer noopener" className="flex items-center gap-2 text-ork-blue hover:underline">
            <Globe className="size-3.5" aria-hidden /> {item.data.domain}
          </a>
        )}
        {item.data.linkedin && (
          <a href={item.data.linkedin} target="_blank" rel="noreferrer noopener" className="flex items-center gap-2 text-ork-blue hover:underline">
            <ExternalLink className="size-3.5" aria-hidden /> Ver perfil en LinkedIn
          </a>
        )}
      </div>
      {news.length > 0 && (
        <div className="space-y-2">
          <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ork-mist">Noticias recientes</p>
          {news.map((src) => (
            <a key={src.url} href={src.url} target="_blank" rel="noreferrer noopener" className="block rounded-lg border border-ork-line bg-ork-card p-2.5 hover:border-ork-blue/40">
              <span className="text-[12.5px] font-semibold text-ork-ink">{src.headline}</span>
              <span className="mt-0.5 block text-[11.5px] text-ork-mist">{src.label}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

const STEPS: Record<string, string> = {
  en_cola: "En cola, comenzando en unos segundos…",
  leads: "Buscando posibles clientes que cumplen tus filtros…",
  dominios: "Identificando la empresa de cada posible cliente…",
  correos: "Buscando el correo de contacto…",
  cuentas: "Buscando cuentas que cumplen tus filtros…",
};

function RunHeader({
  run,
  loading,
  error,
  staff,
  onReuse,
  onClose,
  onSaved,
}: {
  run?: RunDetail;
  loading: boolean;
  error?: string;
  staff: boolean;
  onReuse: (r: Run) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const cancel = useMutation({ mutationFn: (id: string) => api(`/api/research/runs/${id}/cancel`, { method: "POST" }) });
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () => api<Run>(`/api/research/runs/${run!.id}/saved`, { method: "POST", json: { name } }),
    onSuccess: () => {
      setNaming(false);
      onSaved();
    },
  });
  if (loading) return <Card className="mb-4"><Spinner /></Card>;
  if (error || !run) return <div className="mb-4"><Alert>{error ?? "Esta búsqueda ya no está disponible: solo se conservan las búsquedas guardadas."}</Alert></div>;
  const active = ACTIVE.has(run.status);
  const total = run.progress.total ?? 0;
  const done = run.progress.done ?? 0;
  const pct = run.status === "completed" ? 100 : total ? Math.max(6, Math.round((done / total) * 100)) : 4;
  const t = run.totals;
  const step = STEPS[run.progress.step ?? "en_cola"] ?? "Buscando…";
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[15px] font-bold text-ork-ink">
            {run.saved_name && <BookmarkCheck className="size-4 shrink-0 text-ork-blue" aria-label="Búsqueda guardada" />}
            <span className="truncate">{run.saved_name ?? run.summary}</span>
          </p>
          <p className="text-[12.5px] text-ork-mist">
            {run.saved_name && `${run.summary} · `}
            {formatWhen(run.created_at)}
            {run.status === "completed" &&
              (run.filters.mode === "accounts"
                ? ` · ${t.companies ?? 0} cuentas · ${t.with_domain ?? 0} con sitio web`
                : ` · ${t.people ?? 0} posibles clientes · ${t.companies ?? 0} cuentas · ${(t.with_email ?? 0) + (t.estimated_emails ?? 0)} con correo`)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS[run.status].tone}>{STATUS[run.status].label}</Badge>
          {active ? (
            <Button size="sm" variant="secondary" icon={<Square className="size-3.5" aria-hidden />} loading={cancel.isPending} onClick={() => cancel.mutate(run.id)}>
              Detener
            </Button>
          ) : (
            <>
              {!run.saved_name && (
                <Button size="sm" icon={<Bookmark className="size-3.5" aria-hidden />} onClick={() => { setName(run.summary.slice(0, 120)); setNaming(true); }}>
                  Guardar búsqueda
                </Button>
              )}
              <Button size="sm" variant="secondary" onClick={() => onReuse(run)}>
                Editar filtros
              </Button>
            </>
          )}
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      </div>
      {naming && (
        <form
          className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-ork-line bg-ork-surface/60 p-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <input
            autoFocus
            aria-label="Nombre de la búsqueda"
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            className="ork-focus h-9 min-w-[220px] flex-1 rounded-xl border border-ork-line bg-ork-card px-3 text-[13.5px]"
          />
          <Button size="sm" type="submit" loading={save.isPending} disabled={name.trim().length < 2}>
            Guardar
          </Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setNaming(false)}>
            Cancelar
          </Button>
          {save.error && <p className="w-full text-[12.5px] text-bad">{save.error.message}</p>}
        </form>
      )}
      {!run.saved_name && !active && !naming && (
        <p className="mt-2 text-[12px] text-ork-mist">Esta búsqueda no queda en el historial a menos que la guardes.</p>
      )}
      {active && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-ork-ink/5" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="ork-gradient h-full animate-pulse rounded-full transition-all duration-700" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-2 text-[12.5px] text-ork-graphite" aria-live="polite">
            {step}
            {(run.progress.step === "leads" || run.progress.step === "cuentas") && done ? ` ${done} encontrados hasta ahora` : ""}
          </p>
        </div>
      )}
      {run.status === "failed" && <div className="mt-3"><Alert>{run.error}</Alert></div>}
      {run.status === "completed" && staff && <OperatorAlerts totals={t} />}
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

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

/** Resultados como la lista de posibles clientes de Sales Navigator. */
function PeopleTable({ run, onChanged }: { run: RunDetail; onChanged: () => void }) {
  const client = useQueryClient();
  const lists = useLists();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey | "">("");
  const [page, setPage] = useState(1);
  const size = 25; // 25 por página, sin tope total de resultados
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
    const words = fold(query).split(/\s+/).filter(Boolean);
    let out = run.people.filter((p) => {
      if (!words.length) return true;
      const hay = fold([p.data.full_name, p.data.title, p.data.company, p.data.location, p.data.email].join(" "));
      return words.every((w) => hay.includes(w));
    });
    if (sort) out = [...out].sort((a, b) => String(a.data[sort] ?? "").localeCompare(String(b.data[sort] ?? ""), "es"));
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
    mutationFn: (ids: string[]) =>
      api<{ created: number; merged: number; over_limit: number; list: { name: string } | null }>(`/api/research/runs/${run.id}/save`, {
        method: "POST",
        json: { ids, list_id: listId && listId !== "nueva" ? listId : null, new_list: listId === "nueva" ? newList : "" },
      }),
    onSuccess: (r) => {
      const parts = [`${r.created} guardadas en Contactos`];
      if (r.merged) parts.push(`${r.merged} ya existían y se completaron`);
      if (r.list) parts.push(`en la lista «${r.list.name}»`);
      if (r.over_limit) parts.push(`${r.over_limit} sin guardar: llegaste al límite de contactos de tu plan`);
      setMessage({ tone: r.over_limit ? "bad" : "ok", text: parts.join(" · ") + "." });
      setSelected(new Set());
      setNewList("");
      client.invalidateQueries({ queryKey: ["lists"] });
      client.invalidateQueries({ queryKey: ["contacts"] });
      onChanged();
    },
    onError: (e) => setMessage({ tone: "bad", text: e.message }),
  });
  const copy = async (email: string) => {
    try {
      await navigator.clipboard.writeText(email);
      setCopied(email);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* el navegador no permitió copiar */
    }
  };
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const newListMissing = listId === "nueva" && newList.trim().length < 2;

  if (run.people.length === 0) {
    return (
      <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">
        {ACTIVE.has(run.status) ? "Los posibles clientes aparecerán aquí a medida que se encuentren." : "No aparecieron posibles clientes con estos filtros. Prueba con menos filtros o más niveles de responsabilidad."}
      </p>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-ork-line px-4 py-3">
        <label className="flex items-center gap-2 pr-2 text-[13px] font-semibold text-ork-ink">
          <input
            type="checkbox"
            aria-label="Seleccionar todo en esta página"
            className="size-4 accent-ork-blue"
            checked={allChecked}
            onChange={() => {
              const next = new Set(selected);
              visible.forEach((p) => (allChecked ? next.delete(p.id) : next.add(p.id)));
              setSelected(next);
            }}
          />
          Seleccionar todo
        </label>
        <select
          aria-label="Lista donde guardar"
          value={listId}
          onChange={(e) => setListId(e.target.value)}
          className="ork-focus h-8 min-w-[190px] rounded-xl border border-ork-line bg-ork-card px-3 text-[13px] text-ork-ink"
        >
          <option value="">Solo en Contactos (sin lista)</option>
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
            className="ork-focus h-8 rounded-xl border border-ork-line bg-ork-card px-3 text-[13px]"
          />
        )}
        <Button size="sm" disabled={!selected.size || newListMissing} loading={save.isPending && selected.size > 0} icon={<Save className="size-3.5" aria-hidden />} onClick={() => save.mutate([...selected])}>
          Guardar en la lista{selected.size ? ` (${selected.size})` : ""}
        </Button>
        <Button size="sm" variant="ghost" disabled={!selected.size} loading={remove.isPending} icon={<Eraser className="size-3.5" aria-hidden />} onClick={() => remove.mutate()}>
          Quitar
        </Button>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="relative">
            <span className="sr-only">Buscar en los resultados</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ork-mist" aria-hidden />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(1);
              }}
              placeholder="Buscar en los resultados"
              className="ork-focus h-8 w-[200px] rounded-xl border border-ork-line bg-ork-card pl-8 pr-3 text-[13px]"
            />
          </label>
          <select aria-label="Ordenar" value={sort} onChange={(e) => setSort(e.target.value as SortKey | "")} className="ork-focus h-8 rounded-xl border border-ork-line bg-ork-card px-2 text-[13px]">
            <option value="">Más relevantes</option>
            <option value="full_name">Nombre</option>
            <option value="company">Empresa</option>
            <option value="title">Cargo</option>
            <option value="location">Ubicación</option>
          </select>
          <Button size="sm" variant="ghost" icon={<Download className="size-3.5" aria-hidden />} onClick={() => exportCsv(selected.size ? rows.filter((r) => selected.has(r.id)) : rows)}>
            Exportar
          </Button>
        </div>
      </div>
      {message && (
        <div className="px-4 pt-3">
          <Alert tone={message.tone}>{message.text}</Alert>
        </div>
      )}
      <ul className="divide-y divide-ork-line">
        {visible.map((p) => (
          <Fragment key={p.id}>
            <li className={cx("flex gap-3 px-4 py-4 transition hover:bg-ork-ink/[0.015] sm:gap-4", selected.has(p.id) && "bg-ork-blue/[0.04]")}>
              <input type="checkbox" aria-label={`Seleccionar a ${p.data.full_name}`} className="mt-4 size-4 shrink-0 accent-ork-blue" checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
              <span className="ork-gradient flex size-12 shrink-0 items-center justify-center rounded-full text-[15px] font-bold text-white" aria-hidden>
                {initials(p.data.full_name)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  {p.data.linkedin ? (
                    <a href={p.data.linkedin} target="_blank" rel="noreferrer noopener" className="text-[15px] font-bold text-ork-blue hover:underline">
                      {p.data.full_name}
                    </a>
                  ) : (
                    <span className="text-[15px] font-bold text-ork-ink">{p.data.full_name}</span>
                  )}
                  {p.data.linkedin && (
                    <a href={p.data.linkedin} target="_blank" rel="noreferrer noopener" aria-label={`Perfil de LinkedIn de ${p.data.full_name}`} className="inline-flex size-5 items-center justify-center rounded bg-[#0A66C2] text-[10px] font-bold text-white">
                      in
                    </a>
                  )}
                  {p.saved_contact_id && <Badge tone="ok">Guardado</Badge>}
                </div>
                <p className="mt-0.5 text-[13.5px] text-ork-ink">
                  {p.data.title || "Cargo no publicado"}
                  {p.data.company && (
                    <>
                      <span className="text-ork-mist"> · </span>
                      <span className="font-semibold">{p.data.company}</span>
                    </>
                  )}
                </p>
                {p.data.location && (
                  <p className="mt-0.5 flex items-center gap-1 text-[12.5px] text-ork-mist">
                    <MapPin className="size-3" aria-hidden /> {p.data.location}
                  </p>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {p.data.email ? (
                    <span className="flex items-start gap-1.5">
                      <EmailCell data={p.data} confidence={p.provenance.email?.confidence} why={p.provenance.email?.label} />
                      <button type="button" onClick={() => copy(p.data.email)} className="ork-focus mt-0.5 rounded p-1 text-ork-mist hover:bg-ork-ink/5 hover:text-ork-ink" aria-label={`Copiar ${p.data.email}`}>
                        {copied === p.data.email ? <Check className="size-3.5 text-ok" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
                      </button>
                    </span>
                  ) : (
                    <EmailCell data={p.data} />
                  )}
                  {p.data.linkedin ? (
                    <a href={p.data.linkedin} target="_blank" rel="noreferrer noopener" className="inline-flex max-w-[260px] items-center gap-1 truncate rounded-md bg-[#0A66C2]/10 px-2 py-0.5 text-[12px] font-semibold text-[#0A66C2] hover:bg-[#0A66C2]/15">
                      <ExternalLink className="size-3 shrink-0" aria-hidden /> {p.data.linkedin.replace(/^https?:\/\/([a-z]{2,3}\.)?/, "")}
                    </a>
                  ) : (
                    <span className="text-[12px] text-ork-mist">Sin perfil público</span>
                  )}
                  <button type="button" onClick={() => setOpenRow(openRow === p.id ? null : p.id)} className="ork-focus inline-flex items-center gap-1 rounded text-[12.5px] font-semibold text-ork-graphite hover:text-ork-blue" aria-expanded={openRow === p.id}>
                    Acerca de <ChevronDown className={cx("size-3.5 transition", openRow === p.id && "rotate-180")} aria-hidden />
                  </button>
                </div>
              </div>
              <div className="shrink-0">
                <Button size="sm" variant={p.saved_contact_id ? "secondary" : "primary"} disabled={Boolean(p.saved_contact_id) || newListMissing} onClick={() => save.mutate([p.id])} icon={p.saved_contact_id ? <Check className="size-3.5" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}>
                  {p.saved_contact_id ? "Guardado" : "Guardar"}
                </Button>
              </div>
            </li>
            {openRow === p.id && (
              <li>
                <PersonDetail item={p} />
              </li>
            )}
          </Fragment>
        ))}
      </ul>
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
        <span className="text-ork-mist">{rows.length} posibles clientes</span>
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

function CompanyLogo({ name, domain }: { name: string; domain: string }) {
  const [failed, setFailed] = useState(false);
  if (domain && !failed) {
    return (
      <img
        src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=128`}
        alt=""
        onError={() => setFailed(true)}
        className="size-14 shrink-0 rounded-xl border border-ork-line bg-ork-card object-contain p-1.5"
      />
    );
  }
  return (
    <span className="ork-gradient flex size-14 shrink-0 items-center justify-center rounded-xl text-[17px] font-bold text-white" aria-hidden>
      {initials(name)}
    </span>
  );
}

/** Cuentas como en Sales Navigator: logo, industria · plantilla, «Acerca de» y acciones. */
function CompaniesTable({ run, onChanged, onEmployees, busy }: { run: RunDetail; onChanged: () => void; onEmployees: (name: string) => void; busy: boolean }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (ids: string[]) => api<{ saved: number }>(`/api/research/runs/${run.id}/accounts`, { method: "POST", json: { ids } }),
    onSuccess: (r) => {
      setMessage(`${r.saved} cuenta(s) guardadas en tu base.`);
      setSelected(new Set());
      onChanged();
    },
  });
  useEffect(() => setPage(1), [run.id]);
  if (run.companies.length === 0) {
    return <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">{ACTIVE.has(run.status) ? "Las cuentas aparecerán aquí a medida que se encuentren." : "Sin cuentas con estos filtros."}</p>;
  }
  const size = 25;
  const pages = Math.max(1, Math.ceil(run.companies.length / size));
  const current = Math.min(page, pages);
  const visible = run.companies.slice((current - 1) * size, current * size);
  const allChecked = visible.length > 0 && visible.every((c) => selected.has(c.id));
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-ork-line px-4 py-3">
        <label className="flex items-center gap-2 pr-2 text-[13px] font-semibold text-ork-ink">
          <input
            type="checkbox"
            aria-label="Seleccionar todo en esta página"
            className="size-4 accent-ork-blue"
            checked={allChecked}
            onChange={() => {
              const next = new Set(selected);
              visible.forEach((c) => (allChecked ? next.delete(c.id) : next.add(c.id)));
              setSelected(next);
            }}
          />
          Seleccionar todo
        </label>
        <Button size="sm" disabled={!selected.size} loading={save.isPending} icon={<Save className="size-3.5" aria-hidden />} onClick={() => save.mutate([...selected])}>
          Guardar en mi base{selected.size ? ` (${selected.size})` : ""}
        </Button>
        <span className="ml-auto text-[13px] font-semibold text-ork-graphite">{run.companies.length} resultados</span>
      </div>
      {message && (
        <div className="px-4 pt-3">
          <Alert tone="ok">{message}</Alert>
        </div>
      )}
      <ul className="divide-y divide-ork-line">
        {visible.map((c) => {
          const d = c.data;
          const link = d.linkedin || d.website || (d.domain ? `https://${d.domain}` : "");
          const open = expanded.has(c.id);
          return (
            <li key={c.id} className={cx("flex gap-3 px-4 py-4 sm:gap-4", selected.has(c.id) && "bg-ork-blue/[0.04]")}>
              <input
                type="checkbox"
                aria-label={`Seleccionar ${d.name}`}
                className="mt-5 size-4 shrink-0 accent-ork-blue"
                checked={selected.has(c.id)}
                onChange={() => {
                  const next = new Set(selected);
                  if (next.has(c.id)) next.delete(c.id);
                  else next.add(c.id);
                  setSelected(next);
                }}
              />
              <CompanyLogo name={d.name} domain={d.domain} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {link ? (
                    <a href={link} target="_blank" rel="noreferrer noopener" className="text-[15px] font-bold text-ork-blue hover:underline">
                      {d.name}
                    </a>
                  ) : (
                    <span className="text-[15px] font-bold text-ork-ink">{d.name}</span>
                  )}
                  {d.linkedin && (
                    <a href={d.linkedin} target="_blank" rel="noreferrer noopener" aria-label={`LinkedIn de ${d.name}`} className="inline-flex size-5 items-center justify-center rounded bg-[#0A66C2] text-[10px] font-bold text-white">
                      in
                    </a>
                  )}
                  {d.saved && <Badge tone="ok">Guardada</Badge>}
                </div>
                <p className="mt-0.5 text-[13.5px] text-ork-ink">
                  {[d.industry, d.employees ? `${new Intl.NumberFormat("es").format(d.employees)} empleados` : d.size ? `${d.size} empleados` : ""].filter(Boolean).join(" · ") || "Industria no publicada"}
                </p>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[12.5px] text-ork-mist">
                  {(d.location || d.city) && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin className="size-3" aria-hidden /> {d.location || d.city}
                    </span>
                  )}
                  {d.domain && (
                    <a href={d.website || `https://${d.domain}`} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:text-ork-blue">
                      <Globe className="size-3" aria-hidden /> {d.domain}
                    </a>
                  )}
                  {d.phones[0] && (
                    <span className="inline-flex items-center gap-1">
                      <Phone className="size-3" aria-hidden /> {d.phones[0]}
                    </span>
                  )}
                </div>
                {d.description && (
                  <p className={cx("mt-2 text-[13px] text-ork-graphite", !open && "line-clamp-2")}>
                    <strong className="text-ork-ink">Acerca de:</strong> {d.description}{" "}
                    {d.description.length > 140 && (
                      <button
                        type="button"
                        className="font-semibold text-ork-ink hover:underline"
                        onClick={() => {
                          const next = new Set(expanded);
                          if (open) next.delete(c.id);
                          else next.add(c.id);
                          setExpanded(next);
                        }}
                      >
                        {open ? "Mostrar menos" : "Mostrar más"}
                      </button>
                    )}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 flex-col items-end gap-2">
                <Button size="sm" variant={d.saved ? "secondary" : "primary"} disabled={d.saved} onClick={() => save.mutate([c.id])} icon={d.saved ? <Check className="size-3.5" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}>
                  {d.saved ? "Guardada" : "Guardar"}
                </Button>
                <Button size="sm" variant="ghost" loading={busy} icon={<Users className="size-3.5" aria-hidden />} onClick={() => onEmployees(d.name)}>
                  Ver empleados actuales
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {pages > 1 && (
        <div className="flex flex-wrap items-center justify-center gap-2 border-t border-ork-line px-4 py-3 text-[13px]">
          <PagerButton label="Anterior" disabled={current <= 1} onClick={() => setPage(current - 1)}>
            ‹
          </PagerButton>
          {Array.from({ length: pages }, (_, k) => k + 1)
            .filter((n) => Math.abs(n - current) <= 2 || n === 1 || n === pages)
            .map((n) => (
              <PagerButton key={n} label={`Página ${n}`} active={n === current} onClick={() => setPage(n)}>
                {n}
              </PagerButton>
            ))}
          <PagerButton label="Siguiente" disabled={current >= pages} onClick={() => setPage(current + 1)}>
            ›
          </PagerButton>
        </div>
      )}
    </div>
  );
}

/** Avisos de configuración: solo los ve el equipo de administración de ORKESTA. */
function OperatorAlerts({ totals }: { totals: Record<string, number> }) {
  if (totals.serp_fatal_clave) return <div className="mt-3"><Alert>Solo administración: la clave de Serper no es válida. Revisa SERPER_API_KEY en Render.</Alert></div>;
  if (totals.serp_fatal_creditos) return <div className="mt-3"><Alert>Solo administración: se acabaron los créditos de Serper. Recarga en serper.dev.</Alert></div>;
  return null;
}

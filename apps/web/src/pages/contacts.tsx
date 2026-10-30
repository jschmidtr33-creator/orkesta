import * as Dialog from "@radix-ui/react-dialog";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, ListPlus, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  cx,
  Modal,
  PageHeader,
  SelectField,
  Spinner,
  TextField,
} from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { formatWhen } from "./dashboard";

export type Option = { value: string; label: string };
export type Meta = {
  statuses: Option[];
  industries: Option[];
  countries: Option[];
  mappable: Option[];
  max_mb: number;
};
type ContactRow = {
  id: string;
  full_name: string;
  first_name: string;
  last_name: string;
  job_title: string | null;
  company: string | null;
  email: string | null;
  phone: string | null;
  city: string | null;
  country: string | null;
  country_code: string | null;
  industry: string | null;
  industry_label: string | null;
  status: string;
  status_label: string;
};
type ContactPage = {
  items: ContactRow[];
  total: number;
  page: number;
  pages: number;
  counts: Record<string, number>;
};
type Source = { value: string; source_type: string; source_label: string; obtained_at: string };
type ContactDetail = ContactRow & {
  fields: { field: string; label: string; value: string | null; pending: boolean; sources: Source[] }[];
  lists: { id: string; name: string }[];
};
export type ListItem = { id: string; name: string; description: string; contacts: number };

const STATUS_TONE: Record<string, "neutral" | "blue" | "violet" | "ok" | "warn"> = {
  nuevo: "neutral",
  en_proceso: "warn",
  enriquecido: "violet",
  validado: "ok",
};
const TABS = [
  { value: "", label: "Todos", count: "todos" },
  { value: "nuevo", label: "Nuevos", count: "nuevo" },
  { value: "enriquecido", label: "Enriquecidos", count: "enriquecido" },
  { value: "validado", label: "Validados", count: "validado" },
  { value: "en_proceso", label: "En proceso", count: "en_proceso" },
];

export function useMeta() {
  return useQuery({ queryKey: ["contacts-meta"], queryFn: () => api<Meta>("/api/contacts/meta"), staleTime: Infinity });
}

export function useLists() {
  return useQuery({ queryKey: ["lists"], queryFn: () => api<ListItem[]>("/api/lists") });
}

function useDebounced<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function ContactsPage() {
  const { session } = useSession();
  const canManage = session?.workspace?.role !== "vendedor";
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const query = useDebounced(q);
  const filters = {
    q: query,
    estado: params.get("estado") ?? "",
    pais: params.get("pais") ?? "",
    industria: params.get("industria") ?? "",
    lista: params.get("lista") ?? "",
  };
  const page = Number(params.get("pagina") ?? "1") || 1;
  const search = new URLSearchParams(
    Object.entries({ ...filters, pagina: String(page) }).filter(([, v]) => v) as [string, string][],
  ).toString();
  const meta = useMeta();
  const lists = useLists();
  const contacts = useQuery({
    queryKey: ["contacts", search],
    queryFn: () => api<ContactPage>(`/api/contacts?${search}`),
    placeholderData: keepPreviousData,
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const [editing, setEditing] = useState<ContactDetail | "new" | null>(null);
  const [listOpen, setListOpen] = useState(false);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("pagina");
    setParams(next, { replace: true });
    setSelected(new Set());
  };
  useEffect(() => {
    if ((params.get("q") ?? "") !== query) setFilter("q", query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const remove = useMutation({
    mutationFn: (ids: string[]) => api<{ deleted: number }>("/api/contacts/delete", { method: "POST", json: { ids } }),
    onSuccess: () => {
      setSelected(new Set());
      client.invalidateQueries({ queryKey: ["contacts"] });
      client.invalidateQueries({ queryKey: ["lists"] });
    },
  });

  const data = contacts.data;
  const items = data?.items ?? [];
  const allChecked = items.length > 0 && items.every((c) => selected.has(c.id));
  const activeList = lists.data?.find((l) => l.id === filters.lista);
  const exportHref = `/api/contacts/export?${new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v) as [string, string][],
  ).toString()}`;

  return (
    <>
      <PageHeader
        title="Contactos"
        description="Tu base comercial: cada dato con su fuente. Lo que falta queda pendiente de enriquecimiento, nunca se inventa."
        action={
          <div className="flex flex-wrap gap-2">
            <Link to="/contactos/listas" className="ork-focus inline-flex h-10 items-center rounded-xl px-3 text-sm font-semibold text-ork-graphite hover:bg-ork-ink/5">
              Listas
            </Link>
            <Link to="/contactos/importar" className="ork-focus inline-flex h-10 items-center gap-2 rounded-xl border border-ork-line bg-white px-4 text-sm font-semibold text-ork-ink hover:border-ork-blue/40">
              <FileUp className="size-4" aria-hidden /> Importar
            </Link>
            <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setEditing("new")}>
              Agregar contacto
            </Button>
          </div>
        }
      />
      <Card className="p-0">
        <div className="flex flex-wrap items-center gap-x-1 gap-y-2 border-b border-ork-line px-4 pt-3">
          {TABS.map((tab) => {
            const active = filters.estado === tab.value;
            return (
              <button
                key={tab.label}
                type="button"
                onClick={() => setFilter("estado", tab.value)}
                className={cx(
                  "ork-focus -mb-px flex items-center gap-2 border-b-2 px-3 py-2.5 text-[13.5px] font-semibold",
                  active ? "border-ork-blue text-ork-blue" : "border-transparent text-ork-mist hover:text-ork-ink",
                )}
              >
                {tab.label}
                <span className={cx("rounded-full px-1.5 text-[11.5px]", active ? "bg-ork-blue/10" : "bg-ork-ink/5")}>
                  {(data?.counts[tab.count] ?? 0).toLocaleString("es")}
                </span>
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <label className="relative min-w-[220px] flex-1">
            <span className="sr-only">Buscar</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ork-mist" aria-hidden />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar por nombre, empresa, cargo o correo…"
              className="ork-focus h-10 w-full rounded-xl border border-ork-line bg-white pl-9 pr-3 text-[14px] placeholder:text-ork-mist"
            />
          </label>
          <FilterSelect label="País" value={filters.pais} onChange={(v) => setFilter("pais", v)} options={meta.data?.countries ?? []} />
          <FilterSelect label="Industria" value={filters.industria} onChange={(v) => setFilter("industria", v)} options={meta.data?.industries ?? []} />
          <FilterSelect
            label="Lista"
            value={filters.lista}
            onChange={(v) => setFilter("lista", v)}
            options={(lists.data ?? []).map((l) => ({ value: l.id, label: l.name }))}
          />
          <a href={exportHref} className="ork-focus inline-flex h-10 items-center gap-2 rounded-xl px-3 text-[13.5px] font-semibold text-ork-graphite hover:bg-ork-ink/5">
            <Download className="size-4" aria-hidden /> Exportar
          </a>
        </div>
        {activeList && (
          <div className="mx-4 mb-3 flex items-center justify-between rounded-xl bg-ork-violet/5 px-3 py-2 text-[13px] text-ork-violet">
            <span>
              Viendo la lista <strong>{activeList.name}</strong>
            </span>
            <button type="button" className="font-semibold hover:underline" onClick={() => setFilter("lista", "")}>
              Quitar filtro
            </button>
          </div>
        )}
        {selected.size > 0 && (
          <div className="mx-4 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-ork-blue/20 bg-ork-blue/5 px-3 py-2">
            <span className="text-[13px] font-semibold text-ork-blue">{selected.size} seleccionados</span>
            <Button size="sm" variant="secondary" icon={<ListPlus className="size-3.5" aria-hidden />} onClick={() => setListOpen(true)}>
              Agregar a lista
            </Button>
            {canManage && (
              <Button
                size="sm"
                variant="danger"
                loading={remove.isPending}
                icon={<Trash2 className="size-3.5" aria-hidden />}
                onClick={() => {
                  if (window.confirm(`¿Eliminar ${selected.size} contactos? Esta acción no se puede deshacer.`)) remove.mutate([...selected]);
                }}
              >
                Eliminar
              </Button>
            )}
            <button type="button" className="ml-auto text-[13px] text-ork-mist hover:text-ork-ink" onClick={() => setSelected(new Set())}>
              Limpiar selección
            </button>
          </div>
        )}
        {contacts.isLoading ? (
          <Spinner />
        ) : items.length === 0 ? (
          <EmptyContacts filtered={Object.values(filters).some(Boolean)} onAdd={() => setEditing("new")} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-[13.5px]">
              <thead>
                <tr className="border-y border-ork-line bg-ork-surface/60 text-[12px] uppercase tracking-wide text-ork-mist">
                  <th className="w-10 py-2.5 pl-4">
                    <input
                      type="checkbox"
                      aria-label="Seleccionar todos en esta página"
                      checked={allChecked}
                      onChange={() => {
                        const next = new Set(selected);
                        items.forEach((c) => (allChecked ? next.delete(c.id) : next.add(c.id)));
                        setSelected(next);
                      }}
                      className="size-4 accent-ork-blue"
                    />
                  </th>
                  <th className="py-2.5 pr-3 font-semibold">Nombre</th>
                  <th className="py-2.5 pr-3 font-semibold">Cargo</th>
                  <th className="py-2.5 pr-3 font-semibold">Empresa</th>
                  <th className="py-2.5 pr-3 font-semibold">Ubicación</th>
                  <th className="py-2.5 pr-4 font-semibold">Estado</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} className="cursor-pointer border-b border-ork-line last:border-0 hover:bg-ork-surface/70" onClick={() => setOpenId(c.id)}>
                    <td className="py-3 pl-4" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Seleccionar a ${c.full_name}`}
                        checked={selected.has(c.id)}
                        onChange={() => {
                          const next = new Set(selected);
                          if (next.has(c.id)) next.delete(c.id);
                          else next.add(c.id);
                          setSelected(next);
                        }}
                        className="size-4 accent-ork-blue"
                      />
                    </td>
                    <td className="py-3 pr-3">
                      <div className="flex items-center gap-3">
                        <Avatar name={c.full_name} email={c.email ?? ""} size={32} />
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-ork-ink">{c.full_name}</p>
                          <p className="truncate text-[12.5px] text-ork-mist">{c.email ?? "Correo pendiente"}</p>
                        </div>
                      </div>
                    </td>
                    <td className="py-3 pr-3 text-ork-graphite">{c.job_title ?? <Pending />}</td>
                    <td className="py-3 pr-3 text-ork-graphite">{c.company ?? <Pending />}</td>
                    <td className="py-3 pr-3 text-ork-graphite">{[c.city, c.country].filter(Boolean).join(", ") || <Pending />}</td>
                    <td className="py-3 pr-4">
                      <Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between border-t border-ork-line px-4 py-3 text-[13px] text-ork-mist">
            <span>
              {data.total.toLocaleString("es")} contactos · página {data.page} de {data.pages}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setFilter("pagina", String(page - 1))}>
                Anterior
              </Button>
              <Button size="sm" variant="secondary" disabled={page >= data.pages} onClick={() => setFilter("pagina", String(page + 1))}>
                Siguiente
              </Button>
            </div>
          </div>
        )}
      </Card>

      <ContactDrawer id={openId} onClose={() => setOpenId(null)} onEdit={(c) => setEditing(c)} />
      {editing && <ContactForm contact={editing === "new" ? null : editing} meta={meta.data} onClose={() => setEditing(null)} onSaved={(id) => setOpenId(id)} />}
      <AddToListModal open={listOpen} onOpenChange={setListOpen} ids={[...selected]} onDone={() => setSelected(new Set())} />
    </>
  );
}

function Pending() {
  return <span className="text-[12.5px] italic text-ork-mist">Pendiente</span>;
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: Option[] }) {
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cx(
          "ork-focus h-10 max-w-[200px] rounded-xl border bg-white px-3 text-[13.5px]",
          value ? "border-ork-blue/40 text-ork-blue" : "border-ork-line text-ork-graphite",
        )}
      >
        <option value="">{label}: todos</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyContacts({ filtered, onAdd }: { filtered: boolean; onAdd: () => void }) {
  if (filtered) return <p className="px-4 pb-10 pt-6 text-center text-[14px] text-ork-mist">Ningún contacto coincide con estos filtros.</p>;
  return (
    <div className="flex flex-col items-center gap-3 px-6 pb-14 pt-8 text-center">
      <span className="ork-gradient flex size-12 items-center justify-center rounded-2xl text-white">
        <FileUp className="size-6" aria-hidden />
      </span>
      <h2 className="text-[18px] font-bold text-ork-ink">Tu base está vacía</h2>
      <p className="max-w-md text-[14px] text-ork-mist">
        Importa un Excel o CSV: ORKESTA reconoce las columnas y guarda solo nombre, apellido, ubicación, teléfono, correo, cargo, empresa e industria.
      </p>
      <div className="flex gap-2">
        <Link to="/contactos/importar" className="ork-gradient inline-flex h-10 items-center rounded-xl px-4 text-sm font-semibold text-white">
          Importar base
        </Link>
        <Button variant="secondary" onClick={onAdd}>
          Agregar uno a mano
        </Button>
      </div>
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  importacion: "Importación",
  manual: "Manual",
  enriquecimiento: "Enriquecimiento",
  investigacion: "Investigación IA",
};

function SourceText({ label }: { label: string }) {
  const match = label.match(/https?:\/\/\S+/);
  if (!match) return <>{label}</>;
  const [before] = label.split(match[0]);
  return (
    <>
      {before}
      <a href={match[0]} target="_blank" rel="noreferrer noopener" className="text-ork-blue hover:underline">
        {match[0].replace(/^https?:\/\/(www\.)?/, "").slice(0, 60)}
      </a>
    </>
  );
}

function ContactDrawer({ id, onClose, onEdit }: { id: string | null; onClose: () => void; onEdit: (c: ContactDetail) => void }) {
  const detail = useQuery({
    queryKey: ["contact", id],
    queryFn: () => api<ContactDetail>(`/api/contacts/${id}`),
    enabled: Boolean(id),
  });
  const c = detail.data;
  return (
    <Dialog.Root open={Boolean(id)} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ork-ink/40" />
        <Dialog.Content className="fixed inset-y-0 right-0 z-50 flex w-[min(100vw,480px)] flex-col bg-white shadow-[var(--shadow-pop)]">
          <div className="flex items-start justify-between gap-3 border-b border-ork-line p-5">
            {c ? (
              <div className="flex items-center gap-3">
                <Avatar name={c.full_name} email={c.email ?? ""} size={44} />
                <div>
                  <Dialog.Title className="text-[18px] font-bold text-ork-ink">{c.full_name}</Dialog.Title>
                  <Dialog.Description className="text-[13px] text-ork-mist">
                    {[c.job_title, c.company].filter(Boolean).join(" · ") || "Sin cargo ni empresa"}
                  </Dialog.Description>
                </div>
              </div>
            ) : (
              <Dialog.Title className="text-[18px] font-bold">Contacto</Dialog.Title>
            )}
            <Dialog.Close className="ork-focus rounded-lg p-1 text-ork-mist hover:bg-ork-ink/5" aria-label="Cerrar">
              <X className="size-5" />
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-5">
            {detail.isLoading && <Spinner />}
            {c && (
              <>
                <div className="mb-5 flex items-center justify-between">
                  <Badge tone={STATUS_TONE[c.status]}>{c.status_label}</Badge>
                  <Button size="sm" variant="secondary" icon={<Pencil className="size-3.5" aria-hidden />} onClick={() => onEdit(c)}>
                    Editar
                  </Button>
                </div>
                <dl className="divide-y divide-ork-line">
                  {c.fields.map((f) => (
                    <div key={f.field} className="grid grid-cols-[110px_1fr] gap-3 py-3">
                      <dt className="text-[12.5px] font-semibold text-ork-mist">{f.label}</dt>
                      <dd>
                        {f.pending ? (
                          <Badge tone="warn">Pendiente de enriquecimiento</Badge>
                        ) : (
                          <>
                            <p className="break-words text-[14px] text-ork-ink">{f.value}</p>
                            {f.sources[0] && (
                              <p className="mt-0.5 text-[11.5px] text-ork-mist">
                                Fuente: {SOURCE_LABEL[f.sources[0].source_type]} · <SourceText label={f.sources[0].source_label} /> · {formatWhen(f.sources[0].obtained_at)}
                                {f.sources.length > 1 && ` · ${f.sources.length - 1} fuente${f.sources.length > 2 ? "s" : ""} más`}
                              </p>
                            )}
                          </>
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-5">
                  <p className="mb-2 text-[12.5px] font-semibold text-ork-mist">Listas</p>
                  {c.lists.length ? (
                    <div className="flex flex-wrap gap-1.5">
                      {c.lists.map((l) => (
                        <Badge key={l.id} tone="violet">
                          {l.name}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[13px] text-ork-mist">No está en ninguna lista.</p>
                  )}
                </div>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ContactForm({
  contact,
  meta,
  onClose,
  onSaved,
}: {
  contact: ContactDetail | null;
  meta?: Meta;
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const client = useQueryClient();
  const [form, setForm] = useState({
    first_name: contact?.first_name ?? "",
    last_name: contact?.last_name ?? "",
    job_title: contact?.job_title ?? "",
    company: contact?.company ?? "",
    email: contact?.email ?? "",
    phone: contact?.phone ?? "",
    city: contact?.city ?? "",
    country: contact?.country ?? "",
    industry: contact?.industry ?? "",
  });
  const save = useMutation({
    mutationFn: () =>
      contact
        ? api<ContactDetail>(`/api/contacts/${contact.id}`, { method: "PATCH", json: form })
        : api<ContactDetail>("/api/contacts", { method: "POST", json: form }),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ["contacts"] });
      client.setQueryData(["contact", data.id], data);
      onClose();
      onSaved(data.id);
    },
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={contact ? "Editar contacto" : "Agregar contacto"} description="Los campos vacíos quedan pendientes de enriquecimiento.">
      <form onSubmit={submit} className="grid grid-cols-2 gap-3">
        {save.error && (
          <div className="col-span-2">
            <Alert>{save.error.message}</Alert>
          </div>
        )}
        <TextField label="Nombre" required value={form.first_name} onChange={set("first_name")} />
        <TextField label="Apellido" value={form.last_name} onChange={set("last_name")} />
        <TextField label="Cargo" value={form.job_title} onChange={set("job_title")} className="col-span-2" />
        <TextField label="Empresa" value={form.company} onChange={set("company")} className="col-span-2" />
        <TextField label="Correo" type="email" value={form.email} onChange={set("email")} />
        <TextField label="Teléfono" value={form.phone} onChange={set("phone")} />
        <TextField label="Ciudad" value={form.city} onChange={set("city")} />
        <SelectField label="País" value={form.country} onChange={set("country")}>
          <option value="">Sin país</option>
          {meta?.countries.map((c) => (
            <option key={c.value} value={c.label}>
              {c.label}
            </option>
          ))}
        </SelectField>
        <TextField label="Industria" value={form.industry} onChange={set("industry")} className="col-span-2" />
        <div className="col-span-2 mt-2 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" loading={save.isPending}>
            Guardar
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function AddToListModal({ open, onOpenChange, ids, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; ids: string[]; onDone: () => void }) {
  const client = useQueryClient();
  const lists = useLists();
  const [listId, setListId] = useState("");
  const [name, setName] = useState("");
  const add = useMutation({
    mutationFn: async () => {
      let target = listId;
      if (!target) target = (await api<{ id: string }>("/api/lists", { method: "POST", json: { name } })).id;
      return api<{ added: number }>(`/api/lists/${target}/members`, { method: "POST", json: { ids } });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lists"] });
      onDone();
      onOpenChange(false);
      setName("");
      setListId("");
    },
  });
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Agregar a una lista" description={`${ids.length} contactos seleccionados.`}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        {add.error && <Alert>{add.error.message}</Alert>}
        <SelectField label="Lista existente" value={listId} onChange={(e) => setListId(e.target.value)}>
          <option value="">— Crear una lista nueva —</option>
          {lists.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name} ({l.contacts})
            </option>
          ))}
        </SelectField>
        {!listId && <TextField label="Nombre de la lista nueva" required value={name} onChange={(e) => setName(e.target.value)} />}
        <Button type="submit" loading={add.isPending}>
          Agregar
        </Button>
      </form>
    </Modal>
  );
}

export function ListsPage() {
  const { session } = useSession();
  const canManage = session?.workspace?.role !== "vendedor";
  const client = useQueryClient();
  const lists = useLists();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api("/api/lists", { method: "POST", json: { name } }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["lists"] });
      setOpen(false);
      setName("");
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/lists/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["lists"] }),
  });
  const sorted = useMemo(() => lists.data ?? [], [lists.data]);
  return (
    <>
      <PageHeader
        title="Listas"
        description="Agrupa contactos para tus campañas. Borrar una lista no borra a las personas."
        action={
          <div className="flex gap-2">
            <Link to="/contactos" className="ork-focus inline-flex h-10 items-center rounded-xl px-3 text-sm font-semibold text-ork-graphite hover:bg-ork-ink/5">
              Volver a contactos
            </Link>
            <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setOpen(true)}>
              Nueva lista
            </Button>
          </div>
        }
      />
      {lists.isLoading ? (
        <Spinner />
      ) : sorted.length === 0 ? (
        <Card className="py-10 text-center text-[14px] text-ork-mist">Aún no tienes listas. Créalas desde aquí, al importar o seleccionando contactos.</Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {sorted.map((l) => (
            <Card key={l.id} className="flex flex-col gap-3">
              <div>
                <p className="text-[15px] font-bold text-ork-ink">{l.name}</p>
                {l.description && <p className="text-[13px] text-ork-mist">{l.description}</p>}
              </div>
              <p className="text-[26px] font-bold text-ork-ink">
                {l.contacts.toLocaleString("es")} <span className="text-[13px] font-medium text-ork-mist">contactos</span>
              </p>
              <div className="mt-auto flex items-center justify-between">
                <Link to={`/contactos?lista=${l.id}`} className="text-[13.5px] font-semibold text-ork-blue hover:underline">
                  Ver contactos
                </Link>
                {canManage && (
                  <button
                    type="button"
                    className="ork-focus rounded-lg p-1.5 text-ork-mist hover:bg-bad/5 hover:text-bad"
                    aria-label={`Eliminar la lista ${l.name}`}
                    onClick={() => window.confirm(`¿Eliminar la lista «${l.name}»? Los contactos se conservan.`) && remove.mutate(l.id)}
                  >
                    <Trash2 className="size-4" />
                  </button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
      <Modal open={open} onOpenChange={setOpen} title="Nueva lista">
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          {create.error && <Alert>{create.error.message}</Alert>}
          <TextField label="Nombre" required autoFocus value={name} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" loading={create.isPending}>
            Crear lista
          </Button>
        </form>
      </Modal>
    </>
  );
}

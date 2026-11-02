import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Brush, Check, Copy, Eye, FilePlus2, Files, LayoutTemplate, Mail, Package, Palette, Plus, RefreshCw, Send, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Alert, Badge, Button, Card, CardHeader, cx, Modal, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
import { ProposalDocument, RichText } from "../components/ProposalDocument";
import { api } from "../lib/api";
import { RichEditor } from "../components/RichEditor";
import type { Editor } from "@tiptap/react";
import { useMailboxes } from "./campaigns";
import { BrandSettings, DesignPanel, ImageField, lookFrom, useBrand, type Design } from "./proposalDesign";
import { useSession } from "../lib/session";
import { money, type Deal } from "./crm";
import { formatWhen } from "./dashboard";

type Item = { name: string; description: string; quantity: number; unit: string; unit_price: number; discount: number; image_url?: string; payment_url?: string };
type Section = { key: string; title: string; body: string };
type Totals = { subtotal: number; discounts: number; tax: number; total: number };
type ProposalRow = {
  id: string;
  number: number;
  title: string;
  status: "draft" | "sent" | "viewed" | "accepted" | "rejected";
  status_label: string;
  currency: string;
  totals: Totals;
  valid_until: string | null;
  public_id: string;
  contact: { id: string; full_name: string; email: string | null } | null;
  company: string | null;
  deal: { id: string; title: string } | null;
  view_count: number;
  last_viewed_at: string | null;
  sent_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  created_at: string;
};
type Proposal = ProposalRow & { items: Item[]; sections: Section[]; tax_rate: number; design: Design; variables: Record<string, string>; values: Record<string, string> };

/** Reemplaza {{variable|alternativo}} con los datos del cliente (igual que el servidor). */
function fill(text: string, values: Record<string, string>, escape = false): string {
  return text.replace(/\{\{\s*([a-z_]+)\s*(?:\|\s*([^}]*))?\}\}/g, (_, name: string, fallback?: string) => {
    const v = values[name] ?? "";
    if (v) return escape ? v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : v;
    return fallback !== undefined ? fallback.trim() : "";
  });
}
type Product = { id: string; name: string; description: string; unit_price: number; currency: string; unit: string; active: boolean; image_url: string; payment_url: string };

const STATUS_TONE = { draft: "neutral", sent: "blue", viewed: "violet", accepted: "ok", rejected: "bad" } as const;
const CURRENCIES = ["USD", "COP", "MXN", "PEN", "CLP", "CRC", "EUR"];

export function publicLink(publicId: string): string {
  return `${window.location.origin}/app/p/${publicId}`;
}

function useProducts() {
  return useQuery({ queryKey: ["products"], queryFn: () => api<Product[]>("/api/products") });
}

// --- Listado y catálogo -------------------------------------------------------------------

export function ProposalsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("vista") === "catalogo" ? "catalogo" : params.get("vista") === "marca" ? "marca" : "propuestas";
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const list = useQuery({ queryKey: ["proposals", status], queryFn: () => api<ProposalRow[]>(`/api/proposals?estado=${status}`) });
  return (
    <>
      <PageHeader
        title="Propuestas comerciales"
        description="Crea propuestas desde tus oportunidades, compártelas con un enlace y sabe cuándo el cliente las abre, acepta o rechaza."
        action={
          <Button icon={<FilePlus2 className="size-4" aria-hidden />} onClick={() => setCreating(true)}>
            Nueva propuesta
          </Button>
        }
      />
      <div className="mb-4 inline-flex rounded-xl border border-ork-line bg-ork-card p-0.5" role="tablist">
        {(
          [
            ["propuestas", "Propuestas", <Files key="f" className="size-4" aria-hidden />],
            ["catalogo", "Catálogo", <Package key="p" className="size-4" aria-hidden />],
            ["marca", "Marca y diseño", <Palette key="m" className="size-4" aria-hidden />],
          ] as const
        ).map(([value, label, icon]) => (
          <button
            key={value}
            role="tab"
            aria-selected={tab === value}
            onClick={() => setParams({ vista: value })}
            className={cx("ork-focus inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold", tab === value ? "ork-gradient text-white" : "text-ork-graphite")}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>
      {tab === "catalogo" ? (
        <Catalog />
      ) : tab === "marca" ? (
        <BrandSettings />
      ) : (
        <Card className="overflow-hidden p-0">
          <div className="flex flex-wrap gap-1 border-b border-ork-line px-4 pt-3">
            {[
              ["", "Todas"],
              ["draft", "Borradores"],
              ["sent", "Enviadas"],
              ["viewed", "Vistas"],
              ["accepted", "Aceptadas"],
              ["rejected", "Rechazadas"],
            ].map(([value, label]) => (
              <button
                key={value}
                onClick={() => setStatus(value)}
                className={cx("ork-focus -mb-px border-b-2 px-3 pb-2.5 text-[13.5px] font-semibold", status === value ? "border-ork-blue text-ork-blue" : "border-transparent text-ork-mist hover:text-ork-ink")}
              >
                {label}
              </button>
            ))}
          </div>
          {list.isLoading ? (
            <Spinner />
          ) : !list.data?.length ? (
            <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">Aún no hay propuestas. Crea la primera desde una oportunidad del CRM.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-[13.5px]">
                <thead className="border-b border-ork-line bg-ork-ink/[0.02] text-[12.5px] text-ork-graphite">
                  <tr>
                    <th className="px-4 py-3 font-semibold">N.º</th>
                    <th className="px-3 py-3 font-semibold">Propuesta</th>
                    <th className="px-3 py-3 font-semibold">Cliente</th>
                    <th className="px-3 py-3 text-right font-semibold">Total</th>
                    <th className="px-3 py-3 font-semibold">Estado</th>
                    <th className="px-4 py-3 font-semibold">Actividad</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ork-line">
                  {list.data.map((p) => (
                    <tr key={p.id} className="hover:bg-ork-ink/[0.02]">
                      <td className="px-4 py-3 font-semibold text-ork-mist">#{p.number}</td>
                      <td className="px-3 py-3">
                        <Link to={`/propuestas/${p.id}`} className="font-semibold text-ork-ink hover:text-ork-blue">
                          {p.title}
                        </Link>
                        {p.deal && <p className="text-[12px] text-ork-mist">{p.deal.title}</p>}
                      </td>
                      <td className="px-3 py-3 text-ork-graphite">{[p.contact?.full_name, p.company].filter(Boolean).join(" · ") || "—"}</td>
                      <td className="px-3 py-3 text-right font-semibold text-ork-graphite">{money(p.totals.total, p.currency)}</td>
                      <td className="px-3 py-3">
                        <Badge tone={STATUS_TONE[p.status]}>{p.status_label}</Badge>
                      </td>
                      <td className="px-4 py-3 text-[12.5px] text-ork-mist">
                        {p.decided_at
                          ? `${p.status === "accepted" ? "Aceptada" : "Rechazada"} ${formatWhen(p.decided_at)}`
                          : p.view_count
                            ? `${p.view_count} apertura(s) · última ${formatWhen(p.last_viewed_at!)}`
                            : p.sent_at
                              ? `Enviada ${formatWhen(p.sent_at)}`
                              : `Creada ${formatWhen(p.created_at)}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      <NewProposalModal open={creating} onOpenChange={setCreating} />
    </>
  );
}

export function NewProposalModal({ open, onOpenChange, dealId }: { open: boolean; onOpenChange: (o: boolean) => void; dealId?: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const products = useProducts();
  const deals = useQuery({ queryKey: ["crm-deals", "open-for-proposals"], queryFn: () => api<{ deals: Deal[] }>("/api/crm/deals?estado=open"), enabled: open && !dealId });
  const [deal, setDeal] = useState(dealId ?? "");
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => {
    if (open) {
      setDeal(dealId ?? "");
      setPicked([]);
    }
  }, [open, dealId]);
  const create = useMutation({
    mutationFn: () => api<Proposal>("/api/proposals", { method: "POST", json: { deal_id: deal || null, product_ids: picked } }),
    onSuccess: (p) => {
      client.invalidateQueries({ queryKey: ["proposals"] });
      onOpenChange(false);
      navigate(`/propuestas/${p.id}`);
    },
  });
  const active = products.data?.filter((p) => p.active) ?? [];
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Nueva propuesta" description="ORKESTA arma el borrador con los datos de la oportunidad, el contacto y lo conversado. Luego lo ajustas.">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        {create.error && <Alert>{create.error.message}</Alert>}
        {!dealId && (
          <SelectField label="Oportunidad" value={deal} onChange={(e) => setDeal(e.target.value)}>
            <option value="">Sin oportunidad (propuesta en blanco)</option>
            {deals.data?.deals.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
                {d.contact ? ` · ${d.contact.full_name}` : ""}
              </option>
            ))}
          </SelectField>
        )}
        <div>
          <p className="mb-1.5 text-[13px] font-semibold text-ork-ink">Productos o servicios del catálogo (opcional)</p>
          {active.length === 0 ? (
            <p className="text-[12.5px] text-ork-mist">
              Tu catálogo está vacío: se usará el valor de la oportunidad. Puedes cargar productos en la pestaña Catálogo.
            </p>
          ) : (
            <div className="flex max-h-44 flex-col gap-1 overflow-y-auto">
              {active.map((p) => (
                <label key={p.id} className="flex items-center justify-between gap-2 rounded-lg border border-ork-line px-3 py-2 text-[13px]">
                  <span className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-ork-blue"
                      checked={picked.includes(p.id)}
                      onChange={() => setPicked(picked.includes(p.id) ? picked.filter((x) => x !== p.id) : [...picked, p.id])}
                    />
                    {p.name}
                  </span>
                  <span className="text-ork-mist">{money(p.unit_price, p.currency)}</span>
                </label>
              ))}
            </div>
          )}
        </div>
        <Button type="submit" loading={create.isPending}>
          Crear borrador
        </Button>
      </form>
    </Modal>
  );
}

function Catalog() {
  const { session } = useSession();
  const isManager = session?.workspace?.role !== "vendedor";
  const client = useQueryClient();
  const products = useProducts();
  const empty = { name: "", description: "", unit_price: "", currency: "USD", unit: "unidad", image_url: "", payment_url: "" };
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api(editing ? `/api/products/${editing}` : "/api/products", {
        method: editing ? "PATCH" : "POST",
        json: { ...form, unit_price: Number(form.unit_price || 0) },
      }),
    onSuccess: () => {
      setForm(empty);
      setEditing(null);
      client.invalidateQueries({ queryKey: ["products"] });
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/products/${id}`, { method: "DELETE" }), onSuccess: () => client.invalidateQueries({ queryKey: ["products"] }) });
  return (
    <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(360px,480px)]">
      <Card className="overflow-hidden p-0">
        {products.isLoading ? (
          <Spinner />
        ) : !products.data?.length ? (
          <p className="px-5 py-12 text-center text-[13.5px] text-ork-mist">Carga tus productos y servicios para armar propuestas en segundos.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {products.data.map((p) => (
              <li key={p.id} className="flex items-start justify-between gap-3 px-5 py-4">
                <div className="flex min-w-0 flex-1 gap-3">
                  {p.image_url && <img src={p.image_url} alt="" className="size-16 shrink-0 rounded-lg object-cover" />}
                  <div className="min-w-0">
                    <p className="font-semibold text-ork-ink">{p.name}</p>
                    <RichText value={p.description} className="mt-1 text-[13px] leading-relaxed text-ork-graphite" />
                    {p.payment_url && (
                      <a href={p.payment_url} target="_blank" rel="noreferrer" className="mt-1.5 inline-block text-[12.5px] font-semibold text-ork-blue hover:underline">
                        Link de pago ↗
                      </a>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="text-[13.5px] font-semibold text-ork-graphite">
                    {money(p.unit_price, p.currency)} / {p.unit}
                  </span>
                  {isManager && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(p.id);
                          setForm({ name: p.name, description: p.description, unit_price: String(p.unit_price), currency: p.currency, unit: p.unit, image_url: p.image_url, payment_url: p.payment_url });
                        }}
                      >
                        Editar
                      </Button>
                      <button type="button" aria-label={`Eliminar ${p.name}`} onClick={() => remove.mutate(p.id)} className="ork-focus rounded p-1 text-ork-mist hover:text-bad">
                        <Trash2 className="size-4" />
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {isManager && (
        <Card>
          <CardHeader title={editing ? "Editar producto" : "Agregar producto o servicio"} />
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            {save.error && <Alert>{save.error.message}</Alert>}
            <TextField label="Nombre" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <div className="flex flex-col gap-1.5">
              <span className="text-[13px] font-semibold text-ork-ink">Descripción</span>
              <RichEditor key={editing ?? "nuevo"} value={form.description} onChange={(html) => setForm((f) => ({ ...f, description: html }))} minHeight={140} />
              <span className="text-[12px] text-ork-mist">Enter: nuevo párrafo · Shift + Enter: salto de línea. Puedes usar negrita, listas, enlaces e imágenes.</span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <TextField label="Precio" inputMode="decimal" value={form.unit_price} onChange={(e) => setForm({ ...form, unit_price: e.target.value.replace(/[^\d.]/g, "") })} />
              <SelectField label="Moneda" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
                {CURRENCIES.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </SelectField>
            </div>
            <TextField label="Unidad" placeholder="usuario, mes, proyecto…" value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} />
            <ImageField label="Imagen del producto (opcional)" value={form.image_url} onChange={(v) => setForm((f) => ({ ...f, image_url: v }))} hint="Aparece junto al producto en la propuesta." />
            <TextField label="Link de pago (opcional)" placeholder="https://… (Stripe, PayPal, Mercado Pago, Wompi…)" value={form.payment_url} onChange={(e) => setForm({ ...form, payment_url: e.target.value })} hint="En la propuesta aparece el botón «Pagar en línea»." />
            <div className="flex justify-end gap-2">
              {editing && (
                <Button type="button" variant="ghost" onClick={() => (setEditing(null), setForm(empty))}>
                  Cancelar
                </Button>
              )}
              <Button type="submit" loading={save.isPending}>
                {editing ? "Guardar" : "Agregar"}
              </Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}

// --- Editor ---------------------------------------------------------------------------------

function computeTotals(items: Item[], tax: number): Totals {
  let subtotal = 0;
  let discounts = 0;
  for (const i of items) {
    const gross = (Number(i.quantity) || 0) * (Number(i.unit_price) || 0);
    const off = (gross * (Number(i.discount) || 0)) / 100;
    subtotal += gross - off;
    discounts += off;
  }
  const t = (subtotal * (Number(tax) || 0)) / 100;
  const r = (v: number) => Math.round(v * 100) / 100;
  return { subtotal: r(subtotal), discounts: r(discounts), tax: r(t), total: r(subtotal + t) };
}

export function ProposalEditor() {
  const { id } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const products = useProducts();
  const query = useQuery({ queryKey: ["proposal", id], queryFn: () => api<Proposal>(`/api/proposals/${id}`) });
  const p = query.data;
  const [draft, setDraft] = useState<Pick<Proposal, "title" | "currency" | "items" | "sections" | "tax_rate" | "valid_until" | "design"> | null>(null);
  const [copied, setCopied] = useState(false);
  const [mode, setMode] = useState<"contenido" | "diseno" | "vista">("contenido");
  const [openItem, setOpenItem] = useState<number | null>(null);
  const [emailing, setEmailing] = useState(false);
  const caret = useRef<{ el: HTMLInputElement | HTMLTextAreaElement; pos: number } | null>(null);
  useLayoutEffect(() => {
    // Tras insertar un dato, el cursor queda justo después de la variable.
    const c = caret.current;
    if (!c) return;
    caret.current = null;
    c.el.focus();
    c.el.setSelectionRange(c.pos, c.pos);
  });
  const focused = useRef<{ el: HTMLInputElement | HTMLTextAreaElement; apply: (v: string) => void } | null>(null);
  const track = (apply: (v: string) => void) => ({
    onFocus: (e: { currentTarget: HTMLInputElement | HTMLTextAreaElement }) => (focused.current = { el: e.currentTarget, apply }),
  });
  const insertVar = (name: string) => {
    const f = focused.current;
    if (!f) return;
    const token = `{{${name}}}`;
    const start = f.el.selectionStart ?? f.el.value.length;
    const end = f.el.selectionEnd ?? f.el.value.length;
    caret.current = { el: f.el, pos: start + token.length };
    f.apply(f.el.value.slice(0, start) + token + f.el.value.slice(end));
  };
  const brand = useBrand();
  const snapshot = (x: Proposal) => ({ title: x.title, currency: x.currency, items: x.items, sections: x.sections, tax_rate: x.tax_rate, valid_until: x.valid_until, design: x.design ?? {} });
  useEffect(() => {
    if (p) setDraft(snapshot(p));
  }, [p]);
  const locked = p?.status === "accepted" || p?.status === "rejected";
  const dirty = useMemo(
    () => Boolean(p && draft && JSON.stringify(draft) !== JSON.stringify(snapshot(p))),
    [p, draft],
  );
  const refresh = (data: Proposal) => {
    client.setQueryData(["proposal", id], data);
    client.invalidateQueries({ queryKey: ["proposals"] });
  };
  const save = useMutation({ mutationFn: () => api<Proposal>(`/api/proposals/${id}`, { method: "PATCH", json: draft }), onSuccess: refresh });
  const send = useMutation({
    mutationFn: async () => {
      if (dirty) await api(`/api/proposals/${id}`, { method: "PATCH", json: draft });
      return api<Proposal>(`/api/proposals/${id}/send`, { method: "POST" });
    },
    onSuccess: (data) => {
      refresh(data);
      client.invalidateQueries({ queryKey: ["crm-deals"] });
    },
  });
  const regen = useMutation({
    mutationFn: async () => {
      if (dirty) await api(`/api/proposals/${id}`, { method: "PATCH", json: draft });
      return api<Proposal>(`/api/proposals/${id}/regenerate`, { method: "POST" });
    },
    onSuccess: refresh,
  });
  const dup = useMutation({
    mutationFn: () => api<Proposal>(`/api/proposals/${id}/duplicate`, { method: "POST" }),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ["proposals"] });
      navigate(`/propuestas/${data.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/proposals/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["proposals"] });
      navigate("/propuestas");
    },
  });

  if (query.isLoading || !draft) return query.error ? <Alert>{query.error.message}</Alert> : <Spinner />;
  if (!p) return null;
  const t = computeTotals(draft.items, draft.tax_rate);
  const setItem = (i: number, patch: Partial<Item>) => setDraft({ ...draft, items: draft.items.map((it, j) => (j === i ? { ...it, ...patch } : it)) });
  const setSection = (i: number, patch: Partial<Section>) => setDraft({ ...draft, sections: draft.sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(publicLink(p.public_id));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* sin permiso para copiar */
    }
  };
  const error = save.error ?? send.error ?? regen.error ?? dup.error ?? remove.error;

  return (
    <>
      <Link to="/propuestas" className="mb-3 inline-flex items-center gap-1 text-[13px] font-semibold text-ork-mist hover:text-ork-ink">
        <ArrowLeft className="size-4" aria-hidden /> Propuestas
      </Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12.5px] font-semibold text-ork-mist">Propuesta #{p.number}</p>
          <input
            aria-label="Título de la propuesta"
            disabled={locked}
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            {...track((v) => setDraft((d) => (d ? { ...d, title: v } : d)))}
            className="ork-focus -ml-1 w-full max-w-[640px] rounded-lg bg-transparent px-1 text-[22px] font-bold text-ork-ink hover:bg-ork-ink/[0.03]"
          />
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-ork-mist">
            <Badge tone={STATUS_TONE[p.status]}>{p.status_label}</Badge>
            {p.contact && <span>{p.contact.full_name}</span>}
            {p.company && <span>· {p.company}</span>}
            {p.deal && (
              <Link to={`/crm?oportunidad=${p.deal.id}`} className="font-semibold text-ork-blue hover:underline">
                · {p.deal.title}
              </Link>
            )}
            {p.view_count > 0 && <span>· Vista {p.view_count} vez/veces, última {formatWhen(p.last_viewed_at!)}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <a href={`/app/p/${p.public_id}?vista_previa=1`} target="_blank" rel="noreferrer" className="ork-focus inline-flex h-10 items-center gap-2 rounded-xl border border-ork-line bg-ork-card px-3 text-sm font-semibold text-ork-ink hover:border-ork-blue/40">
            <Eye className="size-4" aria-hidden /> Vista previa / PDF
          </a>
          <Button variant="secondary" icon={copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />} onClick={copyLink}>
            {copied ? "Enlace copiado" : "Copiar enlace"}
          </Button>
          {locked ? (
            <Button variant="secondary" loading={dup.isPending} icon={<Files className="size-4" aria-hidden />} onClick={() => dup.mutate()}>
              Duplicar
            </Button>
          ) : (
            <>
              <Button variant="secondary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
                Guardar
              </Button>
              <Button variant="secondary" loading={send.isPending} onClick={() => send.mutate()} title="Si la enviaste por otro medio">
                {p.status === "draft" ? "Marcar como enviada" : "Guardar"}
              </Button>
              <Button
                icon={<Mail className="size-4" aria-hidden />}
                loading={save.isPending}
                onClick={async () => {
                  if (dirty) await save.mutateAsync();
                  setEmailing(true);
                }}
              >
                Enviar por correo
              </Button>
            </>
          )}
        </div>
      </div>
      {error && <div className="mb-4"><Alert>{error.message}</Alert></div>}
      {p.status === "draft" && (
        <div className="mb-4">
          <Alert tone="blue">
            Personaliza plantilla, colores, idioma y tus datos en «Diseño». Luego copia el enlace y envíalo a tu cliente; al marcarla como enviada, la oportunidad pasa a «Propuesta enviada» y verás cuándo la abre.
          </Alert>
        </div>
      )}
      {p.decided_at && (
        <div className="mb-4">
          <Alert tone={p.status === "accepted" ? "ok" : "bad"}>
            {p.decided_by ?? "El cliente"} {p.status === "accepted" ? "aceptó" : "rechazó"} esta propuesta el {formatWhen(p.decided_at)}.
            {p.decision_note ? ` Comentario: «${p.decision_note}»` : ""}
          </Alert>
        </div>
      )}

      <div className="mb-4 inline-flex rounded-xl border border-ork-line bg-ork-card p-0.5" role="tablist" aria-label="Editor">
        {(
          [
            ["contenido", "Contenido", <LayoutTemplate key="c" className="size-4" aria-hidden />],
            ["diseno", "Diseño", <Brush key="d" className="size-4" aria-hidden />],
            ["vista", "Vista previa", <Eye key="v" className="size-4" aria-hidden />],
          ] as const
        ).map(([value, label, icon]) => (
          <button
            key={value}
            role="tab"
            aria-selected={mode === value}
            onClick={() => setMode(value)}
            className={cx("ork-focus inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold", mode === value ? "ork-gradient text-white" : "text-ork-graphite")}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        {mode === "diseno" ? (
          <DesignPanel
            design={draft.design}
            locked={locked}
            onChange={(design) => setDraft({ ...draft, design })}
            translating={regen.isPending}
            onTranslate={() => window.confirm("¿Rehacer los textos en el idioma elegido? Se reemplaza el contenido actual.") && regen.mutate()}
          />
        ) : mode === "vista" ? (
          brand.data ? (
            <div className="min-w-0">
              <ProposalDocument
                data={{
                  number: p.number,
                  title: fill(draft.title, p.values ?? {}),
                  currency: draft.currency,
                  items: draft.items.map((it) => ({ ...it, name: fill(it.name, p.values ?? {}), description: fill(it.description, p.values ?? {}, /</.test(it.description)) })),
                  sections: draft.sections.map((x) => ({ ...x, title: fill(x.title, p.values ?? {}), body: fill(x.body, p.values ?? {}) })),
                  tax_rate: draft.tax_rate,
                  totals: t,
                  valid_until: draft.valid_until,
                  company: p.company,
                  contact_name: p.contact?.full_name ?? null,
                  issuer: brand.data.brand.legal_name,
                  created_at: p.created_at,
                  look: lookFrom(brand.data.brand, draft.design),
                }}
              />
            </div>
          ) : (
            <Spinner />
          )
        ) : (
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader
              title="Contenido"
              description="Generado con los datos de la oportunidad y lo conversado. Edítalo libremente."
              action={
                !locked && (
                  <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" aria-hidden />} loading={regen.isPending} onClick={() => window.confirm("¿Regenerar las secciones? Se reemplazará el texto actual.") && regen.mutate()}>
                    Regenerar borrador
                  </Button>
                )
              }
            />
            {!locked && (
              <div className="mb-4 rounded-xl bg-ork-surface p-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="mr-1 text-[12.5px] font-semibold text-ork-graphite">Insertar dato del cliente:</span>
                  {Object.entries(p.variables ?? {}).map(([k, label]) => (
                    <button
                      key={k}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => insertVar(k)}
                      title={p.values?.[k] ? `Para este cliente: ${p.values[k]}` : "Sin dato para este cliente"}
                      className={cx(
                        "ork-focus rounded-full border px-2.5 py-0.5 text-[12px] font-semibold",
                        p.values?.[k] ? "border-ork-line bg-ork-card text-ork-graphite hover:border-ork-blue/40 hover:text-ork-blue" : "border-dashed border-ork-line text-ork-mist",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-[12px] text-ork-mist">
                  Haz clic en un campo (título, sección o texto) y luego en el dato. El cliente verá su valor real; usa <code className="rounded bg-ork-card px-1">{"{{cargo|equipo directivo}}"}</code> para un texto alterno si falta.
                </p>
              </div>
            )}
            <div className="flex flex-col gap-4">
              {draft.sections.map((s, i) => (
                <div key={s.key + i} className="rounded-xl border border-ork-line p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <input
                      aria-label="Título de la sección"
                      disabled={locked}
                      value={s.title}
                      onChange={(e) => setSection(i, { title: e.target.value })}
                      {...track((v) => setDraft((d) => (d ? { ...d, sections: d.sections.map((x, j) => (j === i ? { ...x, title: v } : x)) } : d)))}
                      className="ork-focus min-w-0 flex-1 rounded-md px-1 text-[14.5px] font-bold text-ork-ink hover:bg-ork-ink/[0.03]"
                    />
                    {!locked && (
                      <button type="button" aria-label="Quitar sección" onClick={() => setDraft({ ...draft, sections: draft.sections.filter((_, j) => j !== i) })} className="ork-focus rounded p-1 text-ork-mist hover:text-bad">
                        <X className="size-4" />
                      </button>
                    )}
                  </div>
                  <textarea
                    aria-label={`Texto de ${s.title}`}
                    disabled={locked}
                    value={s.body}
                    onChange={(e) => setSection(i, { body: e.target.value })}
                    {...track((v) => setDraft((d) => (d ? { ...d, sections: d.sections.map((x, j) => (j === i ? { ...x, body: v } : x)) } : d)))}
                    rows={Math.min(14, Math.max(3, s.body.split("\n").length + 1))}
                    className="ork-focus w-full resize-y rounded-lg border border-ork-line px-3 py-2 text-[13.5px] leading-relaxed text-ork-graphite"
                  />
                </div>
              ))}
              {!locked && (
                <Button
                  variant="ghost"
                  size="sm"
                  icon={<Plus className="size-3.5" aria-hidden />}
                  onClick={() => setDraft({ ...draft, sections: [...draft.sections, { key: `s${Date.now()}`, title: "Nueva sección", body: "" }] })}
                >
                  Agregar sección
                </Button>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Inversión" />
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-[13px]">
                <thead className="text-[12px] text-ork-mist">
                  <tr>
                    <th className="pb-2 font-semibold">Concepto</th>
                    <th className="w-20 pb-2 font-semibold">Cant.</th>
                    <th className="w-32 pb-2 font-semibold">Precio unit.</th>
                    <th className="w-20 pb-2 font-semibold">Desc. %</th>
                    <th className="w-28 pb-2 text-right font-semibold">Total</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {draft.items.map((it, i) => {
                    const line = (Number(it.quantity) || 0) * (Number(it.unit_price) || 0) * (1 - (Number(it.discount) || 0) / 100);
                    const cell = "ork-focus h-9 w-full rounded-lg border border-ork-line px-2";
                    const extras = [it.description && "descripción", it.image_url && "imagen", it.payment_url && "link de pago"].filter(Boolean).join(" · ");
                    return (
                      <Fragment key={i}>
                      <tr className="align-top">
                        <td className="py-1 pr-2">
                          <input aria-label="Concepto" disabled={locked} value={it.name} onChange={(e) => setItem(i, { name: e.target.value })} className={cell} />
                          <button type="button" onClick={() => setOpenItem(openItem === i ? null : i)} className="mt-1 text-left text-[12px] font-semibold text-ork-blue hover:underline">
                            {openItem === i ? "Ocultar detalle" : extras ? `Detalle: ${extras}` : "＋ Descripción, imagen o link de pago"}
                          </button>
                        </td>
                        <td className="py-1 pr-2">
                          <input aria-label="Cantidad" disabled={locked} inputMode="decimal" value={it.quantity} onChange={(e) => setItem(i, { quantity: Number(e.target.value.replace(/[^\d.]/g, "")) })} className={cell} />
                        </td>
                        <td className="py-1 pr-2">
                          <input aria-label="Precio unitario" disabled={locked} inputMode="decimal" value={it.unit_price} onChange={(e) => setItem(i, { unit_price: Number(e.target.value.replace(/[^\d.]/g, "")) })} className={cell} />
                        </td>
                        <td className="py-1 pr-2">
                          <input aria-label="Descuento" disabled={locked} inputMode="decimal" value={it.discount} onChange={(e) => setItem(i, { discount: Math.min(100, Number(e.target.value.replace(/[^\d.]/g, ""))) })} className={cell} />
                        </td>
                        <td className="py-2.5 text-right font-semibold text-ork-graphite">{money(line, draft.currency)}</td>
                        <td className="py-1 pl-1">
                          {!locked && (
                            <button type="button" aria-label="Quitar línea" onClick={() => setDraft({ ...draft, items: draft.items.filter((_, j) => j !== i) })} className="ork-focus mt-1.5 rounded p-1 text-ork-mist hover:text-bad">
                              <Trash2 className="size-4" />
                            </button>
                          )}
                        </td>
                      </tr>
                      {openItem === i && (
                        <tr>
                          <td colSpan={6} className="pb-4 pt-1">
                            <div className="grid gap-3 rounded-xl border border-ork-line bg-ork-surface/60 p-3 lg:grid-cols-[minmax(0,1fr)_240px]">
                              <div className="flex flex-col gap-1.5">
                                <span className="text-[12.5px] font-semibold text-ork-ink">Descripción</span>
                                {locked ? <RichText value={it.description} className="text-[13px]" /> : <RichEditor value={it.description} onChange={(html) => setItem(i, { description: html })} minHeight={110} />}
                              </div>
                              <div className="flex flex-col gap-3">
                                <ImageField label="Imagen" value={it.image_url ?? ""} onChange={(v) => setItem(i, { image_url: v })} />
                                <TextField label="Link de pago" disabled={locked} placeholder="https://…" value={it.payment_url ?? ""} onChange={(e) => setItem(i, { payment_url: e.target.value })} />
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!locked && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => setDraft({ ...draft, items: [...draft.items, { name: "", description: "", quantity: 1, unit: "", unit_price: 0, discount: 0 }] })}>
                  Agregar línea
                </Button>
                {!!products.data?.length && (
                  <select
                    aria-label="Agregar desde el catálogo"
                    value=""
                    onChange={(e) => {
                      const prod = products.data?.find((x) => x.id === e.target.value);
                      if (prod) setDraft({ ...draft, items: [...draft.items, { name: prod.name, description: prod.description, quantity: 1, unit: prod.unit, unit_price: prod.unit_price, discount: 0, image_url: prod.image_url, payment_url: prod.payment_url }] });
                    }}
                    className="ork-focus h-8 rounded-lg border border-ork-line bg-ork-card px-2 text-[13px]"
                  >
                    <option value="">＋ Desde el catálogo…</option>
                    {products.data.filter((x) => x.active).map((x) => (
                      <option key={x.id} value={x.id}>
                        {x.name} · {money(x.unit_price, x.currency)}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}
          </Card>
        </div>
        )}

        <Card className="xl:sticky xl:top-4">
          <CardHeader title="Resumen" />
          <div className="grid grid-cols-2 gap-3">
            <SelectField label="Moneda" disabled={locked} value={draft.currency} onChange={(e) => setDraft({ ...draft, currency: e.target.value })}>
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </SelectField>
            <TextField label="Impuesto %" disabled={locked} inputMode="decimal" value={String(draft.tax_rate)} onChange={(e) => setDraft({ ...draft, tax_rate: Math.min(100, Number(e.target.value.replace(/[^\d.]/g, "")) || 0) })} />
            <TextField label="Válida hasta" type="date" disabled={locked} className="col-span-2" value={draft.valid_until ?? ""} onChange={(e) => setDraft({ ...draft, valid_until: e.target.value || null })} />
          </div>
          <dl className="mt-4 space-y-1.5 text-[13.5px]">
            <div className="flex justify-between text-ork-graphite">
              <dt>Subtotal</dt>
              <dd>{money(t.subtotal, draft.currency)}</dd>
            </div>
            {t.discounts > 0 && (
              <div className="flex justify-between text-ork-mist">
                <dt>Descuentos aplicados</dt>
                <dd>−{money(t.discounts, draft.currency)}</dd>
              </div>
            )}
            <div className="flex justify-between text-ork-graphite">
              <dt>Impuestos ({draft.tax_rate} %)</dt>
              <dd>{money(t.tax, draft.currency)}</dd>
            </div>
            <div className="flex justify-between border-t border-ork-line pt-2 text-[16px] font-bold text-ork-ink">
              <dt>Total</dt>
              <dd>{money(t.total, draft.currency)}</dd>
            </div>
          </dl>
          {p.design?.last_email && (
            <p className="mt-4 rounded-lg bg-ork-surface px-3 py-2 text-[12.5px] text-ork-graphite">
              Último envío a <strong>{p.design.last_email.to.join(", ")}</strong> desde {p.design.last_email.from}.
            </p>
          )}
          {!locked && (
            <button type="button" className="mt-5 inline-flex items-center gap-1 text-[12.5px] font-semibold text-bad hover:underline" onClick={() => window.confirm("¿Eliminar esta propuesta?") && remove.mutate()}>
              <Trash2 className="size-3.5" aria-hidden /> Eliminar propuesta
            </button>
          )}
        </Card>
      </div>
      {emailing && <EmailProposalModal proposal={p} onClose={() => setEmailing(false)} onSent={(data) => refresh(data)} />}
    </>
  );
}

const EMAIL_COPY = {
  es: {
    subject: "{{nombre}}, tu propuesta de {{mi_empresa}}",
    body: "<p>Hola {{nombre}}:</p><p>Como conversamos, te comparto la propuesta para {{empresa}}. Puedes revisarla, descargarla en PDF y aceptarla directamente desde el enlace.</p><p>Quedo atento(a) a tus comentarios.</p><p>Saludos,</p>",
  },
  en: {
    subject: "{{nombre}}, your proposal from {{mi_empresa}}",
    body: "<p>Hi {{nombre}},</p><p>As discussed, here is the proposal for {{empresa}}. You can review it, download it as a PDF and accept it directly from the link.</p><p>Looking forward to your feedback.</p><p>Best regards,</p>",
  },
  pt: {
    subject: "{{nombre}}, sua proposta da {{mi_empresa}}",
    body: "<p>Olá, {{nombre}}:</p><p>Conforme conversamos, compartilho a proposta para {{empresa}}. Você pode revisá-la, baixá-la em PDF e aceitá-la diretamente pelo link.</p><p>Fico à disposição para comentários.</p><p>Abraços,</p>",
  },
};

function EmailProposalModal({ proposal: p, onClose, onSent }: { proposal: Proposal; onClose: () => void; onSent: (data: Proposal) => void }) {
  const boxes = useMailboxes();
  const brand = useBrand();
  const { session } = useSession();
  const lang = (p.design?.language || brand.data?.brand.language || "es") as keyof typeof EMAIL_COPY;
  const [mailbox, setMailbox] = useState("");
  const [to, setTo] = useState(p.contact?.email ?? "");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(EMAIL_COPY[lang].subject);
  const [body, setBody] = useState(EMAIL_COPY[lang].body);
  const editorRef = useRef<Editor | null>(null);
  const active = (boxes.data?.items ?? []).filter((m) => m.status === "active");
  useEffect(() => {
    if (!mailbox && active.length) setMailbox((active.find((m) => m.email === session?.user.email) ?? active[0]).id);
  }, [active, mailbox, session]);
  const send = useMutation({
    mutationFn: () => api<Proposal>(`/api/proposals/${p.id}/email`, { method: "POST", json: { mailbox_id: mailbox, to, cc, subject, body } }),
    onSuccess: (data) => {
      onSent(data);
      onClose();
    },
  });
  const preview = (t: string) => t.replace(/\{\{\s*([a-z_]+)\s*(?:\|\s*([^}]*))?\}\}/g, (_, n: string, f?: string) => p.values?.[n] || (f ?? "").trim());
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title="Enviar propuesta por correo" description="Sale desde tu Gmail, queda en tus Enviados y el cliente recibe un botón para abrir la propuesta." wide>
      {boxes.isLoading ? (
        <Spinner />
      ) : !active.length ? (
        <Alert tone="blue">
          Primero conecta tu Gmail en{" "}
          <Link to="/configuracion/integraciones" className="font-semibold underline">
            Configuración → Integraciones
          </Link>
          .
        </Alert>
      ) : (
        <div className="flex flex-col gap-3">
          {send.error && <Alert>{send.error.message}</Alert>}
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField label="Desde" value={mailbox} onChange={(e) => setMailbox(e.target.value)}>
              {active.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.email}
                </option>
              ))}
            </SelectField>
            <TextField label="Para" value={to} onChange={(e) => setTo(e.target.value)} placeholder="cliente@empresa.com" hint={p.contact ? `Contacto: ${p.contact.full_name}` : undefined} />
          </div>
          <TextField label="CC (opcional)" value={cc} onChange={(e) => setCc(e.target.value)} placeholder="Separa varios con coma" />
          <TextField label="Asunto" value={subject} onChange={(e) => setSubject(e.target.value)} hint={`Así le llega: ${preview(subject)}`} />
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-[12.5px] font-semibold text-ork-graphite">Insertar:</span>
            {Object.entries(p.variables ?? {}).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => editorRef.current?.chain().focus().insertContent(`{{${k}}}`).run()}
                className="ork-focus rounded-full border border-ork-line px-2.5 py-0.5 text-[12px] font-semibold text-ork-graphite hover:border-ork-blue/40 hover:text-ork-blue"
              >
                {label}
              </button>
            ))}
          </div>
          <RichEditor value={body} onChange={setBody} onFocus={(e) => (editorRef.current = e)} minHeight={160} />
          <p className="text-[12px] text-ork-mist">Debajo del texto se agrega el botón «Ver la propuesta» con tus colores y luego tu firma de Gmail configurada en ORKESTA.</p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button icon={<Send className="size-4" aria-hidden />} loading={send.isPending} disabled={!mailbox || !to.trim()} onClick={() => send.mutate()}>
              Enviar ahora
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

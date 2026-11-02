import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, Languages, Loader2, Palette, RotateCcw, Trash2, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ProposalDocument, type DocData, type Look } from "../components/ProposalDocument";
import { uploadImage } from "../components/RichEditor";
import { Alert, Button, Card, CardHeader, cx, SelectField, TextField } from "../components/ui";
import { api } from "../lib/api";

export type Brand = {
  logo_url: string;
  primary: string;
  accent: string;
  font: Look["font"];
  template: Look["template"];
  language: Look["language"];
  legal_name: string;
  website: string;
  phone: string;
  address: string;
  tax_id: string;
  footer: string;
};
export type Design = {
  template?: Look["template"] | "";
  language?: Look["language"] | "";
  primary?: string;
  accent?: string;
  font?: Look["font"] | "";
  cover_url?: string;
  executive?: Look["executive"];
  last_email?: { to: string[]; from: string; thread_id: string };
};
type BrandResponse = { brand: Brand; templates: Record<string, string>; languages: Record<string, string>; fonts: Record<string, string>; can_edit: boolean };

const PALETTES: [string, string, string][] = [
  ["Azul ejecutivo", "#1E3A8A", "#06B6D4"],
  ["Esmeralda", "#065F46", "#F59E0B"],
  ["Grafito", "#111827", "#F97316"],
  ["Vino", "#7F1D1D", "#D4A373"],
  ["Violeta", "#4C1D95", "#22D3EE"],
  ["Océano", "#0C4A6E", "#84CC16"],
];
const TEMPLATE_INFO: Record<Look["template"], string> = {
  ejecutiva: "Portada con degradado de tu color, secciones numeradas.",
  moderna: "Portada dividida con formas geométricas y tarjetas.",
  corporativa: "Formal y sobria, encabezado institucional.",
  minimal: "Mucho espacio en blanco, tipografía grande.",
  audaz: "Portada oscura con diagonales y números grandes.",
};

export function useBrand() {
  return useQuery({ queryKey: ["proposal-brand"], queryFn: () => api<BrandResponse>("/api/proposal-brand"), staleTime: 60_000 });
}

/** Lo que verá el cliente: la propuesta hereda de la marca lo que no personalizó. */
export function lookFrom(brand: Brand, design: Design): Look {
  return {
    template: design.template || brand.template,
    language: design.language || brand.language,
    primary: design.primary || brand.primary,
    accent: design.accent || brand.accent,
    font: design.font || brand.font,
    cover_url: design.cover_url || "",
    logo_url: brand.logo_url,
    executive: design.executive ?? {},
    company: { legal_name: brand.legal_name, website: brand.website, phone: brand.phone, address: brand.address, tax_id: brand.tax_id, footer: brand.footer },
  };
}

/** Miniatura de cada plantilla para elegir de un vistazo. */
export function TemplateThumb({ kind, primary, accent }: { kind: Look["template"]; primary: string; accent: string }) {
  const line = (w: string, c = "#E5E7EB") => <span className="block h-1 rounded-full" style={{ width: w, background: c }} />;
  const body = (
    <div className="space-y-1.5 p-2">
      {line("70%", "#CBD5E1")}
      {line("90%")}
      {line("80%")}
      {line("60%", accent)}
      {line("85%")}
    </div>
  );
  if (kind === "ejecutiva")
    return (
      <div className="h-full bg-white">
        <div className="relative h-[46%] p-2" style={{ background: `linear-gradient(135deg, ${primary}, #000a)` }}>
          <span className="block h-2 w-6 rounded-sm bg-white" />
          <span className="mt-3 block h-0.5 w-5" style={{ background: accent }} />
          <span className="mt-1 block h-1.5 w-[70%] rounded bg-white/90" />
        </div>
        {body}
      </div>
    );
  if (kind === "moderna")
    return (
      <div className="h-full bg-[#F3F4F6]">
        <div className="flex h-[42%]">
          <div className="relative w-[42%] overflow-hidden" style={{ background: primary }}>
            <span className="absolute -right-3 -top-3 size-8 rounded-full opacity-60" style={{ background: accent }} />
          </div>
          <div className="flex-1 space-y-1 bg-white p-2">
            {line("50%", accent)}
            {line("90%", "#111827")}
            {line("60%")}
          </div>
        </div>
        <div className="space-y-1.5 p-1.5">
          <div className="rounded bg-white p-1.5" style={{ borderLeft: `3px solid ${primary}` }}>
            {line("80%")}
          </div>
          <div className="rounded bg-white p-1.5" style={{ borderLeft: `3px solid ${accent}` }}>
            {line("70%")}
          </div>
        </div>
      </div>
    );
  if (kind === "corporativa")
    return (
      <div className="h-full bg-white p-2">
        <div className="flex justify-between">
          <span className="block h-2 w-6 rounded-sm" style={{ background: primary }} />
          {line("30%")}
        </div>
        <span className="mt-1.5 block h-1" style={{ background: primary }} />
        <span className="mt-0.5 block h-0.5" style={{ background: accent }} />
        <div className="mx-auto mt-3 w-[70%] space-y-1">
          {line("100%", "#111827")}
          <span className="mx-auto block h-3 w-[60%] rounded-sm border" style={{ borderColor: primary }} />
        </div>
        <div className="mt-2 space-y-1">
          {line("40%", primary)}
          {line("95%")}
          {line("90%")}
        </div>
      </div>
    );
  if (kind === "minimal")
    return (
      <div className="h-full bg-white p-2.5">
        {line("25%")}
        <span className="mt-5 block h-2 w-[80%] rounded" style={{ background: primary, opacity: 0.85 }} />
        <span className="mt-1 block h-2 w-[55%] rounded" style={{ background: primary, opacity: 0.85 }} />
        <span className="mt-2 block h-px w-6" style={{ background: accent }} />
        <div className="mt-4 grid grid-cols-[1fr_2fr] gap-1.5">
          {line("90%", "#111827")}
          <div className="space-y-1">
            {line("100%")}
            {line("80%")}
          </div>
        </div>
      </div>
    );
  return (
    <div className="h-full bg-white">
      <div className="relative h-[46%] overflow-hidden bg-[#0B1020] p-2">
        <span className="absolute -right-2 top-0 h-full w-[55%] -skew-x-12" style={{ background: primary }} />
        <span className="absolute -right-1 top-0 h-full w-[16%] -skew-x-12" style={{ background: accent }} />
        <span className="relative mt-4 block h-1.5 w-[60%] rounded bg-white" />
        <span className="relative mt-1 block h-1.5 w-[40%] rounded" style={{ background: accent }} />
      </div>
      <div className="flex gap-1.5 p-2">
        <span className="text-[10px] font-black leading-none" style={{ color: primary }}>
          01
        </span>
        <div className="flex-1 space-y-1">
          {line("70%", "#111827")}
          {line("90%")}
        </div>
      </div>
    </div>
  );
}

function ColorField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-ork-ink">
      {label}
      <span className="flex h-11 items-center gap-2 rounded-xl border border-ork-line bg-ork-card px-2">
        <input type="color" value={value || placeholder || "#000000"} onChange={(e) => onChange(e.target.value.toUpperCase())} className="size-8 cursor-pointer rounded-md border-0 bg-transparent p-0" aria-label={label} />
        <input
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="w-full bg-transparent font-mono text-[13px] font-normal uppercase text-ork-graphite placeholder:text-ork-mist focus:outline-none"
          maxLength={7}
        />
      </span>
    </label>
  );
}

export function ImageField({ label, value, onChange, hint, round }: { label: string; value: string; onChange: (url: string) => void; hint?: string; round?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pick = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      onChange(await uploadImage(file));
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo subir.");
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = "";
    }
  };
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-semibold text-ork-ink">{label}</span>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => ref.current?.click()}
          className={cx(
            "ork-focus flex shrink-0 items-center justify-center overflow-hidden border border-dashed border-ork-line bg-ork-surface text-ork-mist hover:border-ork-blue/50",
            round ? "size-16 rounded-full" : "h-16 w-32 rounded-xl",
          )}
          aria-label={`Subir ${label.toLowerCase()}`}
        >
          {busy ? <Loader2 className="size-5 animate-spin" /> : value ? <img src={value} alt="" className={cx("size-full", round ? "object-cover" : "object-contain p-1")} /> : <ImagePlus className="size-5" />}
        </button>
        <div className="text-[12px] text-ork-mist">
          <p>{hint ?? "PNG, JPG o WEBP, hasta 3 MB."}</p>
          {value && (
            <button type="button" onClick={() => onChange("")} className="mt-1 inline-flex items-center gap-1 font-semibold text-bad hover:underline">
              <Trash2 className="size-3" /> Quitar
            </button>
          )}
        </div>
      </div>
      {error && <p className="text-[12px] text-bad">{error}</p>}
      <input ref={ref} type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
    </div>
  );
}

function TemplatePicker({ value, onChange, primary, accent, templates }: { value: string; onChange: (v: Look["template"]) => void; primary: string; accent: string; templates: Record<string, string> }) {
  return (
    <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-5">
      {(Object.keys(templates) as Look["template"][]).map((k) => (
        <button
          key={k}
          type="button"
          onClick={() => onChange(k)}
          aria-pressed={value === k}
          title={TEMPLATE_INFO[k]}
          className={cx("ork-focus group flex flex-col gap-1.5 rounded-xl p-1.5 text-left transition", value === k ? "bg-ork-blue/10 ring-2 ring-ork-blue" : "hover:bg-ork-ink/5")}
        >
          <span className="block aspect-[3/4] overflow-hidden rounded-lg border border-ork-line shadow-sm">
            <TemplateThumb kind={k} primary={primary} accent={accent} />
          </span>
          <span className="text-center text-[12px] font-semibold text-ork-ink">{templates[k]}</span>
        </button>
      ))}
    </div>
  );
}

// --- Panel de diseño en el editor de cada propuesta -----------------------------------------

export function DesignPanel({
  design,
  onChange,
  locked,
  onTranslate,
  translating,
}: {
  design: Design;
  onChange: (d: Design) => void;
  locked: boolean;
  onTranslate: () => void;
  translating: boolean;
}) {
  const b = useBrand();
  if (!b.data) return null;
  const { brand, templates, languages, fonts } = b.data;
  const look = lookFrom(brand, design);
  const ex = design.executive ?? {};
  const setEx = (patch: Partial<Look["executive"]>) => onChange({ ...design, executive: { ...ex, ...patch } });
  const custom = Boolean(design.primary || design.accent);
  return (
    <fieldset disabled={locked} className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Plantilla" description={TEMPLATE_INFO[look.template]} />
        <TemplatePicker value={look.template} onChange={(v) => onChange({ ...design, template: v })} primary={look.primary} accent={look.accent} templates={templates} />
      </Card>

      <Card>
        <CardHeader
          title="Colores y tipografía"
          description={custom ? "Colores personalizados para esta propuesta." : "Usando los colores de tu marca."}
          action={
            custom && (
              <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" aria-hidden />} onClick={() => onChange({ ...design, primary: "", accent: "" })}>
                Usar marca
              </Button>
            )
          }
        />
        <div className="mb-3 flex flex-wrap gap-2">
          {PALETTES.map(([name, p, a]) => (
            <button
              key={name}
              type="button"
              title={name}
              onClick={() => onChange({ ...design, primary: p, accent: a })}
              className={cx("ork-focus flex overflow-hidden rounded-full ring-offset-2", look.primary === p && look.accent === a && "ring-2 ring-ork-blue")}
            >
              <span className="size-6" style={{ background: p }} />
              <span className="size-6" style={{ background: a }} />
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <ColorField label="Principal" value={design.primary ?? ""} placeholder={brand.primary} onChange={(v) => onChange({ ...design, primary: v })} />
          <ColorField label="Acento" value={design.accent ?? ""} placeholder={brand.accent} onChange={(v) => onChange({ ...design, accent: v })} />
        </div>
        <SelectField label="Tipografía" className="mt-3" value={design.font || ""} onChange={(e) => onChange({ ...design, font: e.target.value as Look["font"] })}>
          <option value="">De la marca ({fonts[brand.font]})</option>
          {Object.entries(fonts).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </SelectField>
      </Card>

      <Card>
        <CardHeader title="Idioma y portada" />
        <div className="flex flex-col gap-3">
          <div className="flex items-end gap-2">
            <SelectField label="Idioma del documento" className="flex-1" value={look.language} onChange={(e) => onChange({ ...design, language: e.target.value as Look["language"] })}>
              {Object.entries(languages).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </SelectField>
            <Button variant="secondary" icon={<Languages className="size-4" aria-hidden />} loading={translating} onClick={onTranslate} title="Reemplaza los textos con el borrador en el idioma elegido">
              Textos en este idioma
            </Button>
          </div>
          <p className="text-[12px] text-ork-mist">Títulos, tabla, botones y fechas cambian solos. «Textos en este idioma» rehace el borrador traducido (reemplaza lo que escribiste).</p>
          <ImageField label="Imagen de portada (opcional)" value={design.cover_url ?? ""} onChange={(v) => onChange({ ...design, cover_url: v })} hint="Se integra en la portada con tus colores. Ideal: horizontal, 1600 px." />
        </div>
      </Card>

      <Card>
        <CardHeader title="Ejecutivo comercial" description="Aparece como «Tu contacto» en la propuesta. Se recuerda para las siguientes." />
        <div className="flex flex-col gap-3">
          <ImageField label="Foto" round value={ex.photo_url ?? ""} onChange={(v) => setEx({ photo_url: v })} hint="Cuadrada, de frente." />
          <div className="grid grid-cols-2 gap-3">
            <TextField label="Nombre" value={ex.name ?? ""} onChange={(e) => setEx({ name: e.target.value })} />
            <TextField label="Cargo" value={ex.title ?? ""} onChange={(e) => setEx({ title: e.target.value })} placeholder="Gerente comercial" />
            <TextField label="Correo" type="email" value={ex.email ?? ""} onChange={(e) => setEx({ email: e.target.value })} />
            <TextField label="Teléfono / WhatsApp" value={ex.phone ?? ""} onChange={(e) => setEx({ phone: e.target.value })} />
          </div>
          <TextField label="LinkedIn" value={ex.linkedin ?? ""} onChange={(e) => setEx({ linkedin: e.target.value })} placeholder="https://linkedin.com/in/…" />
          <TextField label="Enlace para agendar" value={ex.calendar_url ?? ""} onChange={(e) => setEx({ calendar_url: e.target.value })} placeholder="https://calendly.com/…" />
        </div>
      </Card>
    </fieldset>
  );
}

// --- Marca de la empresa (pestaña «Marca» en Propuestas) ------------------------------------

const SAMPLE: Omit<DocData, "look"> = {
  number: 12,
  title: "Propuesta para Banco Andino",
  currency: "USD",
  items: [
    { name: "Patrocinio Oro", description: "Stand, keynote y 10 pases", quantity: 1, unit: "paquete", unit_price: 15000, discount: 0 },
    { name: "Mesa ejecutiva", description: "Encuentro con 20 CIO", quantity: 1, unit: "", unit_price: 4000, discount: 10 },
  ],
  sections: [
    { key: "resumen", title: "Resumen", body: "Hola, Laura:\n\nGracias por el tiempo y la confianza. Presentamos cómo podemos acompañar a Banco Andino con un alcance claro y una inversión transparente." },
    { key: "alcance", title: "Alcance y entregables", body: "• Stand de 18 m² en zona principal\n• Keynote de 20 minutos\n• 10 pases ejecutivos" },
  ],
  tax_rate: 0,
  totals: { subtotal: 18600, discounts: 400, tax: 0, total: 18600 },
  valid_until: new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10),
  company: "Banco Andino",
  contact_name: "Laura Gómez",
  issuer: "",
  created_at: new Date().toISOString(),
};

export function BrandSettings() {
  const client = useQueryClient();
  const b = useBrand();
  const [draft, setDraft] = useState<Brand | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (b.data) setDraft(b.data.brand);
  }, [b.data]);
  const save = useMutation({
    mutationFn: () => api<{ brand: Brand }>("/api/proposal-brand", { method: "PUT", json: draft }),
    onSuccess: (data) => {
      client.setQueryData(["proposal-brand"], (old: BrandResponse | undefined) => (old ? { ...old, brand: data.brand } : old));
      client.invalidateQueries({ queryKey: ["proposal"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });
  if (!b.data || !draft) return null;
  const { templates, languages, fonts, can_edit } = b.data;
  const set = (patch: Partial<Brand>) => setDraft({ ...draft, ...patch });
  const dirty = JSON.stringify(draft) !== JSON.stringify(b.data.brand);
  return (
    <div className="grid items-start gap-5 xl:grid-cols-[440px_minmax(0,1fr)]">
      <fieldset disabled={!can_edit} className="flex flex-col gap-5">
        <Card>
          <CardHeader title="Identidad" description="Se aplica a todas tus propuestas; cada una puede personalizarse." />
          <div className="flex flex-col gap-4">
            <ImageField label="Logo de la empresa" value={draft.logo_url} onChange={(v) => set({ logo_url: v })} hint="PNG con fondo transparente se ve mejor. Horizontal." />
            <div>
              <p className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-ork-ink">
                <Palette className="size-4" aria-hidden /> Colores de la marca
              </p>
              <div className="mb-3 flex flex-wrap gap-2">
                {PALETTES.map(([name, p, a]) => (
                  <button key={name} type="button" title={name} onClick={() => set({ primary: p, accent: a })} className={cx("ork-focus flex overflow-hidden rounded-full ring-offset-2", draft.primary === p && draft.accent === a && "ring-2 ring-ork-blue")}>
                    <span className="size-6" style={{ background: p }} />
                    <span className="size-6" style={{ background: a }} />
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-3">
                <ColorField label="Principal" value={draft.primary} onChange={(v) => set({ primary: v })} />
                <ColorField label="Acento" value={draft.accent} onChange={(v) => set({ accent: v })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <SelectField label="Tipografía" value={draft.font} onChange={(e) => set({ font: e.target.value as Brand["font"] })}>
                {Object.entries(fonts).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </SelectField>
              <SelectField label="Idioma por defecto" value={draft.language} onChange={(e) => set({ language: e.target.value as Brand["language"] })}>
                {Object.entries(languages).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </SelectField>
            </div>
            <div>
              <p className="mb-2 text-[13px] font-semibold text-ork-ink">Plantilla por defecto</p>
              <TemplatePicker value={draft.template} onChange={(v) => set({ template: v })} primary={draft.primary} accent={draft.accent} templates={templates} />
            </div>
          </div>
        </Card>
        <Card>
          <CardHeader title="Datos de la empresa" description="Aparecen en el encabezado y pie de cada propuesta." />
          <div className="flex flex-col gap-3">
            <TextField label="Razón social / nombre comercial" value={draft.legal_name} onChange={(e) => set({ legal_name: e.target.value })} />
            <div className="grid grid-cols-2 gap-3">
              <TextField label="NIT / RUC / RFC" value={draft.tax_id} onChange={(e) => set({ tax_id: e.target.value })} />
              <TextField label="Teléfono" value={draft.phone} onChange={(e) => set({ phone: e.target.value })} />
            </div>
            <TextField label="Sitio web" value={draft.website} onChange={(e) => set({ website: e.target.value })} placeholder="www.tuempresa.com" />
            <TextField label="Dirección" value={draft.address} onChange={(e) => set({ address: e.target.value })} />
            <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-ork-ink">
              Nota al pie (opcional)
              <textarea value={draft.footer} onChange={(e) => set({ footer: e.target.value })} rows={2} placeholder="Ej.: Precios en USD. Esta propuesta es confidencial." className="ork-focus rounded-xl border border-ork-line px-3 py-2 text-[14px] font-normal" />
            </label>
          </div>
        </Card>
        {save.error && <Alert>{save.error.message}</Alert>}
        {can_edit ? (
          <Button disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
            {saved ? "Marca guardada ✓" : "Guardar marca"}
          </Button>
        ) : (
          <Alert tone="blue">Solo administradores y supervisores pueden cambiar la marca.</Alert>
        )}
      </fieldset>
      <div className="xl:sticky xl:top-4">
        <p className="mb-2 flex items-center gap-1.5 text-[12.5px] font-semibold text-ork-mist">
          <UserRound className="size-4" aria-hidden /> Vista previa con datos de ejemplo
        </p>
        <div className="max-h-[calc(100dvh-140px)] overflow-y-auto rounded-2xl">
          <ProposalDocument data={{ ...SAMPLE, issuer: draft.legal_name, look: { ...lookFrom(draft, {}), executive: { name: "Ana López", title: "Gerente comercial", email: "ana@tuempresa.com", phone: "+57 300 000 0000" } } }} />
        </div>
      </div>
    </div>
  );
}

import * as Menu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, CircleCheck, CircleX, RefreshCw, Save, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { Alert, Badge, Button, cx, Modal, SelectField, TextField } from "./ui";

type Opt = { value: string; label: string };
type PersonaPreset = Opt & { pains: string[]; goals: string[]; objections: string[] };
type SavedPersona = { id: string; name: string; area: string; industry: string; pains: string[]; goals: string[]; objections: string[]; notes: string };
type CopyOptions = {
  goals: Opt[];
  tones: Opt[];
  frameworks: Opt[];
  languages: Opt[];
  industries: Opt[];
  personas: PersonaPreset[];
  saved_personas: SavedPersona[];
};
type Check = { id: string; status: "ok" | "warn" | "bad"; label: string; tip: string };
export type Review = { score: number; words: number; checks: Check[] };
type GeneratedStep = { subject: string; subjects: string[]; body: string; delay_days: number; review: Review };
type Product = { id: string; name: string };
export type DraftStep = { subject: string; body: string; delay_days: number };

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

export function scoreTone(score: number): "ok" | "warn" | "bad" {
  return score >= 80 ? "ok" : score >= 60 ? "warn" : "bad";
}

const STATUS_ICON = { ok: CircleCheck, warn: CircleAlert, bad: CircleX };
const STATUS_COLOR = { ok: "text-ok", warn: "text-warn", bad: "text-bad" };

export function ReviewList({ review }: { review: Review }) {
  return (
    <ul className="space-y-2">
      {[...review.checks]
        .sort((a, b) => ["bad", "warn", "ok"].indexOf(a.status) - ["bad", "warn", "ok"].indexOf(b.status))
        .map((c) => {
          const Icon = STATUS_ICON[c.status];
          return (
            <li key={c.id} className="flex gap-2">
              <Icon className={cx("mt-0.5 size-4 shrink-0", STATUS_COLOR[c.status])} aria-hidden />
              <span>
                <span className="block text-[13px] font-semibold text-ork-ink">{c.label}</span>
                {c.status !== "ok" && c.tip && <span className="block text-[12.5px] text-ork-mist">{c.tip}</span>}
              </span>
            </li>
          );
        })}
    </ul>
  );
}

/** Calidad del correo en vivo (revisor propio de ORKESTA), con sugerencias al abrirlo. */
export function StepReview({ subject, body, step }: { subject: string; body: string; step: number }) {
  const [input, setInput] = useState({ subject, body });
  useEffect(() => {
    const t = setTimeout(() => setInput({ subject, body }), 700);
    return () => clearTimeout(t);
  }, [subject, body]);
  const q = useQuery({
    queryKey: ["copy-review", step, input.subject, input.body],
    queryFn: () => api<Review>("/api/copy/analyze", { method: "POST", json: { ...input, step } }),
    staleTime: 60_000,
  });
  if (!q.data) return null;
  const tone = scoreTone(q.data.score);
  const pending = q.data.checks.filter((c) => c.status !== "ok").length;
  return (
    <Menu.Root>
      <Menu.Trigger
        className={cx(
          "ork-focus inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold",
          tone === "ok" ? "border-ok/30 bg-ok/10 text-ok" : tone === "warn" ? "border-warn/30 bg-warn/10 text-warn" : "border-bad/30 bg-bad/10 text-bad",
        )}
        aria-label={`Calidad del correo: ${q.data.score} de 100`}
      >
        <Sparkles className="size-3.5" aria-hidden /> Calidad {q.data.score}/100
        {pending > 0 && <span className="font-normal">· {pending} sugerencia(s)</span>}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={6} className="z-50 w-[min(92vw,360px)] rounded-2xl border border-ork-line bg-white p-4 shadow-[var(--shadow-pop)]">
          <p className="mb-3 text-[13px] font-bold text-ork-ink">
            Revisión del correo · {q.data.words} palabras
          </p>
          <ReviewList review={q.data} />
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function Highlight({ html }: { html: string }) {
  // El HTML lo genera el servidor (con los datos del usuario escapados); se resaltan las variables.
  const marked = html.replace(/\{\{([^}]+)\}\}/g, '<span class="rounded bg-ork-blue/10 px-1 font-semibold text-ork-blue">{{$1}}</span>');
  return <div className="ork-prose text-[13.5px] leading-relaxed text-ork-ink [&_li]:ml-5 [&_li]:list-disc [&_p]:mb-2.5" dangerouslySetInnerHTML={{ __html: marked }} />;
}

/** Asistente de redacción: secuencia completa según producto, buyer persona, objetivo y tono. */
export function CopyAssistant({ open, onOpenChange, onUse }: { open: boolean; onOpenChange: (v: boolean) => void; onUse: (steps: DraftStep[]) => void }) {
  const client = useQueryClient();
  const options = useQuery({ queryKey: ["copy-options"], queryFn: () => api<CopyOptions>("/api/copy/options"), enabled: open });
  const products = useQuery({ queryKey: ["products"], queryFn: () => api<Product[]>("/api/products"), enabled: open });
  const [form, setForm] = useState({
    product_id: "",
    product: "",
    summary: "",
    benefits: "",
    proof: "",
    persona: "auto",
    pains: "",
    goals: "",
    industry: "",
    goal: "reunion",
    tone: "cercano",
    framework: "pas",
    language: "es",
    steps: 3,
    link: "",
  });
  const [variant, setVariant] = useState(0);
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [personaName, setPersonaName] = useState("");
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));

  const presets = options.data?.personas ?? [];
  const saved = options.data?.saved_personas ?? [];
  const pickPersona = (value: string) => {
    if (value.startsWith("saved:")) {
      const p = saved.find((s) => `saved:${s.id}` === value);
      if (p) set({ persona: value, pains: p.pains.join("\n"), goals: p.goals.join("\n"), industry: p.industry || form.industry });
      return;
    }
    const p = presets.find((x) => x.value === value);
    set({ persona: value, pains: (p?.pains ?? []).join("\n"), goals: (p?.goals ?? []).join("\n") });
  };

  const payload = useMemo(() => {
    const savedId = form.persona.startsWith("saved:") ? form.persona.slice(6) : null;
    const savedPersona = saved.find((s) => s.id === savedId);
    return {
      product_id: form.product_id || null,
      product: form.product,
      summary: form.summary,
      benefits: lines(form.benefits),
      proof: form.proof,
      persona_id: null,
      area: savedPersona ? savedPersona.area : form.persona,
      persona_label: savedPersona ? savedPersona.name : "",
      pains: form.persona === "auto" ? [] : lines(form.pains),
      goals: form.persona === "auto" ? [] : lines(form.goals),
      industry: form.industry,
      goal: form.goal,
      tone: form.tone,
      framework: form.framework,
      language: form.language,
      steps: form.steps,
      link: form.link,
    };
  }, [form, saved]);

  const compose = useMutation({
    mutationFn: (v: number) => api<{ steps: GeneratedStep[]; tips: string[] }>("/api/copy/compose", { method: "POST", json: { ...payload, variant: v } }),
    onSuccess: () => setChosen({}),
  });
  const savePersona = useMutation({
    mutationFn: () =>
      api<SavedPersona>("/api/copy/personas", {
        method: "POST",
        json: { name: personaName, area: payload.area, industry: form.industry, pains: lines(form.pains), goals: lines(form.goals) },
      }),
    onSuccess: (p) => {
      client.invalidateQueries({ queryKey: ["copy-options"] });
      setPersonaName("");
      set({ persona: `saved:${p.id}` });
    },
  });

  const result = compose.data;
  const canGenerate = Boolean(form.product_id || form.product.trim());

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Redactar con IA" description="La IA de ORKESTA escribe la secuencia completa según tu producto, tu buyer persona y tu objetivo." wide="xl">
      <div className="grid gap-5 lg:grid-cols-[400px_minmax(0,1fr)]">
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            compose.mutate(variant);
          }}
        >
          <p className="text-[12px] font-bold uppercase tracking-[0.12em] text-ork-mist">1 · Qué ofreces</p>
          <SelectField label="Producto del catálogo" value={form.product_id} onChange={(e) => set({ product_id: e.target.value })}>
            <option value="">Escribirlo yo</option>
            {products.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </SelectField>
          {!form.product_id && (
            <>
              <TextField label="Producto o servicio" placeholder="Ej.: Plataforma de prospección B2B" value={form.product} onChange={(e) => set({ product: e.target.value })} />
              <TextField label="En una frase, qué hace" placeholder="Ej.: Encuentra leads y sus correos en minutos." value={form.summary} onChange={(e) => set({ summary: e.target.value })} />
            </>
          )}
          <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-ork-ink">
            Beneficios (uno por línea, con cifras si las tienes)
            <textarea
              rows={3}
              value={form.benefits}
              onChange={(e) => set({ benefits: e.target.value })}
              placeholder={"Reducir el tiempo de prospección un 70%\nLanzar campañas desde Gmail"}
              className="ork-focus rounded-xl border border-ork-line px-3 py-2 text-[13.5px] font-normal"
            />
          </label>
          <TextField label="Prueba social (opcional)" placeholder="Ej.: Más de 40 empresas en 5 países ya lo usan" value={form.proof} onChange={(e) => set({ proof: e.target.value })} />

          <p className="mt-2 text-[12px] font-bold uppercase tracking-[0.12em] text-ork-mist">2 · A quién le escribes</p>
          <SelectField label="Buyer persona" value={form.persona} onChange={(e) => pickPersona(e.target.value)}>
            {saved.length > 0 && (
              <optgroup label="Mis buyer personas">
                {saved.map((p) => (
                  <option key={p.id} value={`saved:${p.id}`}>
                    {p.name}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="Sugeridos por área">
              {presets.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </optgroup>
          </SelectField>
          {form.persona === "auto" ? (
            <p className="rounded-xl bg-ork-blue/5 px-3 py-2 text-[12.5px] text-ork-graphite">
              Cada contacto recibe el reto típico de su área y su industria al momento del envío, según su cargo.
            </p>
          ) : (
            <>
              <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-ork-ink">
                Retos que le quitan el sueño (uno por línea)
                <textarea rows={3} value={form.pains} onChange={(e) => set({ pains: e.target.value })} className="ork-focus rounded-xl border border-ork-line px-3 py-2 text-[13.5px] font-normal" />
              </label>
              <TextField label="Metas que persigue (separadas por línea)" value={form.goals.replace(/\n/g, ", ")} onChange={(e) => set({ goals: e.target.value.split(/,\s*/).join("\n") })} />
            </>
          )}
          <SelectField label="Industria del cliente" value={form.industry} onChange={(e) => set({ industry: e.target.value })}>
            <option value="">{form.persona === "auto" ? "La de cada contacto" : "Sin especificar"}</option>
            {options.data?.industries.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </SelectField>
          {form.persona !== "auto" && !form.persona.startsWith("saved:") && (
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <TextField label="Guardar este buyer persona como" placeholder="Ej.: Jefes de compras retail" value={personaName} onChange={(e) => setPersonaName(e.target.value)} />
              </div>
              <Button type="button" variant="secondary" disabled={personaName.trim().length < 2} loading={savePersona.isPending} icon={<Save className="size-4" aria-hidden />} onClick={() => savePersona.mutate()}>
                Guardar
              </Button>
            </div>
          )}
          {savePersona.error && <Alert>{savePersona.error.message}</Alert>}

          <p className="mt-2 text-[12px] font-bold uppercase tracking-[0.12em] text-ork-mist">3 · Cómo lo dices</p>
          <div className="grid grid-cols-2 gap-3">
            <SelectField label="Objetivo" value={form.goal} onChange={(e) => set({ goal: e.target.value })}>
              {options.data?.goals.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </SelectField>
            <SelectField label="Tono" value={form.tone} onChange={(e) => set({ tone: e.target.value })}>
              {options.data?.tones.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </SelectField>
            <SelectField label="Estructura" value={form.framework} onChange={(e) => set({ framework: e.target.value })}>
              {options.data?.frameworks.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </SelectField>
            <SelectField label="Idioma" value={form.language} onChange={(e) => set({ language: e.target.value })}>
              {options.data?.languages.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </SelectField>
            <SelectField label="Correos en la secuencia" value={String(form.steps)} onChange={(e) => set({ steps: Number(e.target.value) })}>
              {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
            </SelectField>
          </div>
          <TextField label="Enlace de agenda o recurso (opcional)" placeholder="https://…" value={form.link} onChange={(e) => set({ link: e.target.value })} />
          {compose.error && <Alert>{compose.error.message}</Alert>}
          <Button type="submit" size="lg" disabled={!canGenerate} loading={compose.isPending} icon={<Wand2 className="size-4" aria-hidden />}>
            {result ? "Volver a generar" : "Generar secuencia"}
          </Button>
        </form>

        <div className="min-w-0 rounded-2xl border border-ork-line bg-ork-surface/50 p-4">
          {!result ? (
            <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center">
              <span className="ork-gradient inline-flex size-12 items-center justify-center rounded-2xl text-white">
                <Sparkles className="size-6" aria-hidden />
              </span>
              <p className="mt-3 text-[15px] font-bold text-ork-ink">Tu secuencia aparecerá aquí</p>
              <p className="mt-1 max-w-sm text-[13px] text-ork-mist">Con asuntos alternativos, seguimientos en el mismo hilo y una revisión de calidad de cada correo.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[14px] font-bold text-ork-ink">{result.steps.length} correo(s) listos</p>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={<RefreshCw className="size-3.5" aria-hidden />}
                    loading={compose.isPending}
                    onClick={() => {
                      const v = variant + 1;
                      setVariant(v);
                      compose.mutate(v);
                    }}
                  >
                    Otra versión
                  </Button>
                  <Button
                    size="sm"
                    icon={<Check className="size-3.5" aria-hidden />}
                    onClick={() => {
                      onUse(result.steps.map((s, i) => ({ subject: chosen[i] ?? s.subject, body: s.body, delay_days: s.delay_days })));
                      onOpenChange(false);
                    }}
                  >
                    Usar en la campaña
                  </Button>
                </div>
              </div>
              {result.tips.length > 0 && (
                <Alert tone="blue">
                  {result.tips.map((t) => (
                    <span key={t} className="block">
                      {t}
                    </span>
                  ))}
                </Alert>
              )}
              {result.steps.map((s, i) => (
                <article key={i} className="rounded-xl border border-ork-line bg-white p-4">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-[13px] font-bold text-ork-ink">
                      {i === 0 ? "Correo 1 · al lanzar" : `Correo ${i + 1} · ${s.delay_days} días sin respuesta · en el mismo hilo`}
                    </p>
                    <Badge tone={scoreTone(s.review.score) === "ok" ? "ok" : scoreTone(s.review.score) === "warn" ? "warn" : "bad"}>Calidad {s.review.score}/100</Badge>
                  </div>
                  {s.subjects.length > 0 && (
                    <div className="mb-3">
                      <p className="mb-1.5 text-[12px] font-semibold text-ork-mist">Elige el asunto</p>
                      <div className="flex flex-wrap gap-1.5">
                        {s.subjects.map((subj) => {
                          const on = (chosen[i] ?? s.subject) === subj;
                          return (
                            <button
                              key={subj}
                              type="button"
                              onClick={() => setChosen((c) => ({ ...c, [i]: subj }))}
                              aria-pressed={on}
                              className={cx("ork-focus rounded-full border px-2.5 py-1 text-[12.5px] font-medium", on ? "border-ork-blue bg-ork-blue/10 text-ork-blue" : "border-ork-line text-ork-graphite hover:border-ork-mist")}
                            >
                              {on && <Check className="mr-1 inline size-3" aria-hidden />}
                              {subj}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  <Highlight html={s.body} />
                  {s.review.checks.some((c) => c.status !== "ok") && (
                    <details className="mt-2 text-[12.5px]">
                      <summary className="cursor-pointer font-semibold text-ork-graphite">Ver sugerencias</summary>
                      <div className="mt-2">
                        <ReviewList review={s.review} />
                      </div>
                    </details>
                  )}
                </article>
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

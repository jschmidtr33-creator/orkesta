// Campaña automatizada: una frase → ORKESTA busca, completa, valida, arma la lista y Concertino
// escribe la secuencia. Queda en borrador hasta que la persona la aprueba.
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert, Button, Card, PageHeader, SelectField } from "../components/ui";
import { api } from "../lib/api";
import { useMailboxes } from "./campaigns";

type Preview = { chips: string[]; target: number; goal: number | null; missing: string[] };

const EXAMPLES = [
  "Gerentes de compras de supermercados en Colombia, para conseguir 10 reuniones este mes",
  "CFO de bancos en México y Perú, 200 contactos",
  "Directores de recursos humanos en empresas de más de 200 empleados en Latam",
];

export function AutoCampaignPage() {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const boxes = useMailboxes();
  const active = (boxes.data?.items ?? []).filter((m) => m.status === "active");
  const [mailbox, setMailbox] = useState("");
  const navigate = useNavigate();
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 450);
    return () => clearTimeout(t);
  }, [text]);
  const preview = useQuery({
    queryKey: ["auto-preview", debounced],
    queryFn: () => api<Preview>("/api/campaigns/auto/preview", { method: "POST", json: { text: debounced } }),
    enabled: debounced.length >= 3,
  });
  const start = useMutation({
    mutationFn: () =>
      api<{ id: string }>("/api/campaigns/auto", { method: "POST", json: { text: text.trim(), mailbox_id: mailbox || active[0]?.id || undefined } }),
    onSuccess: (c) => navigate(`/campanas/${c.id}`),
  });
  const p = preview.data;
  const ready = text.trim().length >= 3 && p && p.missing.length === 0;
  return (
    <div>
      <Link to="/campanas" className="mb-3 inline-flex items-center gap-1 text-[13px] font-semibold text-ork-mist hover:text-ork-ink">
        <ArrowLeft className="size-4" aria-hidden /> Campañas
      </Link>
      <PageHeader title="Campaña automatizada" description="Una campaña completa a partir de una frase. Si prefieres armarla paso a paso, usa «Nueva campaña»." />
      <Card className="mx-auto max-w-3xl p-7">
        <div className="flex items-center gap-2 text-ork-violet">
          <Wand2 className="size-5" aria-hidden />
          <span className="text-[12.5px] font-semibold uppercase tracking-[0.14em]">Campaña automatizada</span>
        </div>
        <h2 className="mt-3 text-[24px] font-bold text-ork-ink">¿A quién quieres escribirle?</h2>
        <p className="mt-1 text-[14px] text-ork-mist">
          Dilo con tus palabras: cargo, sector, país y, si quieres, tu meta de reuniones. ORKESTA busca, completa y valida los contactos, y Concertino escribe la secuencia con tu producto. Tú solo revisas y apruebas.
        </p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          maxLength={400}
          placeholder={EXAMPLES[0]}
          className="ork-focus mt-5 w-full resize-none rounded-2xl border-2 border-ork-blue/30 bg-ork-blue/[0.03] p-4 text-[16px] text-ork-ink placeholder:text-ork-mist"
        />
        {!text && (
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button key={e} type="button" onClick={() => setText(e)} className="rounded-full border border-ork-line px-3 py-1.5 text-left text-[12.5px] text-ork-graphite hover:border-ork-blue/40 hover:text-ork-ink">
                {e}
              </button>
            ))}
          </div>
        )}
        {p && text.trim() && (
          <>
            <p className="mt-4 text-[12.5px] font-semibold text-ork-graphite">Entendí esto:</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {p.chips.map((c) => (
                <span key={c} className="rounded-full border border-ork-line bg-ork-card px-3 py-1.5 text-[13px] text-ork-ink">{c}</span>
              ))}
              <span className="rounded-full border border-ork-violet/30 bg-ork-violet/10 px-3 py-1.5 text-[13px] font-semibold text-ork-violet">
                {p.goal ? `Meta: ${p.goal} reuniones → ${p.target} contactos` : `Hasta ${p.target} contactos`}
              </span>
            </div>
            {p.missing.map((m) => (
              <p key={m} className="mt-2 text-[13px] text-warn">{m}</p>
            ))}
          </>
        )}
        <div className="mt-6 grid gap-3 sm:grid-cols-3">
          <div>
            {active.length > 1 ? (
              <SelectField label="Enviar desde" value={mailbox || active[0]?.id} onChange={(e) => setMailbox(e.target.value)}>
                {active.map((m) => (
                  <option key={m.id} value={m.id}>{m.email}</option>
                ))}
              </SelectField>
            ) : (
              <div className="rounded-xl bg-ork-ink/[0.03] p-3 text-[13px]">
                <p className="text-ork-mist">Enviar desde</p>
                <p className="font-semibold text-ork-ink">{active[0]?.email ?? "Conecta tu correo antes de lanzar"}</p>
              </div>
            )}
          </div>
          <div className="rounded-xl bg-ork-ink/[0.03] p-3 text-[13px]">
            <p className="text-ork-mist">Secuencia</p>
            <p className="font-semibold text-ork-ink">3 correos en 10 días</p>
          </div>
          <div className="rounded-xl bg-ork-ink/[0.03] p-3 text-[13px]">
            <p className="text-ork-mist">Usa de tu plan</p>
            <p className="font-semibold text-ork-ink">{p ? `hasta ${p.target} verificaciones` : "solo lo necesario"}</p>
          </div>
        </div>
        {start.error && (
          <div className="mt-4">
            <Alert>{start.error.message}</Alert>
          </div>
        )}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <span className="text-[12.5px] text-ork-mist">Nada se envía sin tu aprobación. Solo usa correos confiables.</span>
          <Button size="lg" icon={<Sparkles className="size-4" aria-hidden />} disabled={!ready} loading={start.isPending} onClick={() => start.mutate()}>
            Prepárala
          </Button>
        </div>
      </Card>
    </div>
  );
}

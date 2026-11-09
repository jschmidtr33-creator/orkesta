import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, Music2, Send, ThumbsDown, X } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Alert, Badge, Button, cx } from "./ui";

/** Contexto de la consulta: la cuenta, la respuesta o la campaña desde donde se abre. */
export type AskContext = {
  label?: string;
  mode?: string;
  question?: string;
  contact_id?: string;
  deal_id?: string;
  recipient_id?: string;
  campaign_id?: string;
  autoAsk?: boolean;
  prompt?: string;
};

type Option = { code: string; name: string; why: string; text: string; record: string | null };
type Advice = {
  case_id: string | null;
  topic: string;
  topic_label: string;
  objection: string | null;
  objection_label: string | null;
  summary: string;
  options: Option[];
  tips: string[];
  sections: { title: string; items: string[] }[];
  next_step: string;
  missing: string[];
  sources: string[];
};
type Info = { topics: { code: string; label: string }[]; objections: { code: string; label: string }[]; plan: { full: boolean; topics: string[] } };
type Turn = { id: number; question: string; advice?: Advice; error?: string };

const QUICK: { label: string; mode?: string; question?: string; needs?: keyof AskContext }[] = [
  { label: "Manejar una objeción", mode: "objecion" },
  { label: "¿Cómo le hablo?", mode: "perfil", needs: "contact_id" },
  { label: "¿Qué hago con esta cuenta?", mode: "cuenta", needs: "deal_id" },
  { label: "Revisar mi campaña", mode: "campana" },
  { label: "Preparar una reunión", mode: "reunion" },
  { label: "¿A qué sector apunto?", mode: "estrategia" },
  { label: "¿Cómo presento la propuesta?", mode: "propuesta" },
];

const Ctx = createContext<{ open: (c?: AskContext) => void }>({ open: () => undefined });

export function useConcertino() {
  return useContext(Ctx);
}

/** Concertino: el asesor de ventas de ORKESTA. Solo responde cuando se le pide. */
export function ConcertinoProvider({ children }: { children: ReactNode }) {
  const [context, setContext] = useState<AskContext | null>(null);
  return (
    <Ctx.Provider value={{ open: (c) => setContext({ ...(c ?? {}) }) }}>
      {children}
      <button
        type="button"
        onClick={() => setContext({})}
        className="ork-focus fixed bottom-5 right-5 z-30 flex items-center gap-2 rounded-full bg-ork-blue-solid px-4 py-3 text-[13.5px] font-semibold text-white shadow-[var(--shadow-pop)] hover:bg-ork-blue-solid/90 print:hidden"
        aria-label="Abrir Concertino, tu asesor de ventas"
      >
        <Music2 className="size-4" aria-hidden /> Concertino
      </button>
      {context && createPortal(<Panel key={JSON.stringify(context)} initial={context} onClose={() => setContext(null)} />, document.body)}
    </Ctx.Provider>
  );
}

/** Botón para pedirle ayuda a Concertino desde una cuenta, una respuesta o una campaña. */
export function AskConcertino({ children = "Pregúntale a Concertino", size = "sm", ...context }: AskContext & { children?: ReactNode; size?: "sm" | "md" }) {
  const { open } = useConcertino();
  return (
    <Button size={size} variant="secondary" icon={<Music2 className="size-3.5" aria-hidden />} onClick={() => open(context)}>
      {children}
    </Button>
  );
}

let counter = 0;
const CHIP = "ork-focus rounded-full border border-ork-line bg-ork-card px-3 py-1.5 text-[12.5px] font-semibold text-ork-graphite hover:border-ork-blue/40 hover:text-ork-blue";

function Panel({ initial, onClose }: { initial: AskContext; onClose: () => void }) {
  const info = useQuery({ queryKey: ["concertino-info"], queryFn: () => api<Info>("/api/concertino/info"), staleTime: 300_000 });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState(initial.question ?? "");
  const [trato, setTrato] = useState<"auto" | "tu" | "usted">("auto");
  const end = useRef<HTMLDivElement>(null);
  const ask = useMutation({
    mutationFn: (v: { question: string; mode?: string }) =>
      api<Advice>("/api/concertino/ask", {
        method: "POST",
        json: { question: v.question, mode: v.mode, trato, contact_id: initial.contact_id, deal_id: initial.deal_id, recipient_id: initial.recipient_id, campaign_id: initial.campaign_id },
      }),
  });
  const send = (question: string, mode?: string, label?: string) => {
    const id = ++counter;
    setTurns((t) => [...t, { id, question: label ?? question }]);
    setText("");
    ask.mutate(
      { question, mode },
      {
        onSuccess: (advice) => setTurns((t) => t.map((x) => (x.id === id ? { ...x, advice } : x))),
        onError: (e) => setTurns((t) => t.map((x) => (x.id === id ? { ...x, error: e.message } : x))),
      },
    );
  };
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !initial.autoAsk) return;
    started.current = true;
    send(initial.question ?? "", initial.mode, initial.prompt || initial.question || initial.label);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const quick = QUICK.filter((q) => !q.needs || initial[q.needs]);
  return (
    <div className="fixed inset-0 z-[60] print:hidden" role="dialog" aria-modal="true" aria-label="Concertino">
      <div className="absolute inset-0 bg-ork-sidebar/30" onClick={onClose} aria-hidden />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-[480px] flex-col bg-ork-surface shadow-[var(--shadow-pop)]">
        <header className="flex items-start justify-between gap-3 border-b border-ork-line bg-ork-card px-5 py-4">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 grid size-9 place-items-center rounded-xl bg-ork-blue-solid text-white">
              <Music2 className="size-4" aria-hidden />
            </span>
            <div>
              <h2 className="text-[16px] font-bold text-ork-ink">Concertino</h2>
              <p className="text-[12.5px] text-ork-mist">Tu asesor de ventas. Pregúntale lo que necesites.</p>
              {initial.label && <p className="mt-1 text-[12.5px] font-semibold text-ork-graphite">Sobre: {initial.label}</p>}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <select
              aria-label="Trato"
              value={trato}
              onChange={(e) => setTrato(e.target.value as typeof trato)}
              className="rounded-lg border border-ork-line bg-ork-card px-2 py-1 text-[12.5px] text-ork-graphite"
            >
              <option value="auto">Trato automático</option>
              <option value="tu">Tú</option>
              <option value="usted">Usted</option>
            </select>
            <button type="button" className="ork-focus rounded-lg p-1.5 text-ork-mist hover:bg-ork-ink/5" aria-label="Cerrar Concertino" onClick={onClose}>
              <X className="size-5" />
            </button>
          </div>
        </header>
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          {turns.length === 0 && (
            <div className="space-y-4">
              <p className="text-[13.5px] text-ork-graphite">
                Cuéntame el caso: una respuesta que recibiste, una objeción, un perfil, una cuenta, una campaña, una propuesta o una reunión. Te respondo con tu ficha del producto y lo que le ha funcionado a tu empresa.
              </p>
              <div className="flex flex-wrap gap-2">
                {quick
                  .filter((q) => !info.data || info.data.plan.full || info.data.plan.topics.includes(q.mode ?? ""))
                  .map((q) =>
                    q.mode === "objecion" ? (
                      <button key={q.label} type="button" className={CHIP} onClick={() => setText("Me dijo que ")}>
                        {q.label}
                      </button>
                    ) : (
                      <button key={q.label} type="button" className={CHIP} onClick={() => send(q.question ?? "", q.mode, q.label)}>
                        {q.label}
                      </button>
                    ),
                  )}
              </div>
              {initial.recipient_id && (
                <Button size="sm" onClick={() => send("", "respuesta", "Asísteme con esta respuesta")}>
                  Asísteme con esta respuesta
                </Button>
              )}
            </div>
          )}
          {turns.map((t) => (
            <div key={t.id} className="space-y-3">
              {t.question && <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-ork-blue-solid px-3.5 py-2 text-[13.5px] text-white">{t.question}</p>}
              {t.error ? <Alert>{t.error}</Alert> : t.advice ? <AdviceCard advice={t.advice} objections={info.data?.objections ?? []} /> : <Thinking />}
            </div>
          ))}
          <div ref={end} />
        </div>
        <form
          className="border-t border-ork-line bg-ork-card px-4 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) send(text.trim());
          }}
        >
          <div className="flex items-end gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (text.trim()) send(text.trim());
                }
              }}
              rows={2}
              maxLength={4000}
              placeholder="Ej.: «Me dijo que ya tienen proveedor»"
              aria-label="Tu pregunta para Concertino"
              className="ork-focus min-h-[44px] flex-1 resize-none rounded-xl border border-ork-line bg-ork-surface px-3 py-2 text-[13.5px] text-ork-ink placeholder:text-ork-mist"
            />
            <Button type="submit" loading={ask.isPending} disabled={!text.trim()} aria-label="Enviar pregunta" icon={<Send className="size-4" aria-hidden />}>
              Enviar
            </Button>
          </div>
        </form>
      </aside>
    </div>
  );
}

function Thinking() {
  return (
    <div className="flex items-center gap-2 text-[13px] text-ork-mist" role="status">
      <Music2 className="size-4 animate-pulse" aria-hidden /> Concertino está pensando…
    </div>
  );
}

function AdviceCard({ advice, objections }: { advice: Advice; objections: { code: string; label: string }[] }) {
  const [fixed, setFixed] = useState(false);
  const fix = useMutation({
    mutationFn: (objection: string) => api(`/api/concertino/cases/${advice.case_id}`, { method: "POST", json: { objection } }),
    onSuccess: () => setFixed(true),
  });
  return (
    <div className="space-y-3 rounded-2xl border border-ork-line bg-ork-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="blue">{advice.topic_label}</Badge>
        {advice.objection_label && <Badge tone="warn">{advice.objection_label}</Badge>}
      </div>
      <p className="text-[14px] font-semibold text-ork-ink">{advice.summary}</p>
      {advice.objection && advice.case_id && (
        <label className="flex items-center gap-2 text-[12px] text-ork-mist">
          {fixed ? "Gracias, aprendo de la corrección." : "¿No es esta objeción?"}
          {!fixed && (
            <select className="rounded-md border border-ork-line bg-ork-card px-1.5 py-0.5 text-[12px]" defaultValue="" onChange={(e) => e.target.value && fix.mutate(e.target.value)} aria-label="Corregir el tipo de objeción">
              <option value="">Elegir la correcta…</option>
              {objections
                .filter((o) => o.code !== advice.objection)
                .map((o) => (
                  <option key={o.code} value={o.code}>
                    {o.label}
                  </option>
                ))}
            </select>
          )}
        </label>
      )}
      {advice.options.map((o) => (
        <OptionBox key={o.code} option={o} caseId={advice.case_id} />
      ))}
      {advice.sections.map((s) => (
        <div key={s.title}>
          <p className="text-[12px] font-bold uppercase tracking-wide text-ork-mist">{s.title}</p>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-[13.5px] text-ork-graphite">
            {s.items.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </div>
      ))}
      {advice.tips.length > 0 && (
        <div className="rounded-xl bg-ork-surface px-3 py-2.5 text-[13px] text-ork-graphite">
          {advice.tips.map((t) => (
            <p key={t}>{t}</p>
          ))}
        </div>
      )}
      {advice.next_step && (
        <p className="text-[13px] text-ork-graphite">
          <strong>Siguiente paso:</strong> {advice.next_step}
        </p>
      )}
      {advice.missing.length > 0 && (
        <div className="rounded-xl border border-warn/30 bg-warn/5 px-3 py-2 text-[12.5px] text-ork-graphite">
          {advice.missing.map((m) => (
            <p key={m}>{m}</p>
          ))}
          <Link to="/producto" className="font-semibold text-ork-blue hover:underline">
            Abrir la ficha del producto
          </Link>
        </div>
      )}
      {advice.sources.length > 0 && <p className="text-[11.5px] text-ork-mist">Basado en: {advice.sources.join(" · ")}</p>}
    </div>
  );
}

function OptionBox({ option, caseId }: { option: Option; caseId: string | null }) {
  const [text, setText] = useState(option.text);
  const [state, setState] = useState<"idle" | "used" | "discarded" | "closed">("idle");
  const record = useMutation({
    mutationFn: (json: Record<string, unknown>) => api(`/api/concertino/cases/${caseId}`, { method: "POST", json }),
  });
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* el navegador no dejó copiar: el texto sigue a la vista */
    }
    setState("used");
    if (caseId) record.mutate({ action: text === option.text ? "usada" : "editada", chosen: option.code, final_text: text });
  };
  return (
    <div className={cx("rounded-xl border p-3", state === "discarded" ? "border-ork-line opacity-60" : "border-ork-blue/25 bg-ork-blue/[0.03]")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13.5px] font-bold text-ork-ink">{option.name}</p>
        {option.record && <Badge tone="ok">{option.record}</Badge>}
      </div>
      <p className="mt-0.5 text-[12.5px] text-ork-mist">{option.why}</p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={Math.min(12, text.split("\n").length + 2)}
        aria-label={`Texto sugerido: ${option.name}`}
        className="ork-focus mt-2 w-full resize-y rounded-lg border border-ork-line bg-ork-card px-3 py-2 text-[13px] leading-relaxed text-ork-ink"
      />
      {state === "idle" && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" icon={<Copy className="size-3.5" aria-hidden />} onClick={copy}>
            Copiar y usar
          </Button>
          <Button
            size="sm"
            variant="secondary"
            icon={<ThumbsDown className="size-3.5" aria-hidden />}
            onClick={() => {
              setState("discarded");
              if (caseId) record.mutate({ action: "descartada", chosen: option.code });
            }}
          >
            No me sirve
          </Button>
        </div>
      )}
      {state === "used" && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-ork-graphite">
          <Check className="size-4 text-ok" aria-hidden /> Copiado. Cuando sepas, cuéntame cómo terminó:
          {(
            [
              ["reunion", "Reunión"],
              ["interesado", "Interés"],
              ["sigue", "Sigue en curso"],
              ["no_avanzo", "No avanzó"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={CHIP}
              onClick={() => {
                setState("closed");
                if (caseId) record.mutate({ outcome: value });
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {state === "closed" && <p className="mt-2 text-[12.5px] text-ork-mist">Gracias: así aprendo qué le funciona a tu empresa.</p>}
    </div>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileUp, Plus, Sparkles, Trash2, X } from "lucide-react";
import { useState } from "react";
import { AskConcertino } from "../components/Concertino";
import { Alert, Badge, Button, Card, Modal, PageHeader, SelectField, Spinner, TextField, cx } from "../components/ui";
import { api } from "../lib/api";

type Fact = {
  id: string;
  kind: string;
  kind_label: string;
  title: string;
  detail: string;
  answer: string;
  status: "confirmado" | "aprendido" | "descartado";
  source: string;
  evidence: number;
  meta: Record<string, string>;
};
type Sheet = {
  items: Fact[];
  kinds: { kind: string; label: string }[];
  objection_types: { code: string; label: string }[];
  interview: { kind: string; question: string; example: string }[];
  complete: number;
  total: number;
  learned: { added: number; updated: number } | null;
  can_curate: boolean;
};

const SOURCES: Record<string, string> = { manual: "Escrito por el equipo", campanas: "Aprendido de tus campañas", documento: "Leído de un documento" };
const ORDER = ["producto", "publico", "problema", "valor", "prueba", "diferencial", "precio", "competidor", "objecion", "pregunta"];

/** La ficha viva del producto: lo que Concertino sabe y usa para asesorar. */
export function ProductPage() {
  const client = useQueryClient();
  const sheet = useQuery({ queryKey: ["concertino-facts"], queryFn: () => api<Sheet>("/api/concertino/facts") });
  const learn = useMutation({
    mutationFn: () => api<Sheet>("/api/concertino/facts/learn", { method: "POST" }),
    onSuccess: (data) => client.setQueryData(["concertino-facts"], data),
  });
  const [adding, setAdding] = useState<{ kind: string; title?: string } | null>(null);
  const [importing, setImporting] = useState(false);
  const data = learn.data ?? sheet.data;
  if (sheet.isLoading) return <Spinner label="Abriendo la ficha" />;
  if (sheet.error || !data) return <Alert>{sheet.error?.message ?? "No pude abrir la ficha."}</Alert>;
  const pending = data.items.filter((f) => f.status === "aprendido");
  const confirmed = data.items.filter((f) => f.status === "confirmado");
  const byKind = ORDER.map((k) => ({ kind: k, label: data.kinds.find((x) => x.kind === k)?.label ?? k, items: confirmed.filter((f) => f.kind === k) }));
  return (
    <div className="space-y-6">
      <PageHeader
        title="Ficha del producto"
        description="Lo que Concertino sabe de lo que vendes. Solo afirma lo que está confirmado aquí; lo que aprende de las campañas espera tu revisión."
        action={
          <div className="flex flex-wrap gap-2">
            <AskConcertino mode="producto" question="¿Cómo presento nuestro producto?" autoAsk label="Tu producto">
              Probar con Concertino
            </AskConcertino>
            <Button variant="secondary" size="sm" icon={<FileUp className="size-3.5" aria-hidden />} onClick={() => setImporting(true)}>
              Leer de un documento
            </Button>
            <Button size="sm" icon={<Sparkles className="size-3.5" aria-hidden />} loading={learn.isPending} onClick={() => learn.mutate()}>
              Aprender de mis campañas
            </Button>
          </div>
        }
      />
      {learn.data?.learned && (
        <Alert tone="ok">
          {learn.data.learned.added || learn.data.learned.updated
            ? `Revisé las respuestas de tus campañas: ${learn.data.learned.added} dato${learn.data.learned.added === 1 ? "" : "s"} nuevo${learn.data.learned.added === 1 ? "" : "s"} para revisar.`
            : "Revisé las respuestas de tus campañas: no hay objeciones ni preguntas nuevas que se repitan."}
        </Alert>
      )}
      {data.interview.length > 0 && (
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-[15px] font-bold text-ork-ink">Entrevista inicial</h2>
              <p className="text-[13px] text-ork-mist">
                {data.complete} de {data.total} respuestas. Con estas respuestas Concertino ya puede asesorar con tu producto.
              </p>
            </div>
            <div className="h-2 w-40 overflow-hidden rounded-full bg-ork-ink/10" aria-hidden>
              <div className="h-full bg-ork-blue" style={{ width: `${(100 * data.complete) / data.total}%` }} />
            </div>
          </div>
          <ul className="mt-4 space-y-2">
            {data.interview.map((q) => (
              <li key={q.kind} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-ork-line px-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-[13.5px] font-semibold text-ork-ink">{q.question}</p>
                  <p className="text-[12.5px] text-ork-mist">Ej.: {q.example}</p>
                </div>
                <Button size="sm" variant="secondary" onClick={() => setAdding({ kind: q.kind })}>
                  Responder
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {pending.length > 0 && (
        <Card className="p-5">
          <h2 className="text-[15px] font-bold text-ork-ink">Por revisar ({pending.length})</h2>
          <p className="text-[13px] text-ork-mist">
            {data.can_curate ? "Concertino no los usa hasta que los confirmes." : "La administración o la jefatura los revisan antes de que Concertino los use."}
          </p>
          <ul className="mt-3 space-y-2">
            {pending.map((f) => (
              <FactRow key={f.id} fact={f} canCurate={data.can_curate} types={data.objection_types} />
            ))}
          </ul>
        </Card>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {byKind.map((group) => (
          <Card key={group.kind} className="p-5">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-[14.5px] font-bold text-ork-ink">{group.label}</h2>
              <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => setAdding({ kind: group.kind })}>
                Agregar
              </Button>
            </div>
            {group.items.length === 0 ? (
              <p className="mt-2 text-[13px] text-ork-mist">Aún no hay datos.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {group.items.map((f) => (
                  <FactRow key={f.id} fact={f} canCurate={data.can_curate} types={data.objection_types} />
                ))}
              </ul>
            )}
          </Card>
        ))}
      </div>
      {adding && <FactModal kind={adding.kind} kinds={data.kinds} types={data.objection_types} onClose={() => setAdding(null)} />}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
    </div>
  );
}

function FactRow({ fact, canCurate, types }: { fact: Fact; canCurate: boolean; types: { code: string; label: string }[] }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const save = useMutation({
    mutationFn: (json: Record<string, unknown>) => api(`/api/concertino/facts/${fact.id}`, { method: "PATCH", json }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["concertino-facts"] }),
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/concertino/facts/${fact.id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["concertino-facts"] }),
  });
  const typeLabel = types.find((t) => t.code === fact.meta?.tipo)?.label;
  return (
    <li className="rounded-xl border border-ork-line px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            {fact.status === "aprendido" && <Badge tone="violet">{fact.kind_label}</Badge>}
            {typeLabel && fact.kind === "objecion" && fact.title !== typeLabel && <Badge tone="warn">{typeLabel}</Badge>}
            {fact.evidence > 1 && <Badge>{fact.evidence} veces</Badge>}
          </div>
          <p className="mt-0.5 text-[13.5px] font-semibold text-ork-ink">{fact.title}</p>
          {fact.detail && <p className="text-[12.5px] text-ork-graphite">{fact.detail}</p>}
          {fact.answer && (
            <p className="mt-1 text-[12.5px] text-ork-graphite">
              <strong>Mejor respuesta:</strong> {fact.answer}
            </p>
          )}
          {(fact.kind === "objecion" || fact.kind === "pregunta") && !fact.answer && fact.status === "confirmado" && (
            <p className="mt-1 text-[12px] text-ork-mist">Sin respuesta definida: Concertino usa el oficio del vendedor senior.</p>
          )}
          <p className="mt-1 text-[11.5px] text-ork-mist">{SOURCES[fact.source] ?? fact.source}</p>
        </div>
        {canCurate && (
          <div className="flex shrink-0 gap-1">
            {fact.status === "aprendido" && (
              <Button size="sm" icon={<Check className="size-3.5" aria-hidden />} onClick={() => save.mutate({ status: "confirmado" })} loading={save.isPending}>
                Confirmar
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Editar
            </Button>
            {fact.status === "aprendido" ? (
              <Button size="sm" variant="ghost" aria-label="Descartar" icon={<X className="size-3.5" aria-hidden />} onClick={() => save.mutate({ status: "descartado" })} />
            ) : (
              <Button size="sm" variant="ghost" aria-label="Borrar" icon={<Trash2 className="size-3.5" aria-hidden />} onClick={() => remove.mutate()} />
            )}
          </div>
        )}
      </div>
      {editing && <FactModal fact={fact} kind={fact.kind} kinds={[]} types={types} onClose={() => setEditing(false)} />}
    </li>
  );
}

const HINTS: Record<string, string> = {
  producto: "Qué vendes, en una frase que entendería un cliente.",
  publico: "Sector, tamaño de empresa y cargo que decide.",
  problema: "El problema que resuelves, en palabras del cliente.",
  valor: "El resultado concreto que logra el cliente.",
  prueba: "Un caso de éxito con un número (sin datos confidenciales).",
  diferencial: "Lo que solo ustedes hacen.",
  precio: "Referencia de precio o rango de inversión.",
  competidor: "Nombre del competidor; en «Detalle», en qué se diferencian.",
  objecion: "Lo que dicen los clientes; en «Mejor respuesta», cómo la responde tu equipo.",
  pregunta: "La pregunta; en «Mejor respuesta», la respuesta correcta.",
};

function FactModal({ fact, kind, kinds, types, onClose }: { fact?: Fact; kind: string; kinds: { kind: string; label: string }[]; types: { code: string; label: string }[]; onClose: () => void }) {
  const client = useQueryClient();
  const [k, setK] = useState(kind);
  const [title, setTitle] = useState(fact?.title ?? "");
  const [detail, setDetail] = useState(fact?.detail ?? "");
  const [answer, setAnswer] = useState(fact?.answer ?? "");
  const [tipo, setTipo] = useState(fact?.meta?.tipo ?? "");
  const save = useMutation({
    mutationFn: () => {
      const json = { kind: k, title, detail, answer, meta: tipo ? { tipo } : undefined };
      return fact ? api(`/api/concertino/facts/${fact.id}`, { method: "PATCH", json }) : api("/api/concertino/facts", { method: "POST", json });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["concertino-facts"] });
      onClose();
    },
  });
  const withAnswer = k === "objecion" || k === "pregunta";
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={fact ? "Editar dato" : "Agregar a la ficha"} description={HINTS[k]}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        {save.error && <Alert>{save.error.message}</Alert>}
        {!fact && kinds.length > 0 && (
          <SelectField label="Tipo de dato" value={k} onChange={(e) => setK(e.target.value)}>
            {kinds.map((x) => (
              <option key={x.kind} value={x.kind}>
                {x.label}
              </option>
            ))}
          </SelectField>
        )}
        <TextField label={k === "competidor" ? "Competidor" : withAnswer ? (k === "objecion" ? "Lo que dice el cliente" : "Pregunta") : "Dato"} required maxLength={300} value={title} onChange={(e) => setTitle(e.target.value)} />
        {k === "objecion" && (
          <SelectField label="Tipo de objeción" value={tipo} onChange={(e) => setTipo(e.target.value)}>
            <option value="">Que Concertino lo detecte</option>
            {types.map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </SelectField>
        )}
        <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-ork-graphite">
          {withAnswer ? "Mejor respuesta" : "Detalle (opcional)"}
          <textarea
            className={cx("ork-focus min-h-[90px] rounded-xl border border-ork-line bg-ork-card px-3 py-2 text-[13.5px] font-normal text-ork-ink")}
            value={withAnswer ? answer : detail}
            onChange={(e) => (withAnswer ? setAnswer(e.target.value) : setDetail(e.target.value))}
            maxLength={4000}
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" loading={save.isPending} disabled={!title.trim()}>
            Guardar
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const [text, setText] = useState("");
  const read = useMutation({
    mutationFn: () => api<{ added: number }>("/api/concertino/facts/import", { method: "POST", json: { text } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["concertino-facts"] }),
  });
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title="Leer de un documento" description="Pega el texto de una propuesta, presentación o página de tu producto. Concertino propone datos para la ficha y tú decides cuáles confirmar." wide>
      {read.data ? (
        <div className="flex flex-col gap-4">
          <Alert tone="ok">
            {read.data.added
              ? `Encontré ${read.data.added} dato${read.data.added === 1 ? "" : "s"}. Están en «Por revisar».`
              : "No encontré precios, resultados, diferenciales ni preguntas en ese texto."}
          </Alert>
          <Button variant="secondary" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {read.error && <Alert>{read.error.message}</Alert>}
          <textarea
            className="ork-focus min-h-[240px] rounded-xl border border-ork-line bg-ork-card px-3 py-2 text-[13.5px] text-ork-ink"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={60000}
            aria-label="Texto del documento"
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancelar
            </Button>
            <Button loading={read.isPending} disabled={text.trim().length < 20} onClick={() => read.mutate()}>
              Leer
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

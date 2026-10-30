import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MailCheck, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Modal } from "../components/ui";
import { api } from "../lib/api";

export type EmailStatus = "sin_validar" | "estimado" | "valido" | "invalido" | "catch_all" | "riesgoso" | "desconocido";

export const EMAIL_STATUS: Record<EmailStatus, { label: string; tone: "neutral" | "blue" | "violet" | "ok" | "warn" | "bad"; help: string }> = {
  valido: { label: "Válido", tone: "ok", help: "El servidor de correo confirmó que el buzón existe." },
  invalido: { label: "Inválido", tone: "bad", help: "El buzón o el dominio no existen. No le escribas." },
  catch_all: { label: "Acepta todo", tone: "warn", help: "El dominio acepta cualquier dirección: el buzón no se puede confirmar." },
  riesgoso: { label: "Riesgoso", tone: "warn", help: "Dominio desechable o temporal." },
  desconocido: { label: "Sin confirmar", tone: "neutral", help: "El dominio recibe correo, pero el servidor no confirmó el buzón." },
  estimado: { label: "Estimado", tone: "violet", help: "Deducido del formato de la empresa. Valídalo antes de escribir." },
  sin_validar: { label: "Sin validar", tone: "neutral", help: "Todavía no se ha validado." },
};

export const EMAIL_FILTERS = [
  { value: "valido", label: "Válidos" },
  { value: "invalido", label: "Inválidos" },
  { value: "catch_all", label: "Acepta todo" },
  { value: "riesgoso", label: "Riesgosos" },
  { value: "desconocido", label: "Sin confirmar" },
  { value: "estimado", label: "Estimados" },
  { value: "sin_validar", label: "Sin validar" },
  { value: "sin_correo", label: "Sin correo" },
];

export function EmailBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const meta = EMAIL_STATUS[status as EmailStatus];
  if (!meta) return null;
  return (
    <span title={meta.help}>
      <Badge tone={meta.tone}>{meta.label}</Badge>
    </span>
  );
}

type Run = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled";
  summary: string;
  find_missing: boolean;
  progress: { done?: number; total?: number; log?: { at: string; text: string }[] };
  totals: Record<string, number>;
  error: string | null;
  created_at: string;
};
const ACTIVE = new Set(["queued", "running"]);

export function ValidateModal({
  open,
  onOpenChange,
  ids,
  filters,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  ids: string[];
  filters: Record<string, string>;
  onStarted: () => void;
}) {
  const client = useQueryClient();
  const [findMissing, setFindMissing] = useState(true);
  const start = useMutation({
    mutationFn: () =>
      api<Run>("/api/validation/runs", {
        method: "POST",
        json: { ids, filters: ids.length ? {} : Object.fromEntries(Object.entries(filters).filter(([, v]) => v)), find_missing: findMissing },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["validation-runs"] });
      onStarted();
      onOpenChange(false);
    },
  });
  const scope = ids.length ? `${ids.length} contactos seleccionados` : Object.values(filters).some(Boolean) ? "los contactos del filtro actual" : "toda tu base";
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Validar correos" description={`Se revisarán ${scope}.`}>
      <div className="flex flex-col gap-4">
        <ol className="space-y-2 text-[13.5px] text-ork-graphite">
          <li>1. Formato del correo y si el dominio existe y recibe correo.</li>
          <li>2. Dominios desechables y servidores que aceptan cualquier dirección.</li>
          <li>3. Pregunta al servidor si el buzón existe, sin enviar ningún correo.</li>
        </ol>
        <label className="flex items-start gap-2 rounded-xl border border-ork-line p-3 text-[13.5px] text-ork-graphite">
          <input type="checkbox" className="mt-0.5 size-4 accent-ork-blue" checked={findMissing} onChange={(e) => setFindMissing(e.target.checked)} />
          <span>
            <strong className="text-ork-ink">Buscar correos faltantes.</strong> Para contactos sin correo cuya empresa tiene dominio, prueba los formatos habituales y guarda solo el que el servidor confirme.
          </span>
        </label>
        {start.error && <Alert>{start.error.message}</Alert>}
        <Button loading={start.isPending} icon={<MailCheck className="size-4" aria-hidden />} onClick={() => start.mutate()}>
          Validar
        </Button>
      </div>
    </Modal>
  );
}

export function ValidationBanner() {
  const client = useQueryClient();
  const runs = useQuery({
    queryKey: ["validation-runs"],
    queryFn: () => api<Run[]>("/api/validation/runs"),
    refetchInterval: (q) => (q.state.data?.some((r) => ACTIVE.has(r.status)) ? 2000 : false),
  });
  const latest = runs.data?.[0];
  const [dismissed, setDismissed] = useState<string | null>(null);
  const status = latest?.status;
  const done = latest?.progress.done;
  useEffect(() => {
    // Refresca la tabla mientras avanza y al terminar.
    if (status) client.invalidateQueries({ queryKey: ["contacts"] });
  }, [status, done, client]);
  const cancel = useMutation({
    mutationFn: (id: string) => api(`/api/validation/runs/${id}/cancel`, { method: "POST" }),
    onSuccess: () => runs.refetch(),
  });
  if (!latest || dismissed === latest.id) return null;
  const active = ACTIVE.has(latest.status);
  const recent = Date.now() - new Date(latest.created_at).getTime() < 30 * 60 * 1000;
  if (!active && !recent) return null;
  const total = latest.progress.total ?? 0;
  const pct = latest.status === "completed" ? 100 : total ? Math.max(5, Math.round(((latest.progress.done ?? 0) / total) * 100)) : 3;
  const t = latest.totals;
  const lastLog = latest.progress.log?.at(-1)?.text;
  return (
    <div className="mx-4 mb-3 rounded-xl border border-ork-blue/20 bg-ork-blue/5 px-4 py-3" role="status" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-[13.5px] font-semibold text-ork-ink">
          <MailCheck className="size-4 text-ork-blue" aria-hidden />
          {active ? "Validando correos…" : latest.status === "completed" ? "Validación completada" : latest.status === "failed" ? "La validación falló" : "Validación detenida"}
          <span className="font-normal text-ork-mist">· {latest.summary}</span>
        </p>
        {active ? (
          <Button size="sm" variant="secondary" icon={<Square className="size-3.5" aria-hidden />} loading={cancel.isPending} onClick={() => cancel.mutate(latest.id)}>
            Detener
          </Button>
        ) : (
          <button className="text-[12.5px] font-semibold text-ork-mist hover:text-ork-ink" onClick={() => setDismissed(latest.id)}>
            Cerrar
          </button>
        )}
      </div>
      {active && (
        <>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
            <div className="ork-gradient h-full rounded-full transition-all duration-700" style={{ width: `${pct}%` }} />
          </div>
          {lastLog && <p className="mt-1.5 text-[12.5px] text-ork-mist">{lastLog}</p>}
        </>
      )}
      {latest.status === "completed" && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ork-graphite">
          <span><strong className="text-ok">{t.valido ?? 0}</strong> válidos</span>
          <span><strong className="text-bad">{t.invalido ?? 0}</strong> inválidos</span>
          <span><strong>{t.catch_all ?? 0}</strong> aceptan todo</span>
          <span><strong>{(t.desconocido ?? 0) + (t.riesgoso ?? 0)}</strong> sin confirmar o riesgosos</span>
          {latest.find_missing && <span><strong className="text-ork-blue">{t.found ?? 0}</strong> correos encontrados</span>}
        </div>
      )}
      {t.smtp_blocked ? (
        <p className="mt-2 text-[12.5px] text-warn">
          El servidor donde corre ORKESTA no tiene permitido conectarse al puerto 25, así que los buzones quedaron «Sin confirmar». La revisión de formato, dominio y desechables sí se hizo.
        </p>
      ) : null}
      {latest.status === "failed" && latest.error && <p className="mt-1 text-[12.5px] text-bad">{latest.error}</p>}
    </div>
  );
}

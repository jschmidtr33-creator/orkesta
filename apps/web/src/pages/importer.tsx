import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileSpreadsheet, Sparkles, UploadCloud, XCircle } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert, Badge, Button, Card, CardHeader, cx, PageHeader, SelectField, Spinner, TextField } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { useLists, useMeta } from "./contacts";
import { formatWhen } from "./dashboard";
import { useStartEnrichment } from "./validation";

type SheetPreview = {
  name: string;
  headers: string[];
  rows: number;
  suggested: Record<string, number>;
  usable: boolean;
  sample: string[][];
};
type ImportRecord = {
  id: string;
  filename: string;
  status: "preview" | "queued" | "running" | "completed" | "failed" | "canceled";
  sheets: SheetPreview[] | null;
  mapping: Record<string, Record<string, number>>;
  totals: Record<string, number>;
  error: string | null;
  created_at: string;
};

const REQUIRED_HINT = "Asigna al menos Nombre (o Nombre completo) y Empresa (o Correo).";

async function uploadFile(file: File): Promise<ImportRecord> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/imports", { method: "POST", body, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : "No pudimos leer el archivo.", response.status);
  return data as ImportRecord;
}

export function ImportPage() {
  const client = useQueryClient();
  const meta = useMeta();
  const lists = useLists();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [preview, setPreview] = useState<ImportRecord | null>(null);
  const [mapping, setMapping] = useState<Record<string, Record<string, number>>>({});
  const [listId, setListId] = useState("");
  const [newList, setNewList] = useState("");
  const [runningId, setRunningId] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: uploadFile,
    onSuccess: (data) => {
      setPreview(data);
      setMapping(data.mapping);
    },
  });
  const confirm = useMutation({
    mutationFn: () =>
      api<ImportRecord>(`/api/imports/${preview!.id}/confirm`, {
        method: "POST",
        json: { mapping, list_id: listId || null, new_list: listId ? "" : newList },
      }),
    onSuccess: (data) => {
      setRunningId(data.id);
      setPreview(null);
    },
  });
  const status = useQuery({
    queryKey: ["import", runningId],
    queryFn: () => api<ImportRecord>(`/api/imports/${runningId}`),
    enabled: Boolean(runningId),
    refetchInterval: (q) => (q.state.data && ["completed", "failed"].includes(q.state.data.status) ? false : 1500),
  });
  const history = useQuery({ queryKey: ["imports"], queryFn: () => api<ImportRecord[]>("/api/imports") });
  const finished = status.data && ["completed", "failed"].includes(status.data.status);
  const completed = status.data?.status === "completed";
  useEffect(() => {
    if (!completed) return;
    void client.invalidateQueries({ queryKey: ["contacts"] });
    void client.invalidateQueries({ queryKey: ["lists"] });
    void client.invalidateQueries({ queryKey: ["imports"] });
    void client.invalidateQueries({ queryKey: ["dashboard"] });
  }, [completed, client]);

  const pick = (file?: File | null) => {
    if (file) upload.mutate(file);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    pick(e.dataTransfer.files?.[0]);
  };
  const fieldLabel = (value: string) => meta.data?.mappable.find((m) => m.value === value)?.label ?? value;

  const setColumn = (sheet: string, column: number, field: string) => {
    const current = { ...(mapping[sheet] ?? {}) };
    for (const [k, v] of Object.entries(current)) if (v === column || k === field) delete current[k];
    if (field) current[field] = column;
    setMapping({ ...mapping, [sheet]: current });
  };
  const usable = (cols: Record<string, number> = {}) =>
    ("first" in cols || "full" in cols) && ("company" in cols || "email" in cols);
  const anyUsable = Object.values(mapping).some(usable);

  return (
    <>
      <PageHeader
        title="Importar contactos"
        description="Excel (todas sus hojas) o CSV. Solo se guardan nombre, apellido, ubicación, teléfono, correo, cargo, empresa e industria; el resto de columnas se descarta."
        action={
          <Link to="/contactos" className="ork-focus inline-flex h-10 items-center rounded-xl px-3 text-sm font-semibold text-ork-graphite hover:bg-ork-ink/5">
            Volver a contactos
          </Link>
        }
      />

      {!preview && !runningId && (
        <Card>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            className={cx(
              "flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-6 py-14 text-center transition",
              drag ? "border-ork-blue bg-ork-blue/5" : "border-ork-line",
            )}
          >
            <span className="ork-gradient flex size-14 items-center justify-center rounded-2xl text-white">
              <UploadCloud className="size-7" aria-hidden />
            </span>
            <p className="text-[16px] font-bold text-ork-ink">Arrastra tu archivo aquí</p>
            <p className="text-[13.5px] text-ork-mist">.xlsx o .csv de hasta {meta.data?.max_mb ?? 30} MB</p>
            <input ref={input} type="file" accept=".xlsx,.csv,.txt" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
            <Button loading={upload.isPending} onClick={() => input.current?.click()}>
              Elegir archivo
            </Button>
            {upload.error && <Alert>{upload.error.message}</Alert>}
          </div>
        </Card>
      )}

      {preview?.sheets && (
        <div className="flex flex-col gap-5">
          <Alert tone="blue">
            Revisa qué columna corresponde a cada campo. ORKESTA ya propuso una asignación; las columnas en «No importar» se descartan.
          </Alert>
          {preview.sheets.map((sheet) => {
            const cols = mapping[sheet.name] ?? {};
            const byColumn = Object.fromEntries(Object.entries(cols).map(([f, i]) => [i, f]));
            return (
              <Card key={sheet.name}>
                <CardHeader
                  title={`Hoja «${sheet.name}»`}
                  description={`${sheet.rows.toLocaleString("es")} filas`}
                  action={usable(cols) ? <Badge tone="ok">Lista para importar</Badge> : <Badge tone="warn">No se importará</Badge>}
                />
                {!usable(cols) && <p className="mb-3 text-[13px] text-ork-mist">{REQUIRED_HINT}</p>}
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-left text-[13px]">
                    <thead>
                      <tr className="border-b border-ork-line text-[12px] text-ork-mist">
                        <th className="py-2 pr-3 font-semibold">Columna del archivo</th>
                        <th className="py-2 pr-3 font-semibold">Guardar como</th>
                        <th className="py-2 font-semibold">Ejemplos</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sheet.headers.map((header, index) => (
                        <tr key={index} className="border-b border-ork-line last:border-0">
                          <td className="py-2.5 pr-3 font-semibold text-ork-ink">{header || `Columna ${index + 1}`}</td>
                          <td className="py-2.5 pr-3">
                            <select
                              aria-label={`Campo para la columna ${header}`}
                              value={byColumn[index] ?? ""}
                              onChange={(e) => setColumn(sheet.name, index, e.target.value)}
                              className={cx(
                                "ork-focus h-9 w-full max-w-[240px] rounded-lg border px-2 text-[13px]",
                                byColumn[index] ? "border-ork-blue/40 bg-ork-blue/5 text-ork-blue" : "border-ork-line text-ork-mist",
                              )}
                            >
                              <option value="">No importar</option>
                              {meta.data?.mappable.map((m) => (
                                <option key={m.value} value={m.value}>
                                  {m.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="max-w-[320px] truncate py-2.5 text-ork-mist">
                            {sheet.sample.map((row) => row[index]).filter(Boolean).slice(0, 3).join(" · ") || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-[12px] text-ork-mist">
                  Campos asignados: {Object.keys(cols).map(fieldLabel).join(", ") || "ninguno"}
                </p>
              </Card>
            );
          })}
          <Card>
            <CardHeader title="Agregar a una lista (opcional)" description="Útil para campañas: por ejemplo, «Asistentes Congreso 2027»." />
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectField label="Lista existente" value={listId} onChange={(e) => setListId(e.target.value)}>
                <option value="">Ninguna o una nueva</option>
                {lists.data?.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </SelectField>
              {!listId && <TextField label="Nombre de una lista nueva" value={newList} onChange={(e) => setNewList(e.target.value)} placeholder="Opcional" />}
            </div>
          </Card>
          {confirm.error && <Alert>{confirm.error.message}</Alert>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="ghost"
              onClick={() => {
                void api(`/api/imports/${preview.id}`, { method: "DELETE" });
                setPreview(null);
              }}
            >
              Cancelar
            </Button>
            <Button size="lg" disabled={!anyUsable} loading={confirm.isPending} onClick={() => confirm.mutate()}>
              Importar contactos
            </Button>
          </div>
        </div>
      )}

      {runningId && (
        <Card>
          {!status.data || !finished ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <Spinner label="Importando" />
              <p className="text-[14px] text-ork-graphite">
                {status.data?.totals.filas
                  ? `${(status.data.totals.nuevos ?? 0) + (status.data.totals.actualizados ?? 0)} de ${(
                      (status.data.totals.nuevos ?? 0) + (status.data.totals.actualizados ?? 0) + (status.data.totals.por_procesar ?? 0)
                    ).toLocaleString("es")} contactos procesados`
                  : "Leyendo el archivo…"}
              </p>
              <p className="text-[12.5px] text-ork-mist">Puedes salir de esta página: la importación sigue en segundo plano.</p>
            </div>
          ) : status.data.status === "completed" ? (
            <ImportResult record={status.data} onAgain={() => setRunningId(null)} />
          ) : (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <XCircle className="size-10 text-bad" aria-hidden />
              <p className="text-[16px] font-bold text-ork-ink">La importación no se completó</p>
              <p className="text-[14px] text-ork-mist">{status.data.error}</p>
              <Button variant="secondary" onClick={() => setRunningId(null)}>
                Intentar con otro archivo
              </Button>
            </div>
          )}
        </Card>
      )}

      <Card className="mt-6">
        <CardHeader title="Importaciones recientes" />
        {history.isLoading ? (
          <Spinner />
        ) : !history.data?.length ? (
          <p className="text-[13.5px] text-ork-mist">Todavía no has importado archivos.</p>
        ) : (
          <ul className="divide-y divide-ork-line">
            {history.data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="flex items-center gap-3">
                  <FileSpreadsheet className="size-5 text-ork-mist" aria-hidden />
                  <div>
                    <p className="text-[14px] font-semibold text-ork-ink">{r.filename}</p>
                    <p className="text-[12.5px] text-ork-mist">
                      {formatWhen(r.created_at)}
                      {r.status === "completed" && ` · ${r.totals.nuevos ?? 0} nuevos · ${r.totals.actualizados ?? 0} actualizados`}
                    </p>
                  </div>
                </div>
                <ImportBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function ImportBadge({ status }: { status: ImportRecord["status"] }) {
  if (status === "completed") return <Badge tone="ok">Completada</Badge>;
  if (status === "failed") return <Badge tone="bad">Con error</Badge>;
  if (status === "canceled") return <Badge>Cancelada</Badge>;
  return <Badge tone="blue">En proceso</Badge>;
}

function ImportResult({ record, onAgain }: { record: ImportRecord; onAgain: () => void }) {
  const t = record.totals;
  const stats = [
    { label: "Contactos nuevos", value: t.nuevos },
    { label: "Completados con datos nuevos", value: t.actualizados },
    { label: "Empresas", value: t.empresas },
    { label: "Filas sin nombre o empresa", value: t.omitidas },
    { label: "Repetidos dentro del archivo", value: t.repetidas_en_archivo },
  ];
  return (
    <div className="py-4">
      <div className="mb-6 flex items-center gap-3">
        <CheckCircle2 className="size-8 text-ok" aria-hidden />
        <div>
          <p className="text-[18px] font-bold text-ork-ink">Importación completada</p>
          <p className="text-[13.5px] text-ork-mist">{record.filename}</p>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {stats.map((s) => (
          <div key={s.label} className="rounded-xl border border-ork-line p-3">
            <p className="text-[22px] font-bold text-ork-ink">{(s.value ?? 0).toLocaleString("es")}</p>
            <p className="text-[12.5px] text-ork-mist">{s.label}</p>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[12.5px] text-ork-mist">
        Los contactos que ya existían solo se completaron: ORKESTA nunca reemplaza ni borra un dato que ya tenías.
      </p>
      <div className="mt-5 flex gap-2">
        <Link to="/contactos" className="ork-gradient inline-flex h-10 items-center rounded-xl px-4 text-sm font-semibold text-white">
          Ver contactos
        </Link>
        <EnrichImported />
        <Button variant="secondary" onClick={onAgain}>
          Importar otro archivo
        </Button>
      </div>
    </div>
  );
}


function EnrichImported() {
  const navigate = useNavigate();
  const start = useStartEnrichment(() => navigate("/contactos"));
  return (
    <>
      <Button variant="secondary" icon={<Sparkles className="size-4" aria-hidden />} loading={start.isPending} onClick={() => start.mutate({ filters: { correo: "sin_correo" } })}>
        Enriquecer contactos sin correo
      </Button>
      {start.error && <span className="self-center text-[12.5px] text-bad">{start.error.message}</span>}
    </>
  );
}

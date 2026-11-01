import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Printer, XCircle } from "lucide-react";
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Alert, Button, Spinner, TextField, Wordmark } from "../components/ui";
import { api } from "../lib/api";

type PublicProposal = {
  number: number;
  title: string;
  status: "draft" | "sent" | "viewed" | "accepted" | "rejected";
  currency: string;
  items: { name: string; description: string; quantity: number; unit: string; unit_price: number; discount: number }[];
  sections: { key: string; title: string; body: string }[];
  tax_rate: number;
  totals: { subtotal: number; discounts: number; tax: number; total: number };
  valid_until: string | null;
  expired: boolean;
  company: string | null;
  contact_name: string | null;
  issuer: string;
  owner: { name: string; email: string } | null;
  decided_at: string | null;
  decided_by: string | null;
  created_at: string;
};

const fmt = (v: number, cur: string) => new Intl.NumberFormat("es", { style: "currency", currency: cur, maximumFractionDigits: 2 }).format(v);
const date = (iso: string) => new Date(iso.length === 10 ? iso + "T00:00" : iso).toLocaleDateString("es", { day: "numeric", month: "long", year: "numeric" });

/** Página que ve el cliente (sin sesión). Se puede imprimir o guardar como PDF. */
export function PublicProposalPage() {
  const { publicId } = useParams();
  const [params] = useSearchParams();
  const preview = params.has("vista_previa");
  const client = useQueryClient();
  const key = ["public-proposal", publicId];
  const q = useQuery({
    queryKey: key,
    queryFn: () => api<PublicProposal>(`/api/public/proposals/${publicId}${preview ? "?vista_previa=true" : ""}`),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const decide = useMutation({
    mutationFn: (accept: boolean) => api<PublicProposal>(`/api/public/proposals/${publicId}/decision`, { method: "POST", json: { accept, name, note } }),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const p = q.data;
  if (q.isLoading) return <Spinner label="Abriendo la propuesta" />;
  if (q.error || !p)
    return (
      <div className="mx-auto max-w-lg px-4 py-20 text-center">
        <p className="text-[18px] font-bold text-ork-ink">Propuesta no disponible</p>
        <p className="mt-2 text-[14px] text-ork-mist">{q.error?.message ?? "El enlace no es válido."}</p>
      </div>
    );
  const open = !p.decided_at && !p.expired && p.status !== "draft";
  return (
    <div className="min-h-dvh bg-ork-surface py-8 print:bg-white print:py-0">
      <div className="mx-auto max-w-[860px] px-4 print:max-w-none print:px-0">
        <div className="mb-4 flex items-center justify-between print:hidden">
          <span className="text-[12.5px] text-ork-mist">{preview ? "Vista previa del equipo (no cuenta como apertura)" : "Documento compartido de forma privada"}</span>
          <Button size="sm" variant="secondary" icon={<Printer className="size-4" aria-hidden />} onClick={() => window.print()}>
            Imprimir o guardar PDF
          </Button>
        </div>
        <article className="rounded-2xl bg-white p-8 shadow-[0_1px_3px_rgb(8_11_18/0.08)] sm:p-12 print:rounded-none print:p-0 print:shadow-none">
          <header className="flex flex-wrap items-start justify-between gap-4 border-b border-ork-line pb-6">
            <div>
              <p className="text-[13px] font-semibold uppercase tracking-wide text-ork-blue">{p.issuer}</p>
              <h1 className="mt-1 text-[26px] font-bold leading-tight text-ork-ink">{p.title}</h1>
              <p className="mt-1 text-[13.5px] text-ork-mist">
                {[p.contact_name, p.company].filter(Boolean).join(" · ")}
              </p>
            </div>
            <div className="text-right text-[13px] text-ork-graphite">
              <p className="font-semibold">Propuesta N.º {p.number}</p>
              <p>Emitida el {date(p.created_at)}</p>
              {p.valid_until && <p className={p.expired ? "font-semibold text-bad" : ""}>Válida hasta el {date(p.valid_until)}</p>}
            </div>
          </header>

          {p.sections.map((s) => (
            <section key={s.key} className="mt-7 break-inside-avoid">
              <h2 className="text-[17px] font-bold text-ork-ink">{s.title}</h2>
              <p className="mt-2 whitespace-pre-wrap text-[14.5px] leading-relaxed text-ork-graphite">{s.body}</p>
            </section>
          ))}

          {p.items.length > 0 && (
            <section className="mt-8 break-inside-avoid">
              <h2 className="text-[17px] font-bold text-ork-ink">Inversión</h2>
              <table className="mt-3 w-full text-left text-[13.5px]">
                <thead className="border-b border-ork-line text-[12px] uppercase tracking-wide text-ork-mist">
                  <tr>
                    <th className="py-2 font-semibold">Concepto</th>
                    <th className="py-2 text-right font-semibold">Cant.</th>
                    <th className="py-2 text-right font-semibold">Precio</th>
                    <th className="py-2 text-right font-semibold">Total</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ork-line">
                  {p.items.map((i, n) => (
                    <tr key={n}>
                      <td className="py-2.5 pr-3">
                        <p className="font-semibold text-ork-ink">{i.name}</p>
                        {i.description && <p className="text-[12.5px] text-ork-mist">{i.description}</p>}
                        {i.discount > 0 && <p className="text-[12px] text-ok">Descuento {i.discount} %</p>}
                      </td>
                      <td className="py-2.5 text-right text-ork-graphite">
                        {i.quantity} {i.unit}
                      </td>
                      <td className="py-2.5 text-right text-ork-graphite">{fmt(i.unit_price, p.currency)}</td>
                      <td className="py-2.5 text-right font-semibold text-ork-ink">{fmt(i.quantity * i.unit_price * (1 - i.discount / 100), p.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <dl className="ml-auto mt-4 w-full max-w-[300px] space-y-1 text-[13.5px]">
                <div className="flex justify-between text-ork-graphite">
                  <dt>Subtotal</dt>
                  <dd>{fmt(p.totals.subtotal, p.currency)}</dd>
                </div>
                {p.tax_rate > 0 && (
                  <div className="flex justify-between text-ork-graphite">
                    <dt>Impuestos ({p.tax_rate} %)</dt>
                    <dd>{fmt(p.totals.tax, p.currency)}</dd>
                  </div>
                )}
                <div className="flex justify-between border-t border-ork-line pt-2 text-[17px] font-bold text-ork-ink">
                  <dt>Total</dt>
                  <dd>{fmt(p.totals.total, p.currency)}</dd>
                </div>
              </dl>
            </section>
          )}

          {p.owner && (
            <footer className="mt-10 border-t border-ork-line pt-5 text-[13px] text-ork-graphite">
              Tu contacto: <strong>{p.owner.name}</strong> · {p.owner.email}
            </footer>
          )}
        </article>

        <div className="mt-5 print:hidden">
          {p.decided_at ? (
            <Alert tone={p.status === "accepted" ? "ok" : "bad"}>
              {p.status === "accepted" ? <CheckCircle2 className="mr-1 inline size-4" /> : <XCircle className="mr-1 inline size-4" />}
              {p.decided_by ?? "El cliente"} {p.status === "accepted" ? "aceptó" : "rechazó"} esta propuesta el {date(p.decided_at)}.
            </Alert>
          ) : p.expired ? (
            <Alert>Esta propuesta venció. Escribe a tu asesor para recibir una versión actualizada.</Alert>
          ) : open && !preview ? (
            <div className="rounded-2xl border border-ork-line bg-white p-6">
              <p className="text-[16px] font-bold text-ork-ink">¿Avanzamos?</p>
              <p className="mt-1 text-[13.5px] text-ork-mist">Tu respuesta le llega de inmediato a {p.owner?.name ?? "tu asesor"}.</p>
              {decide.error && <div className="mt-3"><Alert>{decide.error.message}</Alert></div>}
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <TextField label="Tu nombre completo" value={name} onChange={(e) => setName(e.target.value)} />
                <TextField label={rejecting ? "¿Qué podríamos mejorar?" : "Comentario (opcional)"} value={note} onChange={(e) => setNote(e.target.value)} />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {!rejecting ? (
                  <>
                    <Button icon={<CheckCircle2 className="size-4" aria-hidden />} loading={decide.isPending} disabled={name.trim().length < 3} onClick={() => decide.mutate(true)}>
                      Aceptar propuesta
                    </Button>
                    <Button variant="ghost" onClick={() => setRejecting(true)}>
                      No por ahora
                    </Button>
                  </>
                ) : (
                  <>
                    <Button variant="danger" loading={decide.isPending} onClick={() => decide.mutate(false)}>
                      Confirmar que no avanzamos
                    </Button>
                    <Button variant="ghost" onClick={() => setRejecting(false)}>
                      Volver
                    </Button>
                  </>
                )}
              </div>
            </div>
          ) : null}
          <p className="mt-6 flex items-center justify-center gap-2 text-[12px] text-ork-mist">
            Creado con <Wordmark size={16} />
          </p>
        </div>
      </div>
    </div>
  );
}

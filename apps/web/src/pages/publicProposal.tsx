import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileDown, Printer, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { fmtDate, onColor, ProposalDocument, TEXT, type DocData } from "../components/ProposalDocument";
import { Spinner, Wordmark } from "../components/ui";
import { api } from "../lib/api";

type PublicProposal = DocData & {
  status: "draft" | "sent" | "viewed" | "accepted" | "rejected";
  owner: { name: string; email: string } | null;
  decided_at: string | null;
  decided_by: string | null;
  signature?: { name: string; email: string; signed_at: string; doc_hash: string } | null;
};

/** Firma dibujada con mouse o dedo; se exporta como JPEG sobre fondo blanco. */
function SignaturePad({ onChange, clearLabel, hint, color }: { onChange: (data: string) => void; clearLabel: string; hint: string; color: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const drew = useRef(false);
  const [empty, setEmpty] = useState(true);
  useEffect(() => {
    const c = ref.current!;
    const ratio = window.devicePixelRatio || 1;
    c.width = c.offsetWidth * ratio;
    c.height = c.offsetHeight * ratio;
    const ctx = c.getContext("2d")!;
    ctx.scale(ratio, ratio);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#0B1220";
  }, []);
  const point = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = ref.current!.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  };
  const clear = () => {
    const c = ref.current!;
    const ctx = c.getContext("2d")!;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.restore();
    drew.current = false;
    setEmpty(true);
    onChange("");
  };
  return (
    <div>
      <div className="relative">
        <canvas
          ref={ref}
          className="h-[150px] w-full touch-none rounded-xl border-2 border-dashed bg-white"
          style={{ borderColor: `${color}66` }}
          onPointerDown={(e) => {
            drawing.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            const ctx = ref.current!.getContext("2d")!;
            const [x, y] = point(e);
            ctx.beginPath();
            ctx.moveTo(x, y);
          }}
          onPointerMove={(e) => {
            if (!drawing.current) return;
            const ctx = ref.current!.getContext("2d")!;
            const [x, y] = point(e);
            ctx.lineTo(x, y);
            ctx.stroke();
            drew.current = true;
            setEmpty(false);
          }}
          onPointerUp={() => {
            drawing.current = false;
            if (drew.current) onChange(ref.current!.toDataURL("image/jpeg", 0.85));
          }}
        />
        {empty && <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[13px] text-[#9CA3AF]">{hint}</span>}
      </div>
      <button type="button" onClick={clear} className="mt-1 text-[12.5px] font-semibold text-[#6B7280] hover:text-[#111827]">
        {clearLabel}
      </button>
    </div>
  );
}

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
  const [email, setEmail] = useState("");
  const [document, setDocument] = useState("");
  const [signature, setSignature] = useState("");
  const [consent, setConsent] = useState(false);
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [choice, setChoice] = useState<number | null>(null);
  const decide = useMutation({
    mutationFn: (accept: boolean) => api<PublicProposal>(`/api/public/proposals/${publicId}/decision`, { method: "POST", json: { accept, name, note, email, document, signature, consent, choice } }),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const p = q.data;
  if (q.isLoading) return <Spinner label="…" />;
  if (q.error || !p)
    return (
      <div className="mx-auto max-w-lg px-4 py-20 text-center">
        <p className="text-[18px] font-bold text-ork-ink">{TEXT.es.unavailable}</p>
        <p className="mt-2 text-[14px] text-ork-mist">{q.error?.message ?? "—"}</p>
      </div>
    );
  const lang = p.look.language in TEXT ? p.look.language : "es";
  const t = TEXT[lang];
  const open = !p.decided_at && !p.expired && p.status !== "draft";
  const btn = { background: p.look.primary, color: onColor(p.look.primary) };
  return (
    <div className="min-h-dvh py-8 print:bg-white print:py-0" style={{ background: `linear-gradient(180deg, ${p.look.primary}14, #F5F6F8 320px)` }}>
      <div className="mx-auto max-w-[900px] px-3 sm:px-4 print:max-w-none print:px-0">
        <div className="mb-4 flex items-center justify-between gap-3 print:hidden">
          <span className="text-[12.5px] text-[#6B7280]">{preview ? t.preview : t.shared}</span>
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex items-center gap-2 rounded-xl border border-[#E5E7EB] bg-white px-3 py-2 text-[13px] font-semibold text-[#111827] hover:bg-[#F9FAFB]"
          >
            <Printer className="size-4" aria-hidden /> {t.print}
          </button>
        </div>

        <ProposalDocument
          data={p}
          choice={p.chosen_item ?? choice}
          onChoose={open && p.pricing_mode === "opciones" && !preview ? setChoice : undefined}
        />

        <div className="mt-6 print:hidden">
          {p.decided_at ? (
            <div className={`rounded-2xl border px-5 py-4 text-[14px] font-semibold ${p.status === "accepted" ? "border-[#16A34A]/30 bg-[#F0FDF4] text-[#15803D]" : "border-[#DC2626]/30 bg-[#FEF2F2] text-[#B91C1C]"}`}>
              {p.status === "accepted" ? <CheckCircle2 className="mr-1.5 inline size-4" /> : <XCircle className="mr-1.5 inline size-4" />}
              {p.decided_by ?? t.client} {p.status === "accepted" ? t.accepted : t.rejected} {fmtDate(p.decided_at, lang)}.
              {p.signature && (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[13px] font-normal">
                  <span>
                    {t.signedBy} {p.signature.name} ({p.signature.email}) · SHA-256 {p.signature.doc_hash.slice(0, 16)}…
                  </span>
                  <a href={`/api/public/proposals/${publicId}/constancia.pdf`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-semibold underline">
                    <FileDown className="size-4" aria-hidden /> {t.certificate}
                  </a>
                </div>
              )}
            </div>
          ) : p.expired ? (
            <div className="rounded-2xl border border-[#E5E7EB] bg-white px-5 py-4 text-[14px] text-[#374151]">{t.expiredMsg}</div>
          ) : open && !preview ? (
            <div className="rounded-2xl border border-[#E5E7EB] bg-white p-6 shadow-sm">
              <p className="text-[18px] font-bold text-[#0B1220]">{t.proceed}</p>
              <p className="mt-1 text-[13.5px] text-[#6B7280]">
                {t.notify} {p.look.executive.name || p.owner?.name || t.advisor}.
              </p>
              {decide.error && <p className="mt-3 rounded-lg bg-[#FEF2F2] px-3 py-2 text-[13px] text-[#B91C1C]">{decide.error.message}</p>}
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-[#111827]">
                  {t.fullName}
                  <input value={name} onChange={(e) => setName(e.target.value)} className="h-11 rounded-xl border border-[#D1D5DB] px-3 text-[15px] font-normal focus:outline-2" style={{ outlineColor: p.look.primary }} />
                </label>
                {!rejecting && (
                  <>
                    <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-[#111827]">
                      {t.email}
                      <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className="h-11 rounded-xl border border-[#D1D5DB] px-3 text-[15px] font-normal focus:outline-2" style={{ outlineColor: p.look.primary }} />
                    </label>
                    <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-[#111827]">
                      {t.document}
                      <input value={document} onChange={(e) => setDocument(e.target.value)} className="h-11 rounded-xl border border-[#D1D5DB] px-3 text-[15px] font-normal focus:outline-2" style={{ outlineColor: p.look.primary }} />
                    </label>
                  </>
                )}
                <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-[#111827]">
                  {rejecting ? t.improve : t.comment}
                  <input value={note} onChange={(e) => setNote(e.target.value)} className="h-11 rounded-xl border border-[#D1D5DB] px-3 text-[15px] font-normal focus:outline-2" style={{ outlineColor: p.look.primary }} />
                </label>
              </div>
              {!rejecting && (
                <div className="mt-4">
                  <SignaturePad onChange={setSignature} clearLabel={t.clear} hint={t.signHere} color={p.look.primary} />
                  <label className="mt-3 flex items-start gap-2 text-[13px] text-[#374151]">
                    <input type="checkbox" className="mt-0.5 size-4" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                    <span>{t.consent}</span>
                  </label>
                </div>
              )}
              {!rejecting && p.pricing_mode === "opciones" && (
                <p className="mt-3 rounded-lg px-3 py-2 text-[13px] font-semibold" style={{ background: `${p.look.primary}14`, color: p.look.primary }}>
                  {choice === null ? t.pickFirst : `${t.chosen}: ${t.option} ${choice + 1} · ${p.items[choice]?.name ?? ""}`}
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                {!rejecting ? (
                  <>
                    <button
                      type="button"
                      disabled={
                        name.trim().length < 3 ||
                        !email.includes("@") ||
                        !signature ||
                        !consent ||
                        decide.isPending ||
                        (p.pricing_mode === "opciones" && choice === null)
                      }
                      onClick={() => decide.mutate(true)}
                      className="inline-flex h-11 items-center gap-2 rounded-xl px-5 text-[14.5px] font-semibold shadow-sm disabled:opacity-50"
                      style={btn}
                    >
                      <CheckCircle2 className="size-4" aria-hidden /> {t.acceptSign}
                    </button>
                    <button type="button" onClick={() => setRejecting(true)} className="h-11 rounded-xl px-4 text-[14px] font-semibold text-[#4B5563] hover:bg-[#F3F4F6]">
                      {t.notNow}
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" disabled={decide.isPending} onClick={() => decide.mutate(false)} className="h-11 rounded-xl border border-[#DC2626]/40 px-4 text-[14px] font-semibold text-[#B91C1C] hover:bg-[#FEF2F2]">
                      {t.confirmNo}
                    </button>
                    <button type="button" onClick={() => setRejecting(false)} className="h-11 rounded-xl px-4 text-[14px] font-semibold text-[#4B5563] hover:bg-[#F3F4F6]">
                      {t.back}
                    </button>
                  </>
                )}
              </div>
            </div>
          ) : null}
          <p className="mt-6 flex items-center justify-center gap-2 text-[12px] text-[#9CA3AF]">
            {t.made} <Wordmark size={16} />
          </p>
        </div>
      </div>
    </div>
  );
}

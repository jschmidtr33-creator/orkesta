import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Printer, XCircle } from "lucide-react";
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { fmtDate, onColor, ProposalDocument, TEXT, type DocData } from "../components/ProposalDocument";
import { Spinner, Wordmark } from "../components/ui";
import { api } from "../lib/api";

type PublicProposal = DocData & {
  status: "draft" | "sent" | "viewed" | "accepted" | "rejected";
  owner: { name: string; email: string } | null;
  decided_at: string | null;
  decided_by: string | null;
};

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

        <ProposalDocument data={p} />

        <div className="mt-6 print:hidden">
          {p.decided_at ? (
            <div className={`rounded-2xl border px-5 py-4 text-[14px] font-semibold ${p.status === "accepted" ? "border-[#16A34A]/30 bg-[#F0FDF4] text-[#15803D]" : "border-[#DC2626]/30 bg-[#FEF2F2] text-[#B91C1C]"}`}>
              {p.status === "accepted" ? <CheckCircle2 className="mr-1.5 inline size-4" /> : <XCircle className="mr-1.5 inline size-4" />}
              {p.decided_by ?? t.client} {p.status === "accepted" ? t.accepted : t.rejected} {fmtDate(p.decided_at, lang)}.
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
                <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-[#111827]">
                  {rejecting ? t.improve : t.comment}
                  <input value={note} onChange={(e) => setNote(e.target.value)} className="h-11 rounded-xl border border-[#D1D5DB] px-3 text-[15px] font-normal focus:outline-2" style={{ outlineColor: p.look.primary }} />
                </label>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {!rejecting ? (
                  <>
                    <button
                      type="button"
                      disabled={name.trim().length < 3 || decide.isPending}
                      onClick={() => decide.mutate(true)}
                      className="inline-flex h-11 items-center gap-2 rounded-xl px-5 text-[14.5px] font-semibold shadow-sm disabled:opacity-50"
                      style={btn}
                    >
                      <CheckCircle2 className="size-4" aria-hidden /> {t.accept}
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

import { CalendarClock, Globe, Link2, Mail, MapPin, Phone } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

export type Look = {
  template: "ejecutiva" | "moderna" | "corporativa" | "minimal" | "audaz";
  language: "es" | "en" | "pt";
  primary: string;
  accent: string;
  font: "sans" | "serif" | "geometrica" | "humanista";
  cover_url: string;
  logo_url: string;
  executive: { name?: string; title?: string; email?: string; phone?: string; photo_url?: string; linkedin?: string; calendar_url?: string };
  company: { legal_name: string; website: string; phone: string; address: string; tax_id: string; footer: string };
};
export type DocItem = { name: string; description: string; quantity: number; unit: string; unit_price: number; discount: number };
export type DocData = {
  number: number;
  title: string;
  currency: string;
  items: DocItem[];
  sections: { key: string; title: string; body: string }[];
  tax_rate: number;
  totals: { subtotal: number; discounts: number; tax: number; total: number };
  valid_until: string | null;
  expired?: boolean;
  company: string | null;
  contact_name: string | null;
  issuer: string;
  created_at: string;
  look: Look;
};

export const TEXT = {
  es: {
    eyebrow: "Propuesta comercial",
    number: "Propuesta N.º",
    issued: "Emitida",
    valid: "Válida hasta",
    for: "Preparada para",
    by: "Preparada por",
    investment: "Inversión",
    concept: "Concepto",
    qty: "Cant.",
    price: "Precio",
    total: "Total",
    subtotal: "Subtotal",
    taxes: "Impuestos",
    discount: "Descuento",
    contact: "Tu contacto",
    schedule: "Agendar reunión",
    content: "Contenido",
    expired: "Vencida",
    print: "Imprimir o guardar PDF",
    shared: "Documento compartido de forma privada",
    preview: "Vista previa del equipo (no cuenta como apertura)",
    proceed: "¿Avanzamos?",
    notify: "Tu respuesta le llega de inmediato a",
    advisor: "tu asesor",
    fullName: "Tu nombre completo",
    comment: "Comentario (opcional)",
    improve: "¿Qué podríamos mejorar?",
    accept: "Aceptar propuesta",
    notNow: "No por ahora",
    confirmNo: "Confirmar que no avanzamos",
    back: "Volver",
    accepted: "aceptó esta propuesta el",
    rejected: "rechazó esta propuesta el",
    client: "El cliente",
    expiredMsg: "Esta propuesta venció. Escribe a tu asesor para recibir una versión actualizada.",
    unavailable: "Propuesta no disponible",
    made: "Creado con",
  },
  en: {
    eyebrow: "Business proposal",
    number: "Proposal No.",
    issued: "Issued",
    valid: "Valid until",
    for: "Prepared for",
    by: "Prepared by",
    investment: "Investment",
    concept: "Item",
    qty: "Qty",
    price: "Price",
    total: "Total",
    subtotal: "Subtotal",
    taxes: "Taxes",
    discount: "Discount",
    contact: "Your contact",
    schedule: "Book a meeting",
    content: "Contents",
    expired: "Expired",
    print: "Print or save as PDF",
    shared: "Privately shared document",
    preview: "Team preview (not counted as a view)",
    proceed: "Shall we move forward?",
    notify: "Your answer reaches",
    advisor: "your advisor",
    fullName: "Your full name",
    comment: "Comment (optional)",
    improve: "What could we improve?",
    accept: "Accept proposal",
    notNow: "Not now",
    confirmNo: "Confirm we won't proceed",
    back: "Back",
    accepted: "accepted this proposal on",
    rejected: "declined this proposal on",
    client: "The client",
    expiredMsg: "This proposal has expired. Contact your advisor for an updated version.",
    unavailable: "Proposal unavailable",
    made: "Made with",
  },
  pt: {
    eyebrow: "Proposta comercial",
    number: "Proposta N.º",
    issued: "Emitida",
    valid: "Válida até",
    for: "Preparada para",
    by: "Preparada por",
    investment: "Investimento",
    concept: "Item",
    qty: "Qtd.",
    price: "Preço",
    total: "Total",
    subtotal: "Subtotal",
    taxes: "Impostos",
    discount: "Desconto",
    contact: "Seu contato",
    schedule: "Agendar reunião",
    content: "Conteúdo",
    expired: "Vencida",
    print: "Imprimir ou salvar PDF",
    shared: "Documento compartilhado de forma privada",
    preview: "Pré-visualização da equipe (não conta como abertura)",
    proceed: "Vamos avançar?",
    notify: "Sua resposta chega imediatamente a",
    advisor: "seu consultor",
    fullName: "Seu nome completo",
    comment: "Comentário (opcional)",
    improve: "O que poderíamos melhorar?",
    accept: "Aceitar proposta",
    notNow: "Agora não",
    confirmNo: "Confirmar que não vamos avançar",
    back: "Voltar",
    accepted: "aceitou esta proposta em",
    rejected: "recusou esta proposta em",
    client: "O cliente",
    expiredMsg: "Esta proposta venceu. Fale com seu consultor para receber uma versão atualizada.",
    unavailable: "Proposta indisponível",
    made: "Criado com",
  },
};
export type Texts = (typeof TEXT)["es"];

const LOCALE = { es: "es", en: "en-US", pt: "pt-BR" } as const;
const FONT_STACK = {
  sans: "Inter, 'Helvetica Neue', Arial, system-ui, sans-serif",
  serif: "Georgia, 'Times New Roman', 'Iowan Old Style', serif",
  geometrica: "'Avenir Next', Avenir, Montserrat, 'Century Gothic', 'Segoe UI', sans-serif",
  humanista: "'Segoe UI', 'Trebuchet MS', 'Gill Sans', Candara, sans-serif",
};

/** Texto legible sobre un color de fondo (blanco o casi negro). */
export function onColor(hex: string): string {
  const n = parseInt(hex.replace("#", ""), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? "#0B1220" : "#FFFFFF";
}

function mix(hex: string, other: string, amount: number): string {
  const a = parseInt(hex.replace("#", ""), 16);
  const b = parseInt(other.replace("#", ""), 16);
  const ch = (x: number, y: number) => Math.round(x + (y - x) * amount);
  const r = ch((a >> 16) & 255, (b >> 16) & 255);
  const g = ch((a >> 8) & 255, (b >> 8) & 255);
  const bl = ch(a & 255, b & 255);
  return `#${((1 << 24) | (r << 16) | (g << 8) | bl).toString(16).slice(1)}`;
}

export function fmtMoney(v: number, cur: string, lang: Look["language"]) {
  return new Intl.NumberFormat(LOCALE[lang], { style: "currency", currency: cur, maximumFractionDigits: 2 }).format(v);
}
export function fmtDate(iso: string, lang: Look["language"]) {
  return new Date(iso.length === 10 ? iso + "T00:00" : iso).toLocaleDateString(LOCALE[lang], { day: "numeric", month: "long", year: "numeric" });
}

type Ctx = { d: DocData; t: Texts; L: Look; lang: Look["language"]; on: string; dark: string; soft: string };

/** Párrafos y viñetas a partir del texto del editor. */
function Body({ text, className, bullet }: { text: string; className?: string; bullet?: string }) {
  const color = bullet ?? "var(--a)";
  const isItem = (l: string) => /^\s*(•|-|\d+\.)\s+/.test(l);
  return (
    <div className={className}>
      {text.split(/\n{2,}/).map((block, i) => {
        // Agrupa líneas seguidas de viñetas en listas y el resto en párrafos.
        const groups: { list: boolean; lines: string[] }[] = [];
        for (const line of block.split("\n")) {
          const list = isItem(line);
          const last = groups[groups.length - 1];
          if (last && last.list === list) last.lines.push(line);
          else groups.push({ list, lines: [line] });
        }
        return (
          <div key={i} className="my-2">
            {groups.map((g, k) =>
              g.list ? (
                <ul key={k} className="my-1.5 space-y-1.5">
                  {g.lines.map((l, j) => {
                    const num = l.match(/^\s*(\d+)\./)?.[1];
                    return (
                      <li key={j} className="flex gap-2.5">
                        {num ? (
                          <span className="min-w-5 shrink-0 font-bold" style={{ color }}>
                            {num}.
                          </span>
                        ) : (
                          <span className="mt-[0.6em] inline-block size-1.5 shrink-0 rounded-full" style={{ background: color }} aria-hidden />
                        )}
                        <span>{l.replace(/^\s*(•|-|\d+\.)\s+/, "")}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p key={k} className="whitespace-pre-line">
                  {g.lines.join("\n")}
                </p>
              ),
            )}
          </div>
        );
      })}
    </div>
  );
}

function Logo({ L, className, light }: { L: Look; className?: string; light?: boolean }) {
  if (!L.logo_url) return <span className={`text-[15px] font-bold tracking-wide ${light ? "text-white" : ""}`}>{L.company.legal_name}</span>;
  return <img src={L.logo_url} alt={L.company.legal_name} className={className ?? "h-10 w-auto max-w-[180px] object-contain"} />;
}

function Investment({ c, variant }: { c: Ctx; variant: "solid" | "line" | "card" | "dark" }) {
  const { d, t, lang } = c;
  if (!d.items.length) return null;
  const head =
    variant === "solid"
      ? { background: "var(--p)", color: c.on }
      : variant === "dark"
        ? { background: "#0B1020", color: "#fff" }
        : { borderBottom: "2px solid var(--p)", color: "var(--p)" };
  return (
    <section className="break-inside-avoid">
      <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] border-collapse text-left text-[13.5px]">
        <thead>
          <tr style={head} className="text-[11.5px] uppercase tracking-[0.08em]">
            <th className="px-3 py-2.5 font-semibold">{t.concept}</th>
            <th className="px-3 py-2.5 text-right font-semibold">{t.qty}</th>
            <th className="px-3 py-2.5 text-right font-semibold">{t.price}</th>
            <th className="px-3 py-2.5 text-right font-semibold">{t.total}</th>
          </tr>
        </thead>
        <tbody>
          {d.items.map((i, n) => (
            <tr key={n} style={{ background: variant === "card" && n % 2 ? c.soft : undefined, borderBottom: "1px solid #E5E7EB" }}>
              <td className="px-3 py-3 align-top">
                <p className="font-semibold text-[#0B1220]">{i.name}</p>
                {i.description && <p className="mt-0.5 text-[12.5px] text-[#6B7280]">{i.description}</p>}
                {i.discount > 0 && (
                  <p className="mt-0.5 text-[12px] font-semibold" style={{ color: "var(--a)" }}>
                    {t.discount} {i.discount} %
                  </p>
                )}
              </td>
              <td className="px-3 py-3 text-right align-top text-[#374151]">
                {i.quantity} {i.unit}
              </td>
              <td className="px-3 py-3 text-right align-top text-[#374151]">{fmtMoney(i.unit_price, d.currency, lang)}</td>
              <td className="px-3 py-3 text-right align-top font-semibold text-[#0B1220]">{fmtMoney(i.quantity * i.unit_price * (1 - i.discount / 100), d.currency, lang)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <div className="mt-5 flex justify-end">
        <dl className="w-full max-w-[320px] text-[13.5px]">
          <div className="flex justify-between py-1 text-[#4B5563]">
            <dt>{t.subtotal}</dt>
            <dd>{fmtMoney(d.totals.subtotal, d.currency, lang)}</dd>
          </div>
          {d.tax_rate > 0 && (
            <div className="flex justify-between py-1 text-[#4B5563]">
              <dt>
                {t.taxes} ({d.tax_rate} %)
              </dt>
              <dd>{fmtMoney(d.totals.tax, d.currency, lang)}</dd>
            </div>
          )}
          <div
            className="mt-2 flex items-center justify-between rounded-xl px-4 py-3 text-[18px] font-bold"
            style={
              variant === "line"
                ? { borderTop: "2px solid var(--p)", borderRadius: 0, paddingLeft: 0, paddingRight: 0, color: "var(--p)" }
                : variant === "dark"
                  ? { background: "#0B1020", color: "#fff" }
                  : { background: "var(--p)", color: c.on }
            }
          >
            <dt>{t.total}</dt>
            <dd style={variant === "dark" ? { color: "var(--a)" } : undefined}>{fmtMoney(d.totals.total, d.currency, lang)}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

function Executive({ c, tone = "light" }: { c: Ctx; tone?: "light" | "dark" | "outline" }) {
  const e = c.L.executive;
  if (!e.name) return null;
  const dark = tone === "dark";
  return (
    <div
      className="flex items-center gap-4 rounded-2xl p-5 break-inside-avoid"
      style={dark ? { background: "#0B1020", color: "#fff" } : tone === "outline" ? { border: "1px solid #E5E7EB" } : { background: c.soft }}
    >
      {e.photo_url ? (
        <img src={e.photo_url} alt={e.name} className="size-16 shrink-0 rounded-full object-cover" style={{ boxShadow: "0 0 0 3px var(--a)" }} />
      ) : (
        <span className="flex size-16 shrink-0 items-center justify-center rounded-full text-[20px] font-bold" style={{ background: "var(--p)", color: c.on }}>
          {e.name
            .split(" ")
            .slice(0, 2)
            .map((w) => w[0])
            .join("")}
        </span>
      )}
      <div className="min-w-0 text-[13px]">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--a)" }}>
          {c.t.contact}
        </p>
        <p className="text-[16px] font-bold">{e.name}</p>
        {e.title && <p className={dark ? "text-white/70" : "text-[#6B7280]"}>{e.title}</p>}
        <div className={`mt-1.5 flex flex-wrap gap-x-4 gap-y-1 ${dark ? "text-white/85" : "text-[#374151]"}`}>
          {e.email && (
            <a href={`mailto:${e.email}`} className="inline-flex items-center gap-1 hover:underline">
              <Mail className="size-3.5" aria-hidden /> {e.email}
            </a>
          )}
          {e.phone && (
            <a href={`tel:${e.phone.replace(/\s/g, "")}`} className="inline-flex items-center gap-1 hover:underline">
              <Phone className="size-3.5" aria-hidden /> {e.phone}
            </a>
          )}
          {e.linkedin && (
            <a href={e.linkedin} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
              <Link2 className="size-3.5" aria-hidden /> LinkedIn
            </a>
          )}
          {e.calendar_url && (
            <a href={e.calendar_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold hover:underline" style={{ color: "var(--a)" }}>
              <CalendarClock className="size-3.5" aria-hidden /> {c.t.schedule}
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

function CompanyFooter({ c, tone = "light" }: { c: Ctx; tone?: "light" | "primary" | "dark" }) {
  const co = c.L.company;
  const style: CSSProperties =
    tone === "primary" ? { background: "var(--p)", color: c.on } : tone === "dark" ? { background: "#0B1020", color: "#fff" } : { borderTop: "1px solid #E5E7EB", color: "#6B7280" };
  return (
    <footer className="px-8 py-6 text-[12px] sm:px-12" style={style}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="font-semibold">
          {co.legal_name}
          {co.tax_id && <span className="font-normal opacity-80"> · {co.tax_id}</span>}
        </span>
        <span className="flex flex-wrap gap-x-4 gap-y-1 opacity-90">
          {co.website && (
            <span className="inline-flex items-center gap-1">
              <Globe className="size-3.5" aria-hidden /> {co.website}
            </span>
          )}
          {co.phone && (
            <span className="inline-flex items-center gap-1">
              <Phone className="size-3.5" aria-hidden /> {co.phone}
            </span>
          )}
          {co.address && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="size-3.5" aria-hidden /> {co.address}
            </span>
          )}
        </span>
      </div>
      {co.footer && <p className="mt-2 whitespace-pre-line opacity-75">{co.footer}</p>}
    </footer>
  );
}

function Meta({ c, light }: { c: Ctx; light?: boolean }) {
  const { d, t, lang } = c;
  const muted = light ? "opacity-75" : "text-[#6B7280]";
  return (
    <dl className="grid grid-cols-3 gap-4 text-[12.5px]">
      <div>
        <dt className={`text-[10.5px] font-semibold uppercase tracking-[0.12em] ${muted}`}>{t.number}</dt>
        <dd className="mt-0.5 font-semibold">{String(d.number).padStart(4, "0")}</dd>
      </div>
      <div>
        <dt className={`text-[10.5px] font-semibold uppercase tracking-[0.12em] ${muted}`}>{t.issued}</dt>
        <dd className="mt-0.5 font-semibold">{fmtDate(d.created_at, lang)}</dd>
      </div>
      {d.valid_until && (
        <div>
          <dt className={`text-[10.5px] font-semibold uppercase tracking-[0.12em] ${muted}`}>{t.valid}</dt>
          <dd className="mt-0.5 font-semibold">
            {fmtDate(d.valid_until, lang)}
            {d.expired && <span className="ml-1 text-[#DC2626]">· {t.expired}</span>}
          </dd>
        </div>
      )}
    </dl>
  );
}

const forWhom = (d: DocData) => [d.contact_name, d.company].filter(Boolean).join(" · ");

// --- Plantillas -----------------------------------------------------------------------------

function Ejecutiva({ c }: { c: Ctx }) {
  const { d, t, L } = c;
  return (
    <>
      <header className="relative overflow-hidden px-8 pb-12 pt-10 sm:px-12" style={{ background: `linear-gradient(135deg, var(--p) 0%, ${c.dark} 100%)`, color: c.on }}>
        {L.cover_url && <img src={L.cover_url} alt="" className="absolute inset-0 size-full object-cover opacity-25 mix-blend-luminosity" />}
        <svg className="pointer-events-none absolute -right-24 -top-24 size-[420px] opacity-[0.12]" viewBox="0 0 200 200" aria-hidden>
          {[90, 70, 50, 30].map((r) => (
            <circle key={r} cx="100" cy="100" r={r} fill="none" stroke="currentColor" strokeWidth="1.2" />
          ))}
        </svg>
        <div className="relative flex items-center justify-between">
          <span className="rounded-xl bg-white px-3 py-2 shadow-sm">
            <Logo L={L} className="h-8 w-auto max-w-[160px] object-contain" />
          </span>
          <span className="text-[11px] font-semibold uppercase tracking-[0.2em] opacity-80">{t.eyebrow}</span>
        </div>
        <div className="relative mt-16 max-w-[620px]">
          <span className="mb-4 block h-1 w-16 rounded-full" style={{ background: "var(--a)" }} />
          <h1 className="text-[34px] font-bold leading-[1.1] sm:text-[42px]">{d.title}</h1>
          {forWhom(d) && (
            <p className="mt-4 text-[15px] opacity-90">
              {t.for}: <strong>{forWhom(d)}</strong>
            </p>
          )}
        </div>
        <div className="relative mt-12 border-t border-current/20 pt-5" style={{ borderColor: `${c.on}33` }}>
          <Meta c={c} light />
        </div>
      </header>
      <div className="space-y-10 px-8 py-12 sm:px-12">
        {d.sections.map((s, i) => (
          <section key={s.key} className="break-inside-avoid">
            <div className="flex items-baseline gap-3">
              <span className="text-[13px] font-bold" style={{ color: "var(--a)" }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <h2 className="text-[21px] font-bold text-[#0B1220]">{s.title}</h2>
            </div>
            <span className="ml-8 mt-2 block h-0.5 w-10" style={{ background: "var(--p)" }} />
            <Body text={s.body} className="ml-8 mt-3 text-[14.5px] leading-relaxed text-[#374151]" />
          </section>
        ))}
        {d.items.length > 0 && (
          <section>
            <div className="mb-4 flex items-baseline gap-3">
              <span className="text-[13px] font-bold" style={{ color: "var(--a)" }}>
                {String(d.sections.length + 1).padStart(2, "0")}
              </span>
              <h2 className="text-[21px] font-bold text-[#0B1220]">{t.investment}</h2>
            </div>
            <Investment c={c} variant="solid" />
          </section>
        )}
        <Executive c={c} />
      </div>
      <CompanyFooter c={c} tone="primary" />
    </>
  );
}

function Moderna({ c }: { c: Ctx }) {
  const { d, t, L } = c;
  return (
    <>
      <header className="grid min-h-[380px] sm:grid-cols-[42%_58%]">
        <div className="relative overflow-hidden p-8 sm:p-10" style={{ background: "var(--p)", color: c.on }}>
          <svg className="pointer-events-none absolute inset-0 size-full" viewBox="0 0 300 400" preserveAspectRatio="xMidYMid slice" aria-hidden>
            <circle cx="260" cy="60" r="110" fill="var(--a)" opacity="0.35" />
            <circle cx="40" cy="360" r="140" fill="#FFFFFF" opacity="0.08" />
            <rect x="170" y="230" width="90" height="90" rx="18" fill="#FFFFFF" opacity="0.1" transform="rotate(18 215 275)" />
          </svg>
          {L.cover_url && <img src={L.cover_url} alt="" className="absolute inset-0 size-full object-cover opacity-30" />}
          <div className="relative flex h-full flex-col justify-between gap-10">
            <span className="w-fit rounded-xl bg-white/95 px-3 py-2">
              <Logo L={L} className="h-8 w-auto max-w-[150px] object-contain" />
            </span>
            <p className="text-[64px] font-black leading-none opacity-90">{String(d.number).padStart(3, "0")}</p>
          </div>
        </div>
        <div className="flex flex-col justify-center gap-5 bg-white p-8 sm:p-12">
          <span className="w-fit rounded-full px-3 py-1 text-[11px] font-bold uppercase tracking-[0.16em]" style={{ background: c.soft, color: "var(--p)" }}>
            {t.eyebrow}
          </span>
          <h1 className="text-[32px] font-extrabold leading-[1.1] text-[#0B1220] sm:text-[38px]">{d.title}</h1>
          {forWhom(d) && <p className="text-[15px] text-[#4B5563]">{forWhom(d)}</p>}
          <Meta c={c} />
        </div>
      </header>
      <div className="space-y-6 bg-[#F7F8FA] px-6 py-10 sm:px-10">
        {d.sections.map((s, i) => (
          <section key={s.key} className="relative overflow-hidden rounded-2xl bg-white p-6 shadow-[0_1px_2px_rgb(0_0_0/0.05)] break-inside-avoid sm:p-8">
            <span className="absolute inset-y-0 left-0 w-1.5" style={{ background: i % 2 ? "var(--a)" : "var(--p)" }} />
            <div className="flex items-start gap-5">
              <span className="hidden text-[34px] font-black leading-none sm:block" style={{ color: i % 2 ? "var(--a)" : "var(--p)", opacity: 0.25 }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="text-[19px] font-bold text-[#0B1220]">{s.title}</h2>
                <Body text={s.body} className="mt-2 text-[14.5px] leading-relaxed text-[#374151]" />
              </div>
            </div>
          </section>
        ))}
        {d.items.length > 0 && (
          <section className="rounded-2xl bg-white p-6 shadow-[0_1px_2px_rgb(0_0_0/0.05)] sm:p-8">
            <h2 className="mb-4 text-[19px] font-bold text-[#0B1220]">{t.investment}</h2>
            <Investment c={c} variant="card" />
          </section>
        )}
        <Executive c={c} tone="outline" />
      </div>
      <CompanyFooter c={c} />
    </>
  );
}

function Corporativa({ c }: { c: Ctx }) {
  const { d, t, L } = c;
  return (
    <>
      <header className="px-8 pt-8 sm:px-12">
        <div className="flex flex-wrap items-center justify-between gap-4 pb-5">
          <Logo L={L} />
          <div className="text-right text-[12px] text-[#4B5563]">
            <p className="font-semibold text-[#0B1220]">{L.company.legal_name}</p>
            {L.company.website && <p>{L.company.website}</p>}
            {L.company.tax_id && <p>{L.company.tax_id}</p>}
          </div>
        </div>
        <div className="h-1.5" style={{ background: "var(--p)" }} />
        <div className="mt-0.5 h-0.5" style={{ background: "var(--a)" }} />
      </header>
      {L.cover_url && <img src={L.cover_url} alt="" className="mx-8 mt-8 h-56 w-[calc(100%-4rem)] rounded object-cover sm:mx-12 sm:w-[calc(100%-6rem)]" />}
      <div className="px-8 py-10 text-center sm:px-12">
        <p className="text-[11.5px] font-semibold uppercase tracking-[0.25em]" style={{ color: "var(--p)" }}>
          {t.eyebrow}
        </p>
        <h1 className="mx-auto mt-3 max-w-[640px] text-[32px] font-bold leading-tight text-[#0B1220]">{d.title}</h1>
        {forWhom(d) && (
          <div className="mx-auto mt-6 w-fit rounded border px-6 py-3 text-[13.5px]" style={{ borderColor: "var(--p)" }}>
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[#6B7280]">{t.for}</p>
            <p className="font-semibold text-[#0B1220]">{forWhom(d)}</p>
          </div>
        )}
        <div className="mx-auto mt-8 max-w-[560px] text-left">
          <Meta c={c} />
        </div>
      </div>
      <div className="space-y-9 px-8 pb-12 sm:px-12">
        {d.sections.map((s, i) => (
          <section key={s.key} className="break-inside-avoid">
            <h2 className="border-b pb-2 text-[14px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--p)", borderColor: "#D1D5DB" }}>
              {i + 1}. {s.title}
            </h2>
            <Body text={s.body} className="mt-3 text-justify text-[14.5px] leading-[1.75] text-[#1F2937]" bullet="var(--p)" />
          </section>
        ))}
        {d.items.length > 0 && (
          <section>
            <h2 className="mb-4 border-b pb-2 text-[14px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--p)", borderColor: "#D1D5DB" }}>
              {d.sections.length + 1}. {t.investment}
            </h2>
            <Investment c={c} variant="line" />
          </section>
        )}
        <Executive c={c} tone="outline" />
      </div>
      <CompanyFooter c={c} tone="primary" />
    </>
  );
}

function Minimal({ c }: { c: Ctx }) {
  const { d, t, L } = c;
  return (
    <>
      <header className="px-8 pb-14 pt-10 sm:px-14">
        <div className="flex items-center justify-between">
          <Logo L={L} className="h-7 w-auto max-w-[140px] object-contain" />
          <span className="text-[12px] text-[#9CA3AF]">
            {t.number} {String(d.number).padStart(4, "0")}
          </span>
        </div>
        <h1 className="mt-24 max-w-[720px] text-[44px] font-light leading-[1.05] tracking-tight sm:text-[54px]" style={{ color: "var(--p)" }}>
          {d.title}
        </h1>
        <span className="mt-8 block h-px w-24" style={{ background: "var(--a)" }} />
        {forWhom(d) && <p className="mt-6 text-[15px] text-[#4B5563]">{forWhom(d)}</p>}
        <div className="mt-10 max-w-[560px]">
          <Meta c={c} />
        </div>
      </header>
      {L.cover_url && <img src={L.cover_url} alt="" className="h-64 w-full object-cover grayscale" />}
      <div className="divide-y divide-[#F0F1F3] px-8 sm:px-14">
        {d.sections.map((s) => (
          <section key={s.key} className="grid gap-3 py-9 break-inside-avoid sm:grid-cols-[1fr_2fr] sm:gap-10">
            <h2 className="text-[15px] font-semibold text-[#0B1220]">{s.title}</h2>
            <Body text={s.body} className="text-[14.5px] leading-[1.8] text-[#4B5563]" bullet="var(--p)" />
          </section>
        ))}
        {d.items.length > 0 && (
          <section className="grid gap-3 py-9 sm:grid-cols-[1fr_2fr] sm:gap-10">
            <h2 className="text-[15px] font-semibold text-[#0B1220]">{t.investment}</h2>
            <Investment c={c} variant="line" />
          </section>
        )}
        <div className="py-9">
          <Executive c={c} tone="outline" />
        </div>
      </div>
      <CompanyFooter c={c} />
    </>
  );
}

function Audaz({ c }: { c: Ctx }) {
  const { d, t, L } = c;
  return (
    <>
      <header className="relative overflow-hidden bg-[#0B1020] px-8 pb-14 pt-10 text-white sm:px-12">
        {L.cover_url && <img src={L.cover_url} alt="" className="absolute inset-0 size-full object-cover opacity-30" />}
        <div className="pointer-events-none absolute -right-20 top-0 h-full w-[55%] skew-x-[-14deg]" style={{ background: "var(--p)", opacity: 0.9 }} />
        <div className="pointer-events-none absolute -right-10 top-0 h-full w-[18%] skew-x-[-14deg]" style={{ background: "var(--a)" }} />
        <div className="relative flex items-center justify-between">
          <span className="rounded-lg bg-white px-3 py-2">
            <Logo L={L} className="h-8 w-auto max-w-[150px] object-contain" />
          </span>
        </div>
        <div className="relative mt-20 max-w-[560px]">
          <p className="text-[12px] font-bold uppercase tracking-[0.3em]" style={{ color: "var(--a)" }}>
            {t.eyebrow}
          </p>
          <h1 className="mt-3 text-[40px] font-black uppercase leading-[1] sm:text-[50px]">{d.title}</h1>
          {forWhom(d) && (
            <p className="mt-5 inline-block px-2 py-1 text-[14px] font-semibold" style={{ background: "var(--a)", color: onColor(L.accent) }}>
              {forWhom(d)}
            </p>
          )}
        </div>
        <div className="relative mt-12 max-w-[560px]">
          <Meta c={c} light />
        </div>
      </header>
      <div className="space-y-12 px-8 py-12 sm:px-12">
        {d.sections.map((s, i) => (
          <section key={s.key} className="grid gap-4 break-inside-avoid sm:grid-cols-[90px_1fr]">
            <span className="text-[56px] font-black leading-none" style={{ color: "var(--p)" }}>
              {String(i + 1).padStart(2, "0")}
            </span>
            <div>
              <h2 className="inline text-[22px] font-extrabold uppercase text-[#0B1220]" style={{ boxShadow: `inset 0 -0.45em 0 ${L.accent}55` }}>
                {s.title}
              </h2>
              <Body text={s.body} className="mt-3 text-[14.5px] leading-relaxed text-[#374151]" />
            </div>
          </section>
        ))}
        {d.items.length > 0 && (
          <section>
            <h2 className="mb-5 inline-block text-[22px] font-extrabold uppercase text-[#0B1220]" style={{ boxShadow: `inset 0 -0.45em 0 ${L.accent}55` }}>
              {t.investment}
            </h2>
            <Investment c={c} variant="dark" />
          </section>
        )}
        <Executive c={c} tone="dark" />
      </div>
      <CompanyFooter c={c} tone="dark" />
    </>
  );
}

const TEMPLATES = { ejecutiva: Ejecutiva, moderna: Moderna, corporativa: Corporativa, minimal: Minimal, audaz: Audaz };

/** Documento de la propuesta con la plantilla, colores, tipografía e idioma elegidos. */
export function ProposalDocument({ data, children }: { data: DocData; children?: ReactNode }) {
  const L = data.look;
  const lang = L.language in TEXT ? L.language : "es";
  const ctx: Ctx = { d: data, t: TEXT[lang], L, lang, on: onColor(L.primary), dark: mix(L.primary, "#000000", 0.45), soft: mix(L.primary, "#FFFFFF", 0.92) };
  const Template = TEMPLATES[L.template] ?? Ejecutiva;
  return (
    <article
      lang={lang}
      className="ork-doc overflow-hidden rounded-2xl bg-white text-[#111827] shadow-[0_10px_40px_rgb(8_11_18/0.12)] print:rounded-none print:shadow-none"
      style={{ "--p": L.primary, "--a": L.accent, fontFamily: FONT_STACK[L.font] ?? FONT_STACK.sans } as CSSProperties}
    >
      <Template c={ctx} />
      {children}
    </article>
  );
}

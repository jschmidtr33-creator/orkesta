// Componentes base del sistema de diseño ORKESTA.
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Logo({ size = 28, className }: { size?: number; className?: string }) {
  return <img src="/app/logo-mark.svg" alt="" width={size} height={Math.round(size * 0.9)} className={cx("shrink-0", className)} />;
}

/** Logo completo: símbolo + «ORKESTΛ» (la A final sin travesaño, como en la marca). */
export function Wordmark({ dark = false, size = 28 }: { dark?: boolean; size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5" role="img" aria-label="ORKESTA">
      <Logo size={size} />
      <span
        aria-hidden="true"
        className={cx("font-medium tracking-[0.2em]", dark ? "text-white" : "text-ork-ink")}
        style={{ fontSize: size * 0.62, fontFamily: "'Avenir Next', Avenir, Montserrat, 'Century Gothic', 'Segoe UI', sans-serif" }}
      >
        ORKESTΛ
      </span>
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  icon?: ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading, icon, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        "ork-focus inline-flex items-center justify-center gap-2 rounded-xl font-semibold transition",
        "disabled:cursor-not-allowed disabled:opacity-60",
        size === "sm" && "h-8 px-3 text-[13px]",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-6 text-[15px]",
        variant === "primary" &&
          "ork-gradient text-white shadow-[0_6px_20px_rgb(79_70_229/0.35)] hover:brightness-110",
        variant === "secondary" &&
          "border border-ork-line bg-white text-ork-ink hover:border-ork-blue/40 hover:bg-ork-blue/5",
        variant === "ghost" && "text-ork-graphite hover:bg-ork-ink/5",
        variant === "danger" && "border border-bad/30 bg-white text-bad hover:bg-bad/5",
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

type FieldProps = { label: string; hint?: ReactNode; error?: string | null };

export function TextField({
  label,
  hint,
  error,
  className,
  ...input
}: FieldProps & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-semibold text-ork-ink">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={Boolean(error)}
        aria-describedby={hint || error ? `${id}-hint` : undefined}
        className={cx(
          "ork-focus h-11 rounded-xl border bg-white px-3.5 text-[15px] text-ork-ink placeholder:text-ork-mist",
          error ? "border-bad" : "border-ork-line hover:border-ork-mist",
        )}
        {...input}
      />
      {(hint || error) && (
        <p id={`${id}-hint`} className={cx("text-[12.5px]", error ? "text-bad" : "text-ork-mist")}>
          {error || hint}
        </p>
      )}
    </div>
  );
}

export function SelectField({
  label,
  children,
  className,
  ...select
}: { label: string; children: ReactNode } & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-semibold text-ork-ink">
        {label}
      </label>
      <select
        id={id}
        className="ork-focus h-11 rounded-xl border border-ork-line bg-white px-3 text-[15px] text-ork-ink hover:border-ork-mist"
        {...select}
      >
        {children}
      </select>
    </div>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx("ork-card p-5", className)}>{children}</section>;
}

export function CardHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-[15px] font-bold text-ork-ink">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-ork-mist">{description}</p>}
      </div>
      {action}
    </div>
  );
}

const BADGE_TONES = {
  neutral: "bg-ork-ink/5 text-ork-graphite",
  blue: "bg-ork-blue/10 text-ork-blue",
  violet: "bg-ork-violet/10 text-ork-violet",
  ok: "bg-ok/10 text-ok",
  warn: "bg-warn/10 text-warn",
  bad: "bg-bad/10 text-bad",
};

export function Badge({ tone = "neutral", children }: { tone?: keyof typeof BADGE_TONES; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-semibold", BADGE_TONES[tone])}>
      {children}
    </span>
  );
}

export function Alert({ tone = "bad", children }: { tone?: "bad" | "ok" | "blue"; children: ReactNode }) {
  return (
    <div
      role={tone === "bad" ? "alert" : "status"}
      className={cx(
        "rounded-xl border px-3.5 py-2.5 text-[13.5px]",
        tone === "bad" && "border-bad/20 bg-bad/5 text-bad",
        tone === "ok" && "border-ok/20 bg-ok/5 text-ok",
        tone === "blue" && "border-ork-blue/20 bg-ork-blue/5 text-ork-blue",
      )}
    >
      {children}
    </div>
  );
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean | "xl";
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ork-ink/50 backdrop-blur-[2px]" />
        <Dialog.Content className={cx("fixed left-1/2 top-1/2 z-50 max-h-[92dvh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-white p-6 shadow-[var(--shadow-pop)]", wide === "xl" ? "w-[min(96vw,1180px)]" : wide ? "w-[min(94vw,760px)]" : "w-[min(92vw,480px)]")}>
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-lg font-bold text-ork-ink">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-1 text-[13.5px] text-ork-mist">{description}</Dialog.Description>}
            </div>
            <Dialog.Close className="ork-focus rounded-lg p-1 text-ork-mist hover:bg-ork-ink/5" aria-label="Cerrar">
              <X className="size-5" />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Spinner({ label = "Cargando" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-ork-mist" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function PageHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-bold tracking-tight text-ork-ink">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-[14px] text-ork-mist">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function initials(name: string, email: string): string {
  const source = name.trim() || email;
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "·";
}

export function Avatar({ name, email, size = 32 }: { name: string; email: string; size?: number }) {
  return (
    <span
      className="ork-gradient inline-flex shrink-0 items-center justify-center rounded-full font-bold text-white"
      style={{ width: size, height: size, fontSize: size * 0.38 }}
      aria-hidden
    >
      {initials(name, email)}
    </span>
  );
}

/** Globo de ayuda inmediato al pasar el mouse o enfocar (también con teclado). */
export function Tip({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <span className={cx("group relative inline-flex", className)} tabIndex={0} aria-label={label}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-[280px] -translate-x-1/2 whitespace-normal rounded-lg bg-ork-ink text-center px-2.5 py-1.5 text-[12px] font-medium text-white opacity-0 shadow-lg transition group-hover:opacity-100 group-focus:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}

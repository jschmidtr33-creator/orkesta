import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Alert, Button, TextField, Wordmark } from "../components/ui";
import { api, type Session } from "../lib/api";
import { useSession } from "../lib/session";

const GOOGLE_ERRORS: Record<string, string> = {
  google_pendiente: "El inicio de sesión con Google está pendiente de conexión. Usa tu correo y contraseña.",
  google_cancelado: "Cancelaste el inicio de sesión con Google.",
  google: "Google no confirmó el inicio de sesión. Inténtalo de nuevo.",
  cuenta_inactiva: "Tu cuenta está desactivada. Habla con tu administrador.",
};

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-ork-sidebar text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="ork-glow pointer-events-none absolute inset-0" aria-hidden />
        <div className="relative">
          <Wordmark dark size={34} />
        </div>
        <div className="relative max-w-md">
          <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-ork-mist">
            Plataforma de automatización comercial B2B
          </p>
          <h1 className="mt-4 text-[40px] font-bold leading-[1.1] tracking-tight">
            De la prospección a la oportunidad. <span className="ork-gradient-text">Todo conectado</span> en un solo sistema.
          </h1>
          <ul className="mt-8 space-y-3 text-[15px] text-white/80">
            {[
              "Encuentra decisores con filtros por cargo, industria y país.",
              "Enriquece y valida cada dato, con su fuente.",
              "Campañas, CRM y propuestas en el mismo lugar.",
            ].map((line) => (
              <li key={line} className="flex gap-3">
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-ork-violet" aria-hidden />
                {line}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-[13px] text-ork-mist">Conecta. Enriquece. Conquista.</p>
      </aside>
      <main className="flex items-center justify-center px-5 py-10 sm:px-10">
        <div className="w-full max-w-[420px]">
          <div className="mb-8 lg:hidden">
            <Wordmark size={30} />
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

function GoogleButton({ label }: { label: string }) {
  const config = useQuery({
    queryKey: ["auth-config"],
    queryFn: () => api<{ google: boolean }>("/api/auth/config"),
  });
  const enabled = config.data?.google;
  return (
    <div>
      <a
        href={enabled ? "/api/auth/google/start?next=/app" : undefined}
        aria-disabled={!enabled}
        className={
          "ork-focus flex h-11 w-full items-center justify-center gap-3 rounded-xl border border-ork-line bg-ork-card text-[14.5px] font-semibold text-ork-ink transition " +
          (enabled ? "hover:border-ork-mist hover:bg-ork-surface" : "cursor-not-allowed opacity-55")
        }
      >
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
          <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
          <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
          <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
          <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
        </svg>
        {label}
      </a>
      {config.data && !enabled && (
        <p className="mt-1.5 text-center text-[12px] text-ork-mist">Inicio con Google: pendiente de conexión</p>
      )}
      <div className="my-5 flex items-center gap-3 text-[12px] text-ork-mist">
        <span className="h-px flex-1 bg-ork-line" /> o con tu correo <span className="h-px flex-1 bg-ork-line" />
      </div>
    </div>
  );
}

function useAuthSubmit(path: string) {
  const { setSession } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("siguiente");
  return useMutation({
    mutationFn: (body: unknown) => api<Session>(path, { method: "POST", json: body }),
    onSuccess: (data) => {
      setSession(data);
      // Solo rutas internas de la app (evita redirecciones abiertas).
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : null;
      navigate(safeNext ?? (data.workspace ? "/" : "/bienvenida"), { replace: true });
    },
  });
}

export function LoginPage() {
  const { session } = useSession();
  const [params] = useSearchParams();
  const submit = useAuthSubmit("/api/auth/login");
  const [form, setForm] = useState({ email: "", password: "" });
  if (session) return <Navigate to="/" replace />;
  const googleError = GOOGLE_ERRORS[params.get("error") ?? ""];
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit.mutate(form);
  };
  return (
    <AuthLayout>
      <h2 className="text-[26px] font-bold tracking-tight text-ork-ink">Ingresa a ORKESTA</h2>
      <p className="mb-7 mt-1 text-[14.5px] text-ork-mist">Te damos la bienvenida de nuevo.</p>
      <GoogleButton label="Continuar con Google" />
      <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        {googleError && <Alert>{googleError}</Alert>}
        {submit.error && <Alert>{submit.error.message}</Alert>}
        <TextField label="Correo" type="email" autoComplete="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <TextField label="Contraseña" type="password" autoComplete="current-password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <Button type="submit" size="lg" loading={submit.isPending} className="mt-1 w-full">
          Ingresar
        </Button>
      </form>
      <p className="mt-6 text-center text-[14px] text-ork-mist">
        ¿Tu empresa aún no usa ORKESTA?{" "}
        <Link to="/registro" className="font-semibold text-ork-blue hover:underline">
          Crea tu espacio de trabajo
        </Link>
      </p>
    </AuthLayout>
  );
}

export function RegisterPage() {
  const { session } = useSession();
  const submit = useAuthSubmit("/api/auth/register");
  const [form, setForm] = useState({ company: "", full_name: "", email: "", password: "" });
  if (session) return <Navigate to="/" replace />;
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  return (
    <AuthLayout>
      <h2 className="text-[26px] font-bold tracking-tight text-ork-ink">Crea tu espacio de trabajo</h2>
      <p className="mb-7 mt-1 text-[14.5px] text-ork-mist">Tu empresa, tu equipo y tus datos, separados de los demás.</p>
      <GoogleButton label="Registrarme con Google" />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit.mutate(form);
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        {submit.error && <Alert>{submit.error.message}</Alert>}
        <TextField label="Empresa" autoComplete="organization" required value={form.company} onChange={set("company")} />
        <TextField label="Tu nombre completo" autoComplete="name" required value={form.full_name} onChange={set("full_name")} />
        <TextField label="Correo de trabajo" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
        <TextField
          label="Contraseña"
          type="password"
          autoComplete="new-password"
          required
          value={form.password}
          onChange={set("password")}
          hint="Mínimo 10 caracteres, con mayúsculas, minúsculas y un número."
        />
        <Button type="submit" size="lg" loading={submit.isPending} className="mt-1 w-full">
          Crear espacio de trabajo
        </Button>
        <p className="text-center text-[12px] leading-relaxed text-ork-mist">
          Al registrarte aceptas el tratamiento de tus datos para operar tu cuenta, según la{" "}
          <a href="/privacidad" className="underline">política de privacidad</a>.
        </p>
      </form>
      <p className="mt-6 text-center text-[14px] text-ork-mist">
        ¿Ya tienes cuenta?{" "}
        <Link to="/ingresar" className="font-semibold text-ork-blue hover:underline">
          Ingresa
        </Link>
      </p>
    </AuthLayout>
  );
}

export function WelcomePage() {
  const { session, setSession } = useSession();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api<Session>("/api/workspaces", { method: "POST", json: { name } }),
    onSuccess: (data) => {
      setSession(data);
      navigate("/", { replace: true });
    },
  });
  if (!session) return <Navigate to="/ingresar" replace />;
  return (
    <AuthLayout>
      <h2 className="text-[26px] font-bold tracking-tight text-ork-ink">
        Hola{session.user.full_name ? `, ${session.user.full_name.split(" ")[0]}` : ""}
      </h2>
      <p className="mb-7 mt-1 text-[14.5px] text-ork-mist">
        Crea el espacio de trabajo de tu empresa. Si te invitaron a uno, abre el enlace de la invitación.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="flex flex-col gap-4"
      >
        {create.error && <Alert>{create.error.message}</Alert>}
        <TextField label="Nombre de la empresa" autoComplete="organization" required value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" size="lg" loading={create.isPending} className="w-full">
          Crear espacio de trabajo
        </Button>
      </form>
    </AuthLayout>
  );
}

type InvitationInfo = { workspace: string; email: string; role: string; account_exists: "yes" | "no" };

export function InvitationPage() {
  const { token = "" } = useParams();
  const { session, setSession } = useSession();
  const navigate = useNavigate();
  const info = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => api<InvitationInfo>(`/api/invitations/${encodeURIComponent(token)}`),
    retry: false,
  });
  const [form, setForm] = useState({ full_name: "", password: "" });
  const accept = useMutation({
    mutationFn: () =>
      api<Session>(`/api/invitations/${encodeURIComponent(token)}/accept`, {
        method: "POST",
        json: session ? {} : form,
      }),
    onSuccess: (data) => {
      setSession(data);
      navigate("/", { replace: true });
    },
  });
  return (
    <AuthLayout>
      {info.isLoading && <p className="text-ork-mist">Revisando la invitación…</p>}
      {info.error && (
        <>
          <h2 className="text-[24px] font-bold text-ork-ink">Invitación no disponible</h2>
          <div className="mt-4">
            <Alert>{info.error.message}</Alert>
          </div>
        </>
      )}
      {info.data && (
        <>
          <h2 className="text-[26px] font-bold tracking-tight text-ork-ink">Únete a {info.data.workspace}</h2>
          <p className="mb-7 mt-1 text-[14.5px] text-ork-mist">
            Te invitaron como <strong className="text-ork-ink">{info.data.role}</strong> con el correo {info.data.email}.
          </p>
          {accept.error && (
            <div className="mb-4">
              <Alert>{accept.error.message}</Alert>
            </div>
          )}
          {session ? (
            <Button size="lg" className="w-full" loading={accept.isPending} onClick={() => accept.mutate()}>
              Aceptar invitación como {session.user.email}
            </Button>
          ) : info.data.account_exists === "yes" ? (
            <Alert tone="blue">
              Ya tienes cuenta con ese correo.{" "}
              <Link to={`/ingresar?siguiente=/invitacion/${token}`} className="font-semibold underline">
                Ingresa
              </Link>{" "}
              y vuelve a abrir este enlace.
            </Alert>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                accept.mutate();
              }}
              className="flex flex-col gap-4"
            >
              <TextField label="Tu nombre completo" required autoComplete="name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
              <TextField
                label="Crea una contraseña"
                type="password"
                required
                autoComplete="new-password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                hint="Mínimo 10 caracteres, con mayúsculas, minúsculas y un número."
              />
              <Button type="submit" size="lg" loading={accept.isPending} className="w-full">
                Crear cuenta y unirme
              </Button>
            </form>
          )}
        </>
      )}
    </AuthLayout>
  );
}

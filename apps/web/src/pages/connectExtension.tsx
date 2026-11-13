import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Puzzle } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { Alert, Button, Card, Spinner, Wordmark } from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";

const STORE = "https://chromewebstore.google.com/detail/cfaooobjacoehihnbhapaomnoidcoidp";

/** /app/conectar-extension: conecta la extensión de Chrome con la cuenta que inició sesión. La
 * extensión escucha en esta página (y solo aquí) y guarda la clave que se le entrega. */
export function ConnectExtensionPage() {
  const { session, loading } = useSession();
  const [installed, setInstalled] = useState<boolean | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== window.location.origin) return;
      if (event.data?.type === "orkesta-extension-ready") setInstalled(true);
      if (event.data?.type === "orkesta-extension-saved") setSaved(true);
    };
    window.addEventListener("message", onMessage);
    window.postMessage({ type: "orkesta-extension-ping" }, window.location.origin);
    const timer = setTimeout(() => setInstalled((v) => v ?? false), 1500);
    return () => {
      window.removeEventListener("message", onMessage);
      clearTimeout(timer);
    };
  }, []);
  const connect = useMutation({
    mutationFn: () => api<{ token: string; company: string }>("/api/extension/connect", { method: "POST" }),
    onSuccess: (r) => window.postMessage({ type: "orkesta-extension-token", token: r.token }, window.location.origin),
  });
  if (loading) return <Spinner />;
  if (!session) return <Navigate to="/ingresar?siguiente=/conectar-extension" replace />;
  const company = session.workspace?.name ?? "";
  return (
    <div className="grid min-h-screen place-items-center bg-ork-bg px-4">
      <Card className="w-full max-w-md text-center">
        <div className="flex justify-center">
          <Wordmark />
        </div>
        {saved ? (
          <>
            <CheckCircle2 className="mx-auto mt-6 size-10 text-ok" aria-hidden />
            <h1 className="mt-3 text-[20px] font-bold text-ork-ink">Extensión conectada</h1>
            <p className="mt-2 text-[14px] text-ork-graphite">
              Los contactos que guardes se irán a {company ? <strong>{company}</strong> : "tu empresa"}. Ya puedes cerrar esta pestaña y usar «Guardar en ORKESTA».
            </p>
          </>
        ) : (
          <>
            <Puzzle className="mx-auto mt-6 size-10 text-ork-blue" aria-hidden />
            <h1 className="mt-3 text-[20px] font-bold text-ork-ink">Conectar la extensión de Chrome</h1>
            <p className="mt-2 text-[14px] text-ork-graphite">
              Vas a conectar la extensión con <strong>{session.user.full_name || session.user.email}</strong>
              {company ? (
                <>
                  {" "}en <strong>{company}</strong>
                </>
              ) : null}
              . Solo podrá guardar y consultar tus contactos.
            </p>
            {installed === false && (
              <div className="mt-4">
                <Alert>
                  No encontramos la extensión en este navegador.{" "}
                  <a className="font-semibold underline" href={STORE} target="_blank" rel="noopener noreferrer">
                    Instálala desde la Chrome Web Store
                  </a>{" "}
                  y vuelve a abrir esta página.
                </Alert>
              </div>
            )}
            {connect.error && (
              <div className="mt-4">
                <Alert>{connect.error.message}</Alert>
              </div>
            )}
            <div className="mt-6 flex justify-center">
              <Button loading={connect.isPending || installed === null} disabled={installed === false} onClick={() => connect.mutate()}>
                Conectar
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

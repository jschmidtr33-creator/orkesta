# ORKESTA en AWS

Una sola máquina EC2 con ORKESTA, PostgreSQL 16, HTTPS automático (Caddy + Let's Encrypt) y
respaldos diarios en S3. La imagen la construye GitHub Actions y cada cambio en `main` se
despliega solo.

## Costos: lo que hay que saber

- **Cuentas de AWS creadas desde el 15 de julio de 2025**: plan gratuito de **6 meses** o hasta
  **200 USD en créditos** (100 USD al registrarse y 20 USD por cada una de 5 actividades), lo que
  se acabe primero. Al terminar, la cuenta se pausa hasta pasar a un plan pagado.
- **Cuentas creadas antes de esa fecha**: siguen con la capa gratuita antigua de 12 meses.
- Esta configuración cuesta aproximadamente **15 a 20 USD al mes** en precio normal: `t3.small`,
  20 GB de disco y una IP pública. Con los créditos, eso cubre unos 6 meses.
- Para no llevarse sorpresas, crea un **presupuesto con alerta** en AWS Budgets (además da 20 USD
  de crédito).

## Paso a paso (unos 30 minutos)

1. **Cuenta y alerta de costos.** Crea la cuenta en aws.amazon.com y elige la región
   **us-east-1** (Virginia) o **sa-east-1** (São Paulo, más cerca de Colombia). En *Billing →
   Budgets*, crea un presupuesto de 20 USD con alerta por correo.
2. **Servidor (EC2 → Lanzar instancia):**
   - Nombre: `orkesta`. Imagen: **Ubuntu Server 24.04 LTS (x86)**. Tipo: **t3.small**.
   - Par de claves: crea uno nuevo y guarda el archivo `.pem`.
   - Red: permite **SSH (22)**, **HTTP (80)** y **HTTPS (443)**.
   - Almacenamiento: **20 GB gp3**.
3. **IP fija.** En *EC2 → Direcciones IP elásticas*, asigna una y asóciala a la instancia.
4. **Dominio.** Apunta tu dominio (registro A) a esa IP. Si aún no tienes dominio, usa
   `1-2-3-4.sslip.io`, poniendo tu IP con guiones; también obtiene HTTPS.
5. **Respaldos (opcional, recomendado).** Crea un bucket en S3 (ej. `orkesta-respaldos-jenn`).
   Luego crea un rol de IAM para EC2 con permiso `s3:PutObject` sobre ese bucket y asígnalo a la
   instancia (*Acciones → Seguridad → Modificar rol de IAM*).
6. **Preparar el servidor.** Entra con *EC2 → Conectar → EC2 Instance Connect* y ejecuta:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/jschmidtr33-creator/orkesta/main/deploy/aws/setup.sh | sudo bash
   sudo nano /opt/orkesta/.env
   ```
   Completa `DOMAIN`, `PUBLIC_BASE_URL` y `BACKUP_BUCKET`. Copia desde Render **los mismos**
   valores de `SECRET_KEY`, `SERPER_API_KEY`, `GOOGLE_CLIENT_ID/SECRET` y
   `MICROSOFT_CLIENT_ID/SECRET`. La `SECRET_KEY` debe ser idéntica: con ella se descifran los
   buzones conectados.
7. **Imagen pública.** En GitHub → tu perfil → *Packages → orkesta → Package settings*, cambia la
   visibilidad a **Public**. La imagen no contiene secretos; todos van en `.env`.
8. **Copiar los datos desde Render.** En Render → base `orkesta-db` → *Connect*, copia la
   **External Database URL** y ejecuta:
   ```bash
   sudo /opt/orkesta/migrate-from-render.sh "postgresql://...render.com/orkesta"
   sudo /opt/orkesta/deploy.sh
   ```
9. **Google y Microsoft.** Agrega la nueva dirección de retorno en Google Cloud
   (`https://TU-DOMINIO/api/auth/google/callback` y `/api/mail/google/callback`) y en Microsoft
   Entra (`https://TU-DOMINIO/api/mail/microsoft/callback`).
10. **Despliegue automático.** En GitHub → *Settings → Secrets → Actions*, crea `AWS_HOST` (la IP
    o el dominio) y `AWS_SSH_KEY` (el contenido del `.pem`). Desde ahí, cada cambio aprobado se
    publica solo.
11. **Apagar Render** cuando confirmes que todo funciona en AWS: suspende el servicio y la base,
    para no pagar los dos.

## Operación

- Ver registros: `cd /opt/orkesta && sudo docker compose logs -f app`
- Reiniciar: `sudo docker compose restart app`
- Respaldo manual: `sudo /opt/orkesta/backup.sh` (deja copias locales de 7 días en
  `/opt/orkesta/backups` y las sube a S3).
- Restaurar un respaldo:
  `gunzip -c backups/ARCHIVO.sql.gz | sudo docker compose exec -T db psql -U postgres -d orkesta`

## Diferencias con Render

- **Más memoria:** 2 GB de RAM más 2 GB de memoria de intercambio, frente a 512 MB. Se acaban los
  reinicios por falta de memoria.
- **El correo por SMTP funciona** (puertos 587 y 465). AWS solo bloquea el puerto 25. La prueba
  de buzones por SMTP del validador usa el puerto 25: pide a AWS quitar esa restricción (*EC2 →
  Request to remove email sending limitations*) o usa el verificador externo.
- **La base de datos es tuya** y queda en el mismo servidor, con respaldos diarios.

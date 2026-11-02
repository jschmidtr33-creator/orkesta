# Verificador de buzones de ORKESTA

Servicio propio que pregunta al servidor de correo de cada empresa si un buzón existe
(conversación SMTP por el puerto 25, sin enviar ningún correo). Render no permite salir por el
puerto 25, así que este servicio vive en un servidor aparte (cualquier VPS Ubuntu con el puerto
25 de salida abierto).

## Instalar (una sola vez)

En un VPS Ubuntu recién creado, como root:

```bash
curl -fsSL https://raw.githubusercontent.com/jschmidtr33-creator/orkesta/main/apps/verifier/install.sh | bash
```

Al terminar muestra tres valores. Cárgalos en Render (servicio de la API → Environment):

- `VERIFIER_URL`
- `VERIFIER_TOKEN`
- `VERIFIER_FINGERPRINT`

Con eso, la validación de correos de ORKESTA usa el verificador automáticamente. Si los valores
no están, ORKESTA sigue funcionando con la verificación previa por DNS y la memoria de patrones.

## Seguridad

- HTTPS con certificado propio; ORKESTA solo confía en la huella SHA-256 instalada.
- Cada pedido requiere el token; máximo 120 pedidos por minuto y 25 direcciones por pedido.
- No guarda direcciones ni resultados.

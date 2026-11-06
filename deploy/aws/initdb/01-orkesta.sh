#!/bin/sh
# Primera vez que arranca PostgreSQL: crea el usuario de la aplicación SIN superusuario.
# Importante: un superusuario se salta el aislamiento entre empresas (RLS).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE ROLE orkesta LOGIN PASSWORD '${DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE DATABASE orkesta OWNER orkesta;
SQL

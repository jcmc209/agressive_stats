-- Ejecutar en Supabase: SQL Editor → New query → Pegar y Run
-- Añade columnas para offsides (datos de API-Football) si no existen.

ALTER TABLE matches
  ADD COLUMN IF NOT EXISTS offsides_home integer,
  ADD COLUMN IF NOT EXISTS offsides_away integer;

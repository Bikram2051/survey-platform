-- Runs once on first `docker compose up` (empty data volume only).
-- The application role is deliberately NOT a superuser and has no
-- BYPASSRLS: row-level security only constrains roles subject to it.
-- CREATEDB exists solely so the test runner can create test databases.
CREATE ROLE app_user LOGIN PASSWORD 'app_password' NOSUPERUSER NOCREATEROLE NOBYPASSRLS CREATEDB;
CREATE DATABASE surveydb OWNER app_user;

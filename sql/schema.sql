CREATE TABLE dim_produit(id serial PRIMARY KEY, nom text NOT NULL, categorie text NOT NULL, prix_achat numeric NOT NULL);
CREATE TABLE dim_client(id serial PRIMARY KEY, nom text NOT NULL, created date NOT NULL);
CREATE TABLE fact_ventes(id bigserial PRIMARY KEY, date date NOT NULL, ticket int NOT NULL,
  produit_id int REFERENCES dim_produit, client_id int REFERENCES dim_client, qte int NOT NULL, montant numeric NOT NULL, marge numeric NOT NULL);
CREATE INDEX ON fact_ventes(date);
CREATE TABLE stock(produit_id int PRIMARY KEY REFERENCES dim_produit, stock int NOT NULL);
CREATE TABLE users(email text PRIMARY KEY, pw text NOT NULL, role text NOT NULL CHECK (role IN ('patron','employe')));
CREATE VIEW v_stock AS
SELECT p.id, p.nom, s.stock, COALESCE(ROUND(s.stock/NULLIF(v.q/30.0,0),1),99)::float AS couverture,
  CASE WHEN s.stock=0 THEN 'rupture' WHEN s.stock/NULLIF(v.q/30.0,0)<5 THEN 'bas' ELSE 'ok' END AS etat
FROM dim_produit p JOIN stock s ON s.produit_id=p.id
LEFT JOIN (SELECT produit_id, SUM(qte) q FROM fact_ventes WHERE date>current_date-30 GROUP BY 1) v ON v.produit_id=p.id;
-- Rôle lecture seule utilisé par l'assistant IA (pas d'accès à la table users). Changer le mot de passe en production.
CREATE ROLE bi_readonly LOGIN PASSWORD 'change_me_ro';
GRANT SELECT ON fact_ventes, dim_produit, dim_client, v_stock TO bi_readonly;
ALTER ROLE bi_readonly SET statement_timeout='5s';
ALTER ROLE bi_readonly SET default_transaction_read_only=on;

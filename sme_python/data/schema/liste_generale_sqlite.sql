-- Schéma SQLite — liste générale (migration depuis Access).
-- - id           : identifiant interne du nouveau système (AUTOINCREMENT).
-- - id_acces     : copie de ListeGenerale.Id Access (traçabilité / import ; ne sert pas de PK interne).
--   (demande métier « idAcces » : même rôle sous le nom id_acces en SQL.)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS liste_generale (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  id_acces TEXT,
  noms TEXT,
  date_n TEXT,
  lieu_n TEXT,
  grade TEXT,
  date_grade TEXT,
  date_service TEXT,
  ndecision TEXT,
  matmilitaire TEXT,
  matsolde TEXT,
  padmin TEXT,
  fonction TEXT,
  sub_arme TEXT,
  diplome TEXT,
  date_obtention TEXT,
  structure TEXT,
  obs TEXT,
  oder_tri INTEGER,
  presseance INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Un même Id Access ne doit pas être dupliqué après import (lignes sans id_acces possibles pour données créées uniquement côté SQLite).
CREATE UNIQUE INDEX IF NOT EXISTS idx_liste_generale_id_acces
  ON liste_generale(id_acces)
  WHERE id_acces IS NOT NULL AND trim(id_acces) <> '';

CREATE INDEX IF NOT EXISTS idx_liste_generale_structure ON liste_generale(structure);
CREATE INDEX IF NOT EXISTS idx_liste_generale_oder_tri ON liste_generale(oder_tri);

-- ---------------------------------------------------------------------------
-- Référentiels et satellites (copie Access) — même fichier que la liste générale
-- lorsque le mode SQLite est activé.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tab_structure (
  code TEXT PRIMARY KEY NOT NULL,
  designation TEXT,
  oder_tri INTEGER
);

CREATE TABLE IF NOT EXISTS tab_grade (
  code_g TEXT PRIMARY KEY NOT NULL,
  grade TEXT,
  pref_ord INTEGER,
  effectif_g INTEGER
);

CREATE TABLE IF NOT EXISTS tab_secun (
  code_u TEXT PRIMARY KEY NOT NULL,
  secu_design TEXT
);

CREATE TABLE IF NOT EXISTS tab_secdeux (
  code_u TEXT NOT NULL,
  code_d TEXT NOT NULL,
  secd_design TEXT,
  pri_out_d REAL,
  PRIMARY KEY (code_u, code_d)
);

CREATE TABLE IF NOT EXISTS tab_sectrois (
  code_d TEXT NOT NULL,
  code_t TEXT NOT NULL,
  sect_design TEXT,
  pri_out_t REAL,
  PRIMARY KEY (code_d, code_t)
);

CREATE TABLE IF NOT EXISTS tab_sme (
  codsme TEXT PRIMARY KEY NOT NULL,
  mois TEXT,
  statut TEXT
);

CREATE TABLE IF NOT EXISTS tab_mission (
  id TEXT PRIMARY KEY NOT NULL,
  nature TEXT,
  lieu_mission TEXT,
  refnds TEXT,
  date_depart TEXT
);

CREATE TABLE IF NOT EXISTS tab_malade (
  id TEXT PRIMARY KEY NOT NULL,
  date_constat TEXT,
  pathologie TEXT,
  obs TEXT
);

CREATE TABLE IF NOT EXISTS tab_deserteur (
  id TEXT PRIMARY KEY NOT NULL,
  dat_desertion TEXT
);

CREATE TABLE IF NOT EXISTS tab_stage_local (
  id TEXT PRIMARY KEY NOT NULL,
  nature_stage TEXT,
  lieu_stage TEXT,
  date_debut_fin TEXT
);

CREATE TABLE IF NOT EXISTS tab_stage_ex (
  id TEXT PRIMARY KEY NOT NULL,
  nature_stage TEXT,
  lieu_stage TEXT,
  date_debut_fin TEXT
);

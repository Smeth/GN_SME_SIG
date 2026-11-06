-- Tables opérationnelles SME-Gend (MySQL).
-- À exécuter dans la base MySQL de travail (ex. smegend).

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS liste_generale (
  id BIGINT NOT NULL AUTO_INCREMENT,
  id_acces VARCHAR(128) DEFAULT NULL,
  noms VARCHAR(512) DEFAULT NULL,
  date_n VARCHAR(64) DEFAULT NULL,
  lieu_n VARCHAR(256) DEFAULT NULL,
  grade VARCHAR(256) DEFAULT NULL,
  date_grade VARCHAR(64) DEFAULT NULL,
  date_service VARCHAR(64) DEFAULT NULL,
  ndecision VARCHAR(128) DEFAULT NULL,
  matmilitaire VARCHAR(128) DEFAULT NULL,
  matsolde VARCHAR(128) DEFAULT NULL,
  padmin VARCHAR(256) DEFAULT NULL,
  fonction VARCHAR(512) DEFAULT NULL,
  sub_arme VARCHAR(512) DEFAULT NULL,
  diplome VARCHAR(512) DEFAULT NULL,
  date_obtention VARCHAR(64) DEFAULT NULL,
  structure VARCHAR(128) DEFAULT NULL,
  obs TEXT,
  ordre_tri INT DEFAULT NULL,
  presseance INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_liste_generale_id_acces (id_acces)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_structure (
  code VARCHAR(64) NOT NULL,
  designation VARCHAR(512) DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_grade (
  code_g VARCHAR(64) NOT NULL,
  grade VARCHAR(512) DEFAULT NULL,
  pref_ord INT DEFAULT NULL,
  effectif_g INT DEFAULT NULL,
  PRIMARY KEY (code_g)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_secun (
  code_u VARCHAR(64) NOT NULL,
  secu_design VARCHAR(512) DEFAULT NULL,
  PRIMARY KEY (code_u)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_secdeux (
  code_u VARCHAR(64) NOT NULL,
  code_d VARCHAR(64) NOT NULL,
  secd_design VARCHAR(512) DEFAULT NULL,
  pri_out_d DOUBLE DEFAULT NULL,
  PRIMARY KEY (code_u, code_d)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_sectrois (
  code_d VARCHAR(64) NOT NULL,
  code_t VARCHAR(64) NOT NULL,
  sect_design VARCHAR(512) DEFAULT NULL,
  pri_out_t DOUBLE DEFAULT NULL,
  PRIMARY KEY (code_d, code_t)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_sme (
  codsme VARCHAR(32) NOT NULL,
  mois VARCHAR(64) DEFAULT NULL,
  statut VARCHAR(64) DEFAULT NULL,
  PRIMARY KEY (codsme)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_mission (
  id VARCHAR(128) NOT NULL,
  nature VARCHAR(512) DEFAULT NULL,
  lieu_mission VARCHAR(512) DEFAULT NULL,
  refnds VARCHAR(256) DEFAULT NULL,
  date_depart VARCHAR(64) DEFAULT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_malade (
  id VARCHAR(128) NOT NULL,
  date_constat VARCHAR(64) DEFAULT NULL,
  pathologie VARCHAR(512) DEFAULT NULL,
  obs TEXT,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_deserteur (
  id VARCHAR(128) NOT NULL,
  dat_desertion VARCHAR(64) DEFAULT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_stage_local (
  id VARCHAR(128) NOT NULL,
  nature_stage VARCHAR(512) DEFAULT NULL,
  lieu_stage VARCHAR(512) DEFAULT NULL,
  date_debut_fin VARCHAR(128) DEFAULT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_stage_ex (
  id VARCHAR(128) NOT NULL,
  nature_stage VARCHAR(512) DEFAULT NULL,
  lieu_stage VARCHAR(512) DEFAULT NULL,
  date_debut_fin VARCHAR(128) DEFAULT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_gain_perte (
  rowid BIGINT NOT NULL AUTO_INCREMENT,
  id VARCHAR(128) NOT NULL,
  grade VARCHAR(256) DEFAULT NULL,
  nom VARCHAR(512) DEFAULT NULL,
  objet VARCHAR(256) DEFAULT NULL,
  cdsme VARCHAR(32) DEFAULT NULL,
  observ VARCHAR(256) DEFAULT NULL,
  PRIMARY KEY (rowid),
  KEY idx_tab_gain_perte_cdsme (cdsme),
  KEY idx_tab_gain_perte_id (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tab_padmin (
  libelle VARCHAR(256) NOT NULL,
  compte_comme_present TINYINT(1) NOT NULL DEFAULT 0,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (libelle)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO tab_padmin (libelle, compte_comme_present, ordre_tri) VALUES
  ('Présent', 1, 1),
  ('Stage interne', 1, 2),
  ('Détaché', 0, 3),
  ('Congé maladie', 0, 4),
  ('Permission', 0, 5),
  ('Absent', 0, 6),
  ('En mission', 0, 7),
  ('Stage extérieur', 0, 8),
  ('Déserteur', 0, 9),
  ('Retraite', 0, 10),
  ('Suspendu', 0, 11);

SET FOREIGN_KEY_CHECKS = 1;

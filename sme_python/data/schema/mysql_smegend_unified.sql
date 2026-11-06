-- ---------------------------------------------------------------------------
-- Schéma MySQL unifié SME-Gend — entité centrale : agent
-- Remplace liste_generale + personne (doublon supprimé).
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- Sélectionner explicitement la base cible avant une exécution manuelle.
-- L'application utilise exclusivement la base indiquée dans sa configuration.

-- Tables obsolètes (ancien modèle double)
DROP TABLE IF EXISTS liste_generale;
DROP TABLE IF EXISTS personne_distinction;
DROP TABLE IF EXISTS personne_diplome;
DROP TABLE IF EXISTS mouvement_effectif;
DROP TABLE IF EXISTS evenement_deserteur;
DROP TABLE IF EXISTS evenement_malade;
DROP TABLE IF EXISTS mission_operationnelle;
DROP TABLE IF EXISTS parcours_stage;
DROP TABLE IF EXISTS carriere_situation;
DROP TABLE IF EXISTS personne;

-- ---------------------------------------------------------------------------
-- Référentiels
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ref_structure (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  designation VARCHAR(512) DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  famille_unite VARCHAR(64) DEFAULT NULL COMMENT 'Typologie : DIRECTION, REGION, ECOLE…',
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_structure_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_grade (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  libelle VARCHAR(512) DEFAULT NULL,
  pref_ord INT DEFAULT NULL,
  effectif_g INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_grade_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_position_admin (
  id BIGINT NOT NULL AUTO_INCREMENT,
  libelle VARCHAR(256) NOT NULL,
  compte_comme_present TINYINT(1) NOT NULL DEFAULT 0,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_position_admin_libelle (libelle)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_fonction (
  id BIGINT NOT NULL AUTO_INCREMENT,
  libelle VARCHAR(512) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_fonction_libelle (libelle(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_fonction_structure (
  id BIGINT NOT NULL AUTO_INCREMENT,
  structure_id BIGINT NOT NULL,
  fonction_id BIGINT NOT NULL,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_fonction_struct (structure_id, fonction_id),
  CONSTRAINT fk_rfs_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_rfs_fonction FOREIGN KEY (fonction_id) REFERENCES ref_fonction (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_subdivision_noeud (
  id BIGINT NOT NULL AUTO_INCREMENT,
  parent_id BIGINT DEFAULT NULL,
  code VARCHAR(64) NOT NULL,
  libelle VARCHAR(512) DEFAULT NULL,
  pri_out DOUBLE DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_subdiv_noeud_parent_code (parent_id, code),
  CONSTRAINT fk_subdiv_noeud_parent FOREIGN KEY (parent_id) REFERENCES ref_subdivision_noeud (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_subdivision_arme (
  id BIGINT NOT NULL AUTO_INCREMENT,
  structure_id BIGINT NOT NULL,
  noeud_feuille_id BIGINT NOT NULL,
  libelle VARCHAR(512) DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_subdiv_arme_struct_feuille (structure_id, noeud_feuille_id),
  CONSTRAINT fk_rsa_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_rsa_noeud FOREIGN KEY (noeud_feuille_id) REFERENCES ref_subdivision_noeud (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_type_stage (
  code VARCHAR(32) NOT NULL,
  libelle VARCHAR(256) DEFAULT NULL,
  PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO ref_type_stage (code, libelle) VALUES
  ('LOCAL', 'Stage local'),
  ('EXTERIEUR', 'Stage extérieur');

CREATE TABLE IF NOT EXISTS ref_medaille (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  libelle_court VARCHAR(256) DEFAULT NULL,
  libelle_long VARCHAR(512) DEFAULT NULL,
  categorie VARCHAR(128) DEFAULT NULL,
  ordre_preseance INT DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_medaille_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_decoration (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  libelle_court VARCHAR(256) DEFAULT NULL,
  libelle_long VARCHAR(512) DEFAULT NULL,
  categorie VARCHAR(128) DEFAULT NULL,
  ordre_preseance INT DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_decoration_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ref_diplome (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  libelle_court VARCHAR(256) DEFAULT NULL,
  libelle_long VARCHAR(512) DEFAULT NULL,
  categorie VARCHAR(128) DEFAULT NULL,
  niveau VARCHAR(128) DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_diplome_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Positions administratives par défaut (ref_position_admin)
INSERT IGNORE INTO ref_position_admin (libelle, compte_comme_present, ordre_tri) VALUES
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

-- ---------------------------------------------------------------------------
-- Temps & import
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sme_periode (
  id BIGINT NOT NULL AUTO_INCREMENT,
  annee_mois CHAR(7) NOT NULL,
  date_dernier_jour_mois DATE NOT NULL,
  codsme VARCHAR(32) DEFAULT NULL,
  statut VARCHAR(64) DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sme_periode_annee_mois (annee_mois)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS import_lot (
  id BIGINT NOT NULL AUTO_INCREMENT,
  sme_periode_id BIGINT DEFAULT NULL,
  chemin_fichier VARCHAR(1024) DEFAULT NULL,
  checksum VARCHAR(128) DEFAULT NULL,
  importe_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_import_lot_periode (sme_periode_id),
  CONSTRAINT fk_import_lot_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS etl_erreur (
  id BIGINT NOT NULL AUTO_INCREMENT,
  import_lot_id BIGINT DEFAULT NULL,
  table_cible VARCHAR(128) DEFAULT NULL,
  cle_legacy VARCHAR(512) DEFAULT NULL,
  message TEXT,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_etl_erreur_lot (import_lot_id),
  CONSTRAINT fk_etl_erreur_lot FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Agent (identité durable)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent (
  id BIGINT NOT NULL AUTO_INCREMENT,
  legacy_access_id VARCHAR(128) NOT NULL COMMENT 'ListeGenerale.Id Access',
  noms VARCHAR(512) DEFAULT NULL,
  date_naissance DATE DEFAULT NULL,
  lieu_naissance VARCHAR(256) DEFAULT NULL,
  sexe CHAR(1) DEFAULT NULL COMMENT 'M = masculin, F = féminin',
  telephone VARCHAR(64) DEFAULT NULL,
  adresse_residence VARCHAR(512) DEFAULT NULL,
  matricule_militaire VARCHAR(128) DEFAULT NULL,
  matricule_solde VARCHAR(128) DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_agent_legacy_access (legacy_access_id),
  KEY idx_agent_matricule (matricule_militaire),
  KEY idx_agent_noms (noms(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Situation de carrière (snapshot courant + historique par période)
-- ---------------------------------------------------------------------------

-- Anciennes identités rapprochées : conservées pour l'audit et les réimports.
CREATE TABLE IF NOT EXISTS agent_identity_alias (
 alias_legacy_id VARCHAR(128) NOT NULL,
 canonical_agent_id BIGINT NOT NULL,
 source_agent_id BIGINT NOT NULL,
 source_snapshot JSON NOT NULL,
 merged_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 merged_by VARCHAR(128) NOT NULL,
 reason TEXT NOT NULL,
 PRIMARY KEY (alias_legacy_id),
 KEY idx_identity_alias_canonical (canonical_agent_id),
 CONSTRAINT fk_identity_alias_agent FOREIGN KEY (canonical_agent_id) REFERENCES agent(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS carriere_situation (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  est_actuelle TINYINT(1) NOT NULL DEFAULT 1 COMMENT '1 = ligne affichée dans la liste générale',
  grade_libelle VARCHAR(256) DEFAULT NULL,
  date_grade DATE DEFAULT NULL,
  date_service VARCHAR(64) DEFAULT NULL,
  structure_code VARCHAR(64) DEFAULT NULL,
  position_admin_libelle VARCHAR(256) DEFAULT NULL,
  fonction_libelle VARCHAR(512) DEFAULT NULL,
  subdivision_arme_libelle VARCHAR(512) DEFAULT NULL,
  diplome_libelle VARCHAR(512) DEFAULT NULL,
  date_obtention VARCHAR(64) DEFAULT NULL,
  ndecision VARCHAR(128) DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  presseance INT DEFAULT NULL,
  obs TEXT,
  structure_id BIGINT DEFAULT NULL,
  grade_id BIGINT DEFAULT NULL,
  position_admin_id BIGINT DEFAULT NULL,
  fonction_id BIGINT DEFAULT NULL,
  subdivision_arme_id BIGINT DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  date_debut DATE DEFAULT NULL,
  date_fin DATE DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_carriere_agent_actuelle (agent_id, est_actuelle),
  UNIQUE KEY uk_carriere_agent_periode (agent_id, sme_periode_id),
  KEY idx_carriere_ordre (ordre_tri, agent_id),
  KEY idx_carriere_structure (structure_code),
  KEY idx_carriere_fonction (fonction_id),
  CONSTRAINT fk_carriere_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_carriere_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_carriere_grade FOREIGN KEY (grade_id) REFERENCES ref_grade (id),
  CONSTRAINT fk_carriere_padmin FOREIGN KEY (position_admin_id) REFERENCES ref_position_admin (id),
  CONSTRAINT fk_carriere_fonction FOREIGN KEY (fonction_id) REFERENCES ref_fonction (id),
  CONSTRAINT fk_carriere_subdiv FOREIGN KEY (subdivision_arme_id) REFERENCES ref_subdivision_arme (id),
  CONSTRAINT fk_carriere_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id),
  CONSTRAINT fk_carriere_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_diplome (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  ref_diplome_id BIGINT DEFAULT NULL,
  categorie VARCHAR(32) DEFAULT NULL COMMENT 'MILITAIRE ou CIVIL',
  libelle_libre VARCHAR(512) DEFAULT NULL,
  date_obtention DATE DEFAULT NULL,
  lieu_obtention VARCHAR(256) DEFAULT NULL,
  mention VARCHAR(256) DEFAULT NULL,
  numero_dossier VARCHAR(128) DEFAULT NULL,
  etablissement VARCHAR(512) DEFAULT NULL,
  obs TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_agent_diplome_agent (agent_id, date_obtention),
  CONSTRAINT fk_agent_diplome_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_diplome_ref FOREIGN KEY (ref_diplome_id) REFERENCES ref_diplome (id),
  CONSTRAINT fk_agent_diplome_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_passation_grade (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  grade_id BIGINT DEFAULT NULL,
  grade_libelle VARCHAR(256) NOT NULL,
  date_passation DATE DEFAULT NULL,
  ndecision VARCHAR(128) DEFAULT NULL,
  obs TEXT,
  PRIMARY KEY (id),
  KEY idx_passation_agent_date (agent_id, date_passation),
  CONSTRAINT fk_passation_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_passation_grade FOREIGN KEY (grade_id) REFERENCES ref_grade (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_affectation (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  structure_code VARCHAR(64) NOT NULL,
  structure_id BIGINT DEFAULT NULL,
  fonction_libelle VARCHAR(512) DEFAULT NULL,
  subdivision_arme_libelle VARCHAR(256) DEFAULT NULL,
  date_affectation DATE NOT NULL,
  sme_periode_mois VARCHAR(7) DEFAULT NULL COMMENT 'YYYY-MM',
  obs TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_affectation_agent_date (agent_id, date_affectation),
  CONSTRAINT fk_affectation_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_affectation_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_affectation_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS parcours_stage (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  type_stage_code VARCHAR(32) NOT NULL,
  session VARCHAR(256) DEFAULT NULL,
  date_debut DATE DEFAULT NULL,
  date_fin DATE DEFAULT NULL,
  lieu VARCHAR(512) DEFAULT NULL,
  nature_libelle VARCHAR(512) DEFAULT NULL,
  reference_dossier VARCHAR(256) DEFAULT NULL,
  source_ligne_legacy VARCHAR(32) DEFAULT NULL,
  observation TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_parcours_agent (agent_id, date_debut),
  KEY idx_parcours_periode (sme_periode_id),
  CONSTRAINT fk_parcours_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_parcours_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_parcours_type FOREIGN KEY (type_stage_code) REFERENCES ref_type_stage (code),
  CONSTRAINT fk_parcours_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mouvement_effectif (
  id BIGINT NOT NULL AUTO_INCREMENT,
  sme_periode_id BIGINT NOT NULL,
  agent_id BIGINT NOT NULL,
  identifiant_ligne VARCHAR(256) DEFAULT NULL,
  grade_snapshot VARCHAR(256) DEFAULT NULL,
  nom_snapshot VARCHAR(512) DEFAULT NULL,
  type_mouvement VARCHAR(64) DEFAULT NULL,
  categorie_mouvement VARCHAR(16) DEFAULT NULL COMMENT 'GAIN, PERTE',
  motif VARCHAR(64) DEFAULT NULL COMMENT 'DECES, AJOUT, …',
  cdsme_derive VARCHAR(32) DEFAULT NULL,
  observ VARCHAR(256) DEFAULT NULL,
  date_reference DATE DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_mvt_periode (sme_periode_id),
  KEY idx_mvt_agent (agent_id, sme_periode_id),
  CONSTRAINT fk_mvt_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_mvt_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_mvt_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_distinction (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  ref_medaille_id BIGINT DEFAULT NULL,
  ref_decoration_id BIGINT DEFAULT NULL,
  date_attribution DATE DEFAULT NULL,
  date_publication_journal DATE DEFAULT NULL,
  numero_arrete VARCHAR(128) DEFAULT NULL,
  date_arrete DATE DEFAULT NULL,
  citation TEXT,
  source VARCHAR(64) DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_distinction_agent (agent_id, date_attribution),
  CONSTRAINT fk_distinction_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_distinction_med FOREIGN KEY (ref_medaille_id) REFERENCES ref_medaille (id),
  CONSTRAINT fk_distinction_dec FOREIGN KEY (ref_decoration_id) REFERENCES ref_decoration (id),
  CONSTRAINT fk_distinction_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id),
  CONSTRAINT chk_distinction_xor CHECK (
    (ref_medaille_id IS NOT NULL AND ref_decoration_id IS NULL)
    OR (ref_medaille_id IS NULL AND ref_decoration_id IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Événements numériques par agent (voir mysql_numerique_agent_v2.sql)
CREATE TABLE IF NOT EXISTS agent_mission (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  date_debut DATE DEFAULT NULL,
  date_fin DATE DEFAULT NULL,
  nature_libelle VARCHAR(512) DEFAULT NULL,
  lieu VARCHAR(512) DEFAULT NULL,
  reference_dossier VARCHAR(256) DEFAULT NULL,
  observation TEXT,
  source_ligne_legacy VARCHAR(128) DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_agent_mission_agent (agent_id, date_debut),
  CONSTRAINT fk_agent_mission_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_mission_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_agent_mission_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_malade (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  date_debut DATE NOT NULL,
  date_fin DATE DEFAULT NULL,
  pathologie VARCHAR(512) DEFAULT NULL,
  observation TEXT,
  source_ligne_legacy VARCHAR(128) DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_agent_malade_agent (agent_id, date_debut),
  CONSTRAINT fk_agent_malade_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_malade_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_agent_malade_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_desertion (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  date_desertion DATE NOT NULL,
  reference_dossier VARCHAR(256) DEFAULT NULL,
  observation TEXT,
  source_ligne_legacy VARCHAR(128) DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_agent_desertion_agent (agent_id, date_desertion),
  CONSTRAINT fk_agent_desertion_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_desertion_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_agent_desertion_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Configuration multi-poste (voir aussi data/schema/mysql_app_config.sql)
CREATE TABLE IF NOT EXISTS app_config (
  config_key VARCHAR(64) NOT NULL,
  payload JSON NOT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  updated_by VARCHAR(128) DEFAULT NULL,
  PRIMARY KEY (config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_user_preference (
  username VARCHAR(64) NOT NULL,
  pref_key VARCHAR(64) NOT NULL,
  payload JSON NOT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (username, pref_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS career_alert_ack (
  agent_legacy_id VARCHAR(128) NOT NULL,
  alert_code VARCHAR(64) NOT NULL,
  mois VARCHAR(7) NOT NULL DEFAULT '',
  acknowledged_by VARCHAR(128) DEFAULT NULL,
  acknowledged_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  note VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (agent_legacy_id, alert_code, mois)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Acquittement d'un constat précis. Les anciens acquittements sans empreinte
-- restent dans career_alert_ack ; leur contenu ne peut pas être reconstitué.
-- Ne pas les convertir en acquittements de constats actuels.
CREATE TABLE IF NOT EXISTS career_alert_ack_occurrence (
  agent_legacy_id VARCHAR(128) NOT NULL,
  alert_code VARCHAR(64) NOT NULL,
  mois VARCHAR(7) NOT NULL DEFAULT '',
  fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  acknowledged_by VARCHAR(128) DEFAULT NULL,
  acknowledged_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  note VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (agent_legacy_id, alert_code, mois, fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_journal (
  id BIGINT NOT NULL AUTO_INCREMENT,
  ts_utc DATETIME(3) NOT NULL,
  action VARCHAR(64) NOT NULL,
  actor VARCHAR(128) DEFAULT NULL,
  module VARCHAR(64) DEFAULT NULL,
  personnel_id VARCHAR(64) DEFAULT NULL,
  sme_mois_context CHAR(7) DEFAULT NULL,
  payload JSON DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_audit_ts (ts_utc),
  KEY idx_audit_action (action),
  KEY idx_audit_actor (actor)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS structure_organigram_node (
  id BIGINT NOT NULL AUTO_INCREMENT,
  structure_id BIGINT NOT NULL,
  parent_id BIGINT DEFAULT NULL,
  titre VARCHAR(512) NOT NULL,
  titulaire VARCHAR(512) DEFAULT NULL,
  rsa_id BIGINT DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  pos_x DOUBLE DEFAULT NULL,
  pos_y DOUBLE DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  KEY idx_sorg_structure (structure_id),
  KEY idx_sorg_parent (parent_id),
  CONSTRAINT fk_sorg_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_sorg_parent FOREIGN KEY (parent_id) REFERENCES structure_organigram_node (id),
  CONSTRAINT fk_sorg_rsa FOREIGN KEY (rsa_id) REFERENCES ref_subdivision_arme (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

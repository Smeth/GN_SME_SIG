-- ---------------------------------------------------------------------------
-- Schéma MySQL — base existante ``smegend`` (ne supprime pas la base)
-- Même modèle que mysql_sme_cible.sql ; compagnon :
--   docs/MODELE_DONNEES_MYSQL_CIBLE.md
--
-- Prérequis : CREATE DATABASE smegend déjà fait (utf8mb4).
-- Données : import Access → MySQL via l'application ou scripts d'import.
--   --archives --database smegend --out donnees.sql
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

USE smegend;

-- ---------------------------------------------------------------------------
-- Référentiels (équivalent TabStructure, TabGrade, hiérarchie TabSec* → arbre,
-- tab_padmin…)
-- ---------------------------------------------------------------------------

CREATE TABLE ref_structure (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  designation VARCHAR(512) DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_structure_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ref_grade (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(64) NOT NULL,
  libelle VARCHAR(512) DEFAULT NULL,
  pref_ord INT DEFAULT NULL,
  effectif_g INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_grade_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ref_position_admin (
  id BIGINT NOT NULL AUTO_INCREMENT,
  libelle VARCHAR(256) NOT NULL,
  compte_comme_present TINYINT(1) NOT NULL DEFAULT 0,
  ordre_tri INT DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_position_admin_libelle (libelle)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ref_fonction (
  id BIGINT NOT NULL AUTO_INCREMENT,
  libelle VARCHAR(512) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_fonction_libelle (libelle(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ref_subdivision_noeud (
  id BIGINT NOT NULL AUTO_INCREMENT,
  parent_id BIGINT DEFAULT NULL COMMENT 'NULL uniquement pour la racine technique id=1',
  code VARCHAR(64) NOT NULL COMMENT 'Code du niveau (U, D ou T selon la profondeur sous la racine)',
  libelle VARCHAR(512) DEFAULT NULL,
  pri_out DOUBLE DEFAULT NULL COMMENT 'Priorité / ordre (héritage PriOutD / PriOutT)',
  PRIMARY KEY (id),
  UNIQUE KEY uk_subdiv_noeud_parent_code (parent_id, code),
  CONSTRAINT fk_subdiv_noeud_parent FOREIGN KEY (parent_id) REFERENCES ref_subdivision_noeud (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ref_subdivision_arme (
  id BIGINT NOT NULL AUTO_INCREMENT,
  structure_id BIGINT NOT NULL COMMENT 'Structure d''appartenance',
  noeud_feuille_id BIGINT NOT NULL COMMENT 'Nœud feuille (niveau T) dans ref_subdivision_noeud',
  libelle VARCHAR(512) DEFAULT NULL COMMENT 'Libellé affichage (souvent redondant avec le nœud feuille)',
  ordre_tri INT DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ref_subdivision_arme_struct_feuille (structure_id, noeud_feuille_id),
  CONSTRAINT fk_rsa_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_rsa_noeud_feuille FOREIGN KEY (noeud_feuille_id) REFERENCES ref_subdivision_noeud (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE ref_type_stage (
  code VARCHAR(32) NOT NULL,
  libelle VARCHAR(256) DEFAULT NULL,
  PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO ref_type_stage (code, libelle) VALUES
  ('LOCAL', 'Stage local'),
  ('EXTERIEUR', 'Stage extérieur');

CREATE TABLE ref_medaille (
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

CREATE TABLE ref_decoration (
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

CREATE TABLE ref_diplome (
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

CREATE TABLE sme_periode (
  id BIGINT NOT NULL AUTO_INCREMENT,
  annee_mois CHAR(7) NOT NULL COMMENT 'format YYYY-MM',
  date_dernier_jour_mois DATE NOT NULL,
  codsme VARCHAR(32) DEFAULT NULL COMMENT 'clé TabSme héritée si utile MMYYYY',
  statut VARCHAR(64) DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sme_periode_annee_mois (annee_mois)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE import_lot (
  id BIGINT NOT NULL AUTO_INCREMENT,
  sme_periode_id BIGINT NOT NULL,
  chemin_fichier VARCHAR(1024) DEFAULT NULL,
  checksum VARCHAR(128) DEFAULT NULL,
  importe_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_import_lot_periode (sme_periode_id),
  CONSTRAINT fk_import_lot_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE etl_erreur (
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

CREATE TABLE personne (
  id BIGINT NOT NULL AUTO_INCREMENT,
  legacy_access_id VARCHAR(128) NOT NULL COMMENT 'ex ListeGenerale.Id / id_acces',
  nom_affichage VARCHAR(512) DEFAULT NULL,
  prenoms VARCHAR(512) DEFAULT NULL,
  date_naissance DATE DEFAULT NULL,
  lieu_naissance VARCHAR(256) DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_personne_legacy_access (legacy_access_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personne_diplome (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
  ref_diplome_id BIGINT DEFAULT NULL,
  libelle_libre VARCHAR(512) DEFAULT NULL,
  date_obtention DATE DEFAULT NULL,
  lieu_obtention VARCHAR(256) DEFAULT NULL,
  mention VARCHAR(256) DEFAULT NULL,
  numero_dossier VARCHAR(128) DEFAULT NULL,
  etablissement VARCHAR(512) DEFAULT NULL,
  obs TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_personne_diplome_personne_date (personne_id, date_obtention),
  KEY idx_personne_diplome_ref (ref_diplome_id),
  CONSTRAINT fk_personne_diplome_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_personne_diplome_ref FOREIGN KEY (ref_diplome_id) REFERENCES ref_diplome (id),
  CONSTRAINT fk_personne_diplome_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id),
  CONSTRAINT chk_personne_diplome_ref_libelle_date CHECK (
    (ref_diplome_id IS NOT NULL)
    OR (libelle_libre IS NOT NULL AND CHAR_LENGTH(TRIM(libelle_libre)) > 0)
    OR (date_obtention IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE carriere_situation (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
  date_debut DATE NOT NULL,
  date_fin DATE DEFAULT NULL COMMENT 'dernier jour inclus de la période snapshot',
  structure_id BIGINT DEFAULT NULL,
  grade_id BIGINT DEFAULT NULL,
  position_admin_id BIGINT DEFAULT NULL,
  subdivision_arme_id BIGINT DEFAULT NULL COMMENT 'FK ref_subdivision_arme (subdivision d''arme)',
  subdivision_libelle_legacy VARCHAR(512) DEFAULT NULL COMMENT 'SubArme brute liste si pas de résolution U-D-T',
  fonction_id BIGINT DEFAULT NULL,
  matricule_militaire VARCHAR(128) DEFAULT NULL,
  matricule_solde VARCHAR(128) DEFAULT NULL,
  ndecision VARCHAR(128) DEFAULT NULL,
  diplome_libelle VARCHAR(512) DEFAULT NULL COMMENT 'Snapshot liste (hérité) ; dossier normalisé = personne_diplome',
  date_diplome_obtenu DATE DEFAULT NULL COMMENT 'Idem ; peut diverger des lignes personne_diplome si plusieurs diplômes',
  ordre_tri INT DEFAULT NULL,
  presseance INT DEFAULT NULL,
  obs TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  sme_periode_reference_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_carriere_personne_debut (personne_id, date_debut),
  KEY idx_carriere_personne_fin (personne_id, date_fin),
  KEY idx_carriere_periode_ref (sme_periode_reference_id),
  KEY idx_carriere_subdivision_arme (subdivision_arme_id),
  CONSTRAINT fk_carriere_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_carriere_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_carriere_grade FOREIGN KEY (grade_id) REFERENCES ref_grade (id),
  CONSTRAINT fk_carriere_padmin FOREIGN KEY (position_admin_id) REFERENCES ref_position_admin (id),
  CONSTRAINT fk_carriere_fonction FOREIGN KEY (fonction_id) REFERENCES ref_fonction (id),
  CONSTRAINT fk_carriere_subdivision_arme FOREIGN KEY (subdivision_arme_id) REFERENCES ref_subdivision_arme (id),
  CONSTRAINT fk_carriere_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id),
  CONSTRAINT fk_carriere_sme_ref FOREIGN KEY (sme_periode_reference_id) REFERENCES sme_periode (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE parcours_stage (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
  type_stage_code VARCHAR(32) NOT NULL,
  date_debut DATE DEFAULT NULL,
  date_fin DATE DEFAULT NULL,
  lieu VARCHAR(512) DEFAULT NULL,
  nature_libelle VARCHAR(512) DEFAULT NULL,
  reference_dossier VARCHAR(256) DEFAULT NULL,
  source_ligne_legacy VARCHAR(32) DEFAULT NULL,
  observation TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_parcours_stage_personne (personne_id, date_debut),
  CONSTRAINT fk_parcours_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_parcours_type FOREIGN KEY (type_stage_code) REFERENCES ref_type_stage (code),
  CONSTRAINT fk_parcours_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE mission_operationnelle (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
  nature VARCHAR(512) DEFAULT NULL,
  lieu_mission VARCHAR(512) DEFAULT NULL,
  refnds VARCHAR(256) DEFAULT NULL,
  date_depart DATE DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_mission_personne (personne_id, date_depart),
  CONSTRAINT fk_mission_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_mission_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE evenement_malade (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
  date_constat DATE DEFAULT NULL,
  pathologie VARCHAR(512) DEFAULT NULL,
  obs TEXT,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_malade_personne (personne_id),
  CONSTRAINT fk_malade_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_malade_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE evenement_deserteur (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
  date_desertion DATE DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_deserteur_personne (personne_id),
  CONSTRAINT fk_deserteur_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_deserteur_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE mouvement_effectif (
  id BIGINT NOT NULL AUTO_INCREMENT,
  sme_periode_id BIGINT NOT NULL,
  personne_id BIGINT NOT NULL,
  identifiant_ligne VARCHAR(256) DEFAULT NULL,
  grade_snapshot VARCHAR(256) DEFAULT NULL,
  nom_snapshot VARCHAR(512) DEFAULT NULL,
  type_mouvement VARCHAR(64) DEFAULT NULL COMMENT 'GAIN, PERTE, etc.',
  cdsme_derive VARCHAR(32) DEFAULT NULL,
  observ VARCHAR(256) DEFAULT NULL,
  date_reference DATE DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_mvt_periode (sme_periode_id),
  KEY idx_mvt_personne_periode (personne_id, sme_periode_id),
  CONSTRAINT fk_mvt_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_mvt_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_mvt_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE personne_distinction (
  id BIGINT NOT NULL AUTO_INCREMENT,
  personne_id BIGINT NOT NULL,
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
  KEY idx_distinction_personne_date (personne_id, date_attribution),
  CONSTRAINT fk_distinction_personne FOREIGN KEY (personne_id) REFERENCES personne (id),
  CONSTRAINT fk_distinction_med FOREIGN KEY (ref_medaille_id) REFERENCES ref_medaille (id),
  CONSTRAINT fk_distinction_dec FOREIGN KEY (ref_decoration_id) REFERENCES ref_decoration (id),
  CONSTRAINT fk_distinction_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id),
  CONSTRAINT chk_distinction_xor CHECK (
    (ref_medaille_id IS NOT NULL AND ref_decoration_id IS NULL)
    OR (ref_medaille_id IS NULL AND ref_decoration_id IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------------
-- Situation numérique v2 — modèle agent-centré (remplace tab_mission / tab_malade / …)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_mission (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL COMMENT 'Période SME du snapshot importé',
  date_debut DATE DEFAULT NULL,
  date_fin DATE DEFAULT NULL,
  nature_libelle VARCHAR(512) DEFAULT NULL,
  lieu VARCHAR(512) DEFAULT NULL,
  reference_dossier VARCHAR(256) DEFAULT NULL,
  observation TEXT,
  source_ligne_legacy VARCHAR(128) DEFAULT NULL COMMENT 'ListeGenerale.Id Access',
  import_lot_id BIGINT DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_agent_mission_agent (agent_id, date_debut),
  KEY idx_agent_mission_periode (sme_periode_id),
  CONSTRAINT fk_agent_mission_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_mission_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_agent_mission_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_malade (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  date_debut DATE NOT NULL COMMENT 'Date constat / début indisponibilité',
  date_fin DATE DEFAULT NULL,
  pathologie VARCHAR(512) DEFAULT NULL,
  observation TEXT,
  source_ligne_legacy VARCHAR(128) DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_agent_malade_agent (agent_id, date_debut),
  KEY idx_agent_malade_periode (sme_periode_id),
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
  UNIQUE KEY uk_agent_desertion_legacy (source_ligne_legacy),
  KEY idx_agent_desertion_agent (agent_id, date_desertion),
  CONSTRAINT fk_agent_desertion_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_desertion_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_agent_desertion_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE mouvement_effectif
  ADD COLUMN categorie_mouvement VARCHAR(16) DEFAULT NULL
    COMMENT 'GAIN=nouvel agent, PERTE=sortie (décès, …)' AFTER type_mouvement;

ALTER TABLE mouvement_effectif
  ADD COLUMN motif VARCHAR(64) DEFAULT NULL
    COMMENT 'DECES, AJOUT, ERREUR, …' AFTER categorie_mouvement;

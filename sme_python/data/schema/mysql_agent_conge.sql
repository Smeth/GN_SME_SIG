-- Registre congés (complément position admin CongéAdmin / CongéExp)

CREATE TABLE IF NOT EXISTS agent_conge (
  id BIGINT NOT NULL AUTO_INCREMENT,
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT DEFAULT NULL,
  type_conge_code VARCHAR(32) NOT NULL COMMENT 'ADMIN, EXPECTATIVE, AUTRE',
  date_debut DATE NOT NULL,
  date_fin DATE DEFAULT NULL,
  quota_jours DECIMAL(7, 2) DEFAULT NULL COMMENT 'Jours ouvrés / quota saisi',
  observation TEXT,
  source_code VARCHAR(32) NOT NULL DEFAULT 'saisie'
    COMMENT 'import_padmin, import_access, saisie',
  source_ligne_legacy VARCHAR(128) DEFAULT NULL,
  import_lot_id BIGINT DEFAULT NULL,
  cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_conge_agent_periode_legacy (agent_id, sme_periode_id, source_ligne_legacy),
  KEY idx_agent_conge_agent (agent_id, date_debut),
  KEY idx_agent_conge_periode (sme_periode_id),
  CONSTRAINT fk_agent_conge_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_agent_conge_periode FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id),
  CONSTRAINT fk_agent_conge_import FOREIGN KEY (import_lot_id) REFERENCES import_lot (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

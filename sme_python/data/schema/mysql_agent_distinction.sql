-- Médailles, décorations / ordres, attributions agent (hors archives SME Access).

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
  CONSTRAINT chk_distinction_xor CHECK (
    (ref_medaille_id IS NOT NULL AND ref_decoration_id IS NULL)
    OR (ref_medaille_id IS NULL AND ref_decoration_id IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

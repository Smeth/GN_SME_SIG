-- Real dated membership changes are distinct from monthly Access observations.
CREATE TABLE IF NOT EXISTS agent_effectif_movement (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  agent_id BIGINT NOT NULL,
  category VARCHAR(8) NOT NULL,
  movement_kind VARCHAR(64) NOT NULL,
  effective_date DATE NOT NULL,
  recorded_at DATETIME(6) NOT NULL,
  recorded_by VARCHAR(128) NOT NULL,
  reference_note VARCHAR(256) NULL,
  details TEXT NULL,
  version BIGINT NOT NULL DEFAULT 1,
  UNIQUE KEY uk_effectif_movement_agent_date (agent_id,effective_date),
  KEY idx_effectif_movement_date (effective_date,id),
  CONSTRAINT fk_effectif_movement_agent FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE,
  CONSTRAINT chk_effectif_movement_category CHECK (category IN ('GAIN','PERTE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sme_month_membership (
  agent_id BIGINT NOT NULL,
  sme_periode_id BIGINT NOT NULL,
  is_member TINYINT NOT NULL,
  baseline_member TINYINT NOT NULL DEFAULT 1,
  event_id BIGINT NULL,
  updated_at DATETIME(6) NOT NULL,
  PRIMARY KEY (agent_id,sme_periode_id),
  KEY idx_membership_period (sme_periode_id,is_member,agent_id),
  CONSTRAINT fk_membership_agent FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE,
  CONSTRAINT fk_membership_period FOREIGN KEY (sme_periode_id) REFERENCES sme_periode(id) ON DELETE CASCADE,
  CONSTRAINT fk_membership_event FOREIGN KEY (event_id) REFERENCES agent_effectif_movement(id) ON DELETE SET NULL,
  CONSTRAINT chk_membership_value CHECK (is_member IN (0,1)),
  CONSTRAINT chk_membership_baseline CHECK (baseline_member IN (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

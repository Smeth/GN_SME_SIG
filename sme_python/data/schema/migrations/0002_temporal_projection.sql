-- Additive temporal metadata. Event registers remain authoritative.
-- Applied only by the explicit administrative schema migration command.
CREATE TABLE IF NOT EXISTS sme_temporal_change (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  change_uid CHAR(32) NOT NULL,
  agent_id BIGINT NOT NULL,
  source_table VARCHAR(64) NOT NULL,
  source_id BIGINT NOT NULL,
  mutation VARCHAR(32) NOT NULL,
  effective_from DATE NULL,
  effective_until DATE NULL,
  recorded_at DATETIME(6) NOT NULL,
  actor VARCHAR(128) NOT NULL,
  reason TEXT NULL,
  before_state JSON NULL,
  after_state JSON NULL,
  provenance JSON NULL,
  UNIQUE KEY uk_sme_temporal_change_uid (change_uid),
  KEY idx_sme_temporal_agent_date (agent_id,effective_from,id),
  KEY idx_sme_temporal_source (source_table,source_id,id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sme_projection_dirty (
  agent_id BIGINT NOT NULL,
  component VARCHAR(32) NOT NULL,
  effective_from DATE NOT NULL,
  requested_token CHAR(32) NOT NULL,
  requested_at DATETIME(6) NOT NULL,
  state VARCHAR(16) NOT NULL DEFAULT 'pending',
  processed_token CHAR(32) NULL,
  processed_at DATETIME(6) NULL,
  last_error TEXT NULL,
  PRIMARY KEY (agent_id,component),
  KEY idx_sme_projection_pending (state,requested_at),
  CONSTRAINT fk_sme_projection_agent FOREIGN KEY (agent_id) REFERENCES agent(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

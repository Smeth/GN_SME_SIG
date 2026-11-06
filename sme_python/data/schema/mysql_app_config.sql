-- Configuration applicative centralisée (multi-poste)
-- À exécuter sur la base smegend (idempotent).

SET NAMES utf8mb4;

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

-- Duplicate index errors are ignored by the idempotent schema initializer.
CREATE INDEX idx_audit_module_time ON audit_journal (module, ts_utc, id);
CREATE INDEX idx_audit_action_time ON audit_journal (action, ts_utc, id);

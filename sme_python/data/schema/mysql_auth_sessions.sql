-- Sessions applicatives actives (limite multi-postes, authentification MySQL)

CREATE TABLE IF NOT EXISTS auth_active_session (
  id BIGINT NOT NULL AUTO_INCREMENT,
  session_token VARCHAR(64) NOT NULL,
  username VARCHAR(64) NOT NULL,
  client_instance_id VARCHAR(64) NOT NULL,
  machine_label VARCHAR(256) DEFAULT NULL,
  session_rev INT NOT NULL DEFAULT 0,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expires_at DATETIME(3) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_auth_session_token (session_token),
  KEY idx_auth_session_user (username),
  KEY idx_auth_session_seen (last_seen_at),
  KEY idx_auth_session_client (username, client_instance_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

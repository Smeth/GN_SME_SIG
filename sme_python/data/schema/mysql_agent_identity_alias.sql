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

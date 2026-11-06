-- Acquittement des alertes qualité "parcours carrière" (multi-poste)
-- À exécuter sur la base smegend (idempotent).

SET NAMES utf8mb4;

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

-- Explicit migration only. Monthly exports and approvals are separate objects.
CREATE TABLE IF NOT EXISTS sme_publication_write_gate (
 id TINYINT NOT NULL PRIMARY KEY
) ENGINE=InnoDB;
INSERT IGNORE INTO sme_publication_write_gate(id) VALUES(1);

CREATE TABLE IF NOT EXISTS sme_publication_period (
 mois CHAR(7) NOT NULL PRIMARY KEY,
 next_revision INT UNSIGNED NOT NULL DEFAULT 1,
 current_validated_id BIGINT UNSIGNED NULL,
 version BIGINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sme_publication (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 revision_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 mois CHAR(7) NOT NULL,
 revision_no INT UNSIGNED NOT NULL,
 status ENUM('brouillon','valide','remplace') NOT NULL DEFAULT 'brouillon',
 version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL,
 created_by VARCHAR(128) NOT NULL,
 validated_at DATETIME(6) NULL,
 validated_by VARCHAR(128) NULL,
 validation_note TEXT NULL,
 replaced_by_id BIGINT UNSIGNED NULL,
 dataset_footprint CHAR(64) CHARACTER SET ascii NOT NULL,
 manifest JSON NOT NULL,
 UNIQUE KEY uk_publication_revision_id(revision_id),
 UNIQUE KEY uk_publication_month_revision(mois,revision_no),
 KEY idx_publication_month_status(mois,status,id),
 CONSTRAINT fk_publication_period FOREIGN KEY(mois) REFERENCES sme_publication_period(mois)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sme_publication_artifact (
 publication_id BIGINT UNSIGNED NOT NULL,
 artifact_name VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 byte_size BIGINT UNSIGNED NOT NULL,
 sha256 CHAR(64) CHARACTER SET ascii NOT NULL,
 chunk_count INT UNSIGNED NOT NULL,
 PRIMARY KEY(publication_id,artifact_name),
 CONSTRAINT fk_pub_artifact_publication FOREIGN KEY(publication_id) REFERENCES sme_publication(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sme_publication_chunk (
 publication_id BIGINT UNSIGNED NOT NULL,
 artifact_name VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 chunk_index INT UNSIGNED NOT NULL,
 content MEDIUMBLOB NOT NULL,
 PRIMARY KEY(publication_id,artifact_name,chunk_index),
 CONSTRAINT fk_pub_chunk_artifact FOREIGN KEY(publication_id,artifact_name)
 REFERENCES sme_publication_artifact(publication_id,artifact_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

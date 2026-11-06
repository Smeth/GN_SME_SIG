-- Organigramme hiérarchique par structure (postes + titulaires, arbre éditable).

CREATE TABLE IF NOT EXISTS structure_organigram_node (
  id BIGINT NOT NULL AUTO_INCREMENT,
  structure_id BIGINT NOT NULL,
  parent_id BIGINT DEFAULT NULL,
  titre VARCHAR(512) NOT NULL,
  titulaire VARCHAR(512) DEFAULT NULL,
  rsa_id BIGINT DEFAULT NULL,
  ordre_tri INT DEFAULT NULL,
  pos_x DOUBLE DEFAULT NULL,
  pos_y DOUBLE DEFAULT NULL,
  actif TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  KEY idx_sorg_structure (structure_id),
  KEY idx_sorg_parent (parent_id),
  CONSTRAINT fk_sorg_structure FOREIGN KEY (structure_id) REFERENCES ref_structure (id),
  CONSTRAINT fk_sorg_parent FOREIGN KEY (parent_id) REFERENCES structure_organigram_node (id),
  CONSTRAINT fk_sorg_rsa FOREIGN KEY (rsa_id) REFERENCES ref_subdivision_arme (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS avancement_regle (
  grade_cible VARCHAR(16) NOT NULL,
  grade_precedent VARCHAR(16) NOT NULL DEFAULT '',
  libelle VARCHAR(128) NOT NULL,
  annees_grade INT DEFAULT NULL,
  annees_grade_ecole INT DEFAULT NULL,
  annees_services INT DEFAULT NULL,
  age_min INT DEFAULT NULL,
  age_max INT DEFAULT NULL,
  diplomes VARCHAR(255) NOT NULL DEFAULT '',
  diplome_ou_superieur TINYINT(1) NOT NULL DEFAULT 0,
  inscription_tableau TINYINT(1) NOT NULL DEFAULT 1,
  voie VARCHAR(24) NOT NULL DEFAULT 'tableau',
  acte VARCHAR(24) NOT NULL DEFAULT '',
  non_codifie TINYINT(1) NOT NULL DEFAULT 0,
  condition_manuelle VARCHAR(32) NOT NULL DEFAULT '',
  PRIMARY KEY (grade_cible)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS avancement_campagne (
  id BIGINT NOT NULL AUTO_INCREMENT,
  annee INT NOT NULL,
  trimestre TINYINT NOT NULL DEFAULT 1,
  statut VARCHAR(16) NOT NULL DEFAULT 'ouverte',
  obs TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_avancement_campagne_periode (annee, trimestre)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS avancement_quota (
  id BIGINT NOT NULL AUTO_INCREMENT,
  campagne_id BIGINT NOT NULL,
  grade_cible VARCHAR(16) NOT NULL,
  quota INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_avancement_quota (campagne_id, grade_cible),
  CONSTRAINT fk_avancement_quota_campagne FOREIGN KEY (campagne_id)
    REFERENCES avancement_campagne (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS avancement_ligne (
  id BIGINT NOT NULL AUTO_INCREMENT,
  campagne_id BIGINT NOT NULL,
  agent_id BIGINT NOT NULL,
  grade_actuel VARCHAR(64) NOT NULL DEFAULT '',
  grade_cible VARCHAR(16) NOT NULL,
  voie VARCHAR(24) NOT NULL DEFAULT 'tableau',
  issu_ecole TINYINT(1) DEFAULT NULL,
  maniere_servir VARCHAR(16) DEFAULT NULL,
  temps_commandement TINYINT(1) DEFAULT NULL,
  statut VARCHAR(16) NOT NULL DEFAULT 'propose',
  date_nomination DATE DEFAULT NULL,
  reference_acte VARCHAR(128) DEFAULT NULL,
  obs TEXT,
  PRIMARY KEY (id),
  UNIQUE KEY uk_avancement_ligne (campagne_id, agent_id, grade_cible, voie),
  KEY idx_avancement_ligne_agent (agent_id),
  CONSTRAINT fk_avancement_ligne_campagne FOREIGN KEY (campagne_id)
    REFERENCES avancement_campagne (id) ON DELETE CASCADE,
  CONSTRAINT fk_avancement_ligne_agent FOREIGN KEY (agent_id)
    REFERENCES agent (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

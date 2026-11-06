-- Tâches workflow et notifications (multi-poste)
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS workflow_task (
  id BIGINT NOT NULL AUTO_INCREMENT,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  mois CHAR(7) DEFAULT NULL,
  task_kind VARCHAR(32) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'open',
  priority VARCHAR(16) NOT NULL DEFAULT 'normal',
  title VARCHAR(255) NOT NULL,
  detail TEXT,
  assigned_role VARCHAR(64) DEFAULT NULL,
  created_by VARCHAR(128) DEFAULT NULL,
  resolved_by VARCHAR(128) DEFAULT NULL,
  resolved_at DATETIME(3) DEFAULT NULL,
  PRIMARY KEY (id),
  KEY idx_wf_task_status (status),
  KEY idx_wf_task_mois (mois),
  KEY idx_wf_task_role (assigned_role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

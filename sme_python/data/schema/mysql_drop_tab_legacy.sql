-- Suppression des tables miroir Access (remplacées par ref_* / agent_* / mouvement_effectif).
-- Idempotent : exécuter après migration des données vers le modèle agent v2.

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS tab_gain_perte;
DROP TABLE IF EXISTS tab_mission;
DROP TABLE IF EXISTS tab_malade;
DROP TABLE IF EXISTS tab_deserteur;
DROP TABLE IF EXISTS tab_stage_local;
DROP TABLE IF EXISTS tab_stage_ex;
DROP TABLE IF EXISTS tab_sectrois;
DROP TABLE IF EXISTS tab_secdeux;
DROP TABLE IF EXISTS tab_secun;
DROP TABLE IF EXISTS tab_padmin;
DROP TABLE IF EXISTS tab_grade;
DROP TABLE IF EXISTS tab_structure;
DROP TABLE IF EXISTS tab_sme;

SET FOREIGN_KEY_CHECKS = 1;

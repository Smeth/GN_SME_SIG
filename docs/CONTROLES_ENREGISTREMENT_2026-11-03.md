# Correctifs des enregistrements — version 0.1.9

Les blocages identifiés dans les attestations sont corrigés.

- Le formatage des structures enregistre uniquement les structures.
- Les options du document enregistrent uniquement ville, signataire, validité et références.
- La présentation des noms et prénoms modifie uniquement la personne concernée.
- La configuration des limites enregistre uniquement les grades.
- La synchronisation regroupe les doublons correspondant au même grade et aux mêmes limites, et retire les lignes entièrement vides. Les grades distincts restent distincts.
- Les limites contradictoires et les lignes sans grade portant des valeurs sont signalées précisément, sans suppression automatique ni enregistrement partiel.

Les autres réglages, les champs supplémentaires et le contrôle de version entre postes sont conservés. La synchronisation demande confirmation avant de remplacer des modifications non enregistrées.

## Vérifications

150 tests ciblés hors base et 13 tests MySQL réussis. Après le dernier ajustement des éditeurs, 57 tests concernés ont été rejoués avec succès. Ces tests couvrent les alias et doublons, les grades vides, la conservation des autres sections, les conflits entre postes et l’annulation d’une synchronisation.

Les essais MySQL utilisent une base locale isolée. Les données de production et les limites réglementaires existantes n’ont pas été modifiées.

Les anciens diagnostics `verification-autres-enregistrements-20261103.txt` et `probe_attestation_save_scopes_20261103.py` reproduisent les blocages de la version précédente ; les nouveaux tests vérifient les corrections.

## Utilisation

Fermer l’ancienne application, extraire tout le ZIP 0.1.9, puis lancer `SME_Gendarmerie.exe`. Dans Configuration des limites, Synchroniser permet de regrouper les anciens doublons identiques. Si des limites sont contradictoires, corriger les lignes indiquées avant de recommencer.

Les chemins inspectés de Soit-transmis, Détachés et de la mise en page des présents n’utilisent pas cette validation des grades. Ce constat ciblé ne constitue pas un essai fonctionnel exhaustif de chaque écran.

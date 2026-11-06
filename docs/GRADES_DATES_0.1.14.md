# Grades enregistrés selon leur date — 0.1.14

Le mois affiché n’est plus une raison de refuser une passation dont la nomination appartient à un autre mois. La date de nomination pilote son inscription dans l’historique et le recalcul des situations mensuelles existantes.

## Comportement

- Depuis septembre 2026, saisir ADM au 01/10/2026 enregistre la passation pour octobre. Septembre conserve son grade applicable ; les situations existantes d’octobre et suivantes sont recalculées selon l’historique.
- Une saisie rétroactive recalcule les mois existants à partir de sa date. Une promotion ultérieure conserve sa priorité dans les mois suivants.
- Le traitement concerne la modification individuelle, les actions groupées et les modifications de fiche. L’ajout, la modification et la suppression d’une passation datée dans le dossier recalculent aussi les mois concernés.
- L’écran relit le grade, la date et la décision réellement applicables au mois consulté. Si cette situation diffère de la passation saisie, une confirmation indique la date et le mois de l’événement enregistré.
- Répéter une saisie mensuelle de grade identique avec la même date et la même décision ne crée pas de passation supplémentaire.
- Une opération qui concerne l’historique mais ne change pas le grade du mois consulté ne peut pas être annulée par l’annulation d’une cellule de ce mois ; corriger la passation depuis le dossier.

## Mois et archives

Les mois clôturés restent protégés. Un mois de nomination clôturé doit être rouvert pour permettre sa correction. Les autres archives clôturées ne sont pas réécrites par le recalcul.

Si la situation mensuelle cible n’existe pas encore, la passation reste enregistrée dans le dossier à sa date. L’application ne fabrique pas une fiche mensuelle complète ni un effectif à partir des données d’un autre mois. Les situations existantes sont mises à jour ; les autres pourront prendre en compte cet historique lors de leur création ou de leur resynchronisation.

## Vérification

76 tests ciblés hors base et 20 cas MySQL vérifiés sur des données fictives : nomination future depuis septembre, correction rétroactive depuis novembre, priorité des promotions ultérieures, protection des archives et des anciens formats de mois, mois cible absent, répétition de la même saisie et ajout/modification/suppression dans l’historique. Les parcours diplômes, fin de stage et autres actions groupées ont aussi été revérifiés.

Le dernier lot MySQL a validé 19 cas ; le vingtième a été rejoué avec succès après correction de sa fixture de test. Les essais utilisent des profils temporaires et une instance locale isolée, sans modification de production. Les ACL de ces profils sont simulées ; ils ne certifient pas les protections Windows.

Cette version inclut les correctifs antérieurs, dont la reprise des sessions de 0.1.13. Fermer l’ancienne application, extraire tout le ZIP, puis lancer SME_Gendarmerie.exe.

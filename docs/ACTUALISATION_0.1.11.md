# Actualisation et enregistrement — version 0.1.11

- Dans Édition SME, « Recharger » devient « Actualiser ». L’action invalide le cache et relit les données de la base en conservant les filtres et la sélection.
- L’actualisation de la liste générale invalide également le cache avant de relire les données.
- La fiche complète dispose d’un bouton Actualiser pour relire la fiche et les rubriques du dossier. Son en-tête et son insigne sont dessinés après la relecture de la situation. Les consultations historiques figées ne disposent pas de ce bouton.
- Après ajout, modification ou suppression d’un diplôme ou d’une passation dans la fiche complète, la liste parente est informée qu’elle doit se mettre à jour à la fermeture.
- Une modification mensuelle de grade est contrôlée dans la transaction, après le recalcul depuis les passations. Si le grade, la date de nomination ou la décision demandés ne sont pas retenus, l’écriture est annulée et un message demande de vérifier le mois et les passations. Une sauvegarde réussie ne doit plus masquer ce conflit jusqu’au rechargement.
- Un grade ne peut plus être vidé dans la situation mensuelle en laissant subsister son historique ; le message indique comment intervenir dans les passations.
- Un agent introuvable pendant la synchronisation déclenche une erreur ; la mise à jour ne continue pas sans son historique.

Actualiser relit les valeurs déjà enregistrées ; la validation d’une cellule ou d’un formulaire reste nécessaire pour enregistrer une modification. Les règles chronologiques de carrière et de sélection des diplômes sont conservées.

## Vérifications

59 tests ciblés hors base réussis et 3 tests MySQL sur une instance isolée. Les essais MySQL passent par le modèle Édition SME et le service de personnel réels : persistance et relecture d’un grade valide, refus et annulation complète d’une nomination future pour le mois consulté, refus et annulation d’une valeur remplacée par une passation plus récente. Aucun accès à une base de production.

Les tests utilisent des répertoires temporaires à droits hérités et une simulation des restrictions ACL dans leur profil jetable ; ils ne certifient pas les protections Windows.

Cette version inclut les corrections des versions précédentes. Fermer l’application, extraire tout le ZIP et lancer SME_Gendarmerie.exe.

# Correctifs des enregistrements — 0.1.10

Cette version inclut les corrections de la version 0.1.9 et corrige les trois risques supplémentaires confirmés.

- Détachés : une ligne sans sigle bloque la sauvegarde avec son numéro de ligne. Le brouillon reste affiché ; renseigner le sigle ou supprimer explicitement la ligne permet de reprendre.
- Un ordre non numérique est signalé au lieu d’être remplacé silencieusement.
- Toutes les lignes de titre déjà enregistrées sont affichées et conservées, y compris au-delà de trois titres et après déplacement d’une structure.
- Les champs supplémentaires de configuration sont conservés dans Soit-transmis et Détachés. Pour Détachés, cela comprend les options et les métadonnées de chaque structure, même après renommage ou déplacement.
- Les libellés de rechargement de Détachés indiquent désormais qu’il s’agit d’une configuration partagée entre les postes.

## Vérification

67 tests ciblés réussis : nouveaux cas de non-régression, conflits de configuration partagée et périmètre des détachés. Les nouveaux cas utilisent des données fictives et ne modifient aucune base de production. Le contrôle des modifications concurrentes reste en place.

Le correctif conserve les données encore présentes. Il ne recrée pas des titres ou des champs déjà perdus à la suite d’un enregistrement dans une ancienne version ; ceux-ci nécessitent une sauvegarde antérieure ou une nouvelle saisie.

## Installation

Fermer l’application, extraire l’intégralité du nouveau ZIP dans son dossier, puis lancer SME_Gendarmerie.exe.

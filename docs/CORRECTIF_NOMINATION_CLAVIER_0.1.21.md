Nomination individuelle : référence et saisie au clavier — 0.1.21

Le composant commun des dates ne référence plus les constantes Qt inexistantes
Key_Keypad0 et Key_Keypad9. Les chiffres du clavier principal et du pavé
numérique utilisent les touches numériques standard de Qt. Les séparateurs
comme « / » et les autres touches non numériques n'entraînent plus l'erreur
fatale signalée lors de la saisie d'une date.

La fenêtre de changement de grade individuel dans Édition SME affiche aussi
le champ facultatif « Ref/OG/Decret ». Ce champ était déjà présent dans la
passation groupée. La date de nomination, le grade et la référence sont
enregistrés ensemble, dans la situation applicable et l'historique des grades.
Une référence vide laisse la nouvelle nomination sans référence au lieu de
reprendre celle de l'ancien grade. Annuler la fenêtre n'enregistre rien.

Les règles des événements datés sont conservées : prise d'effet dans le bon
mois, recalcul des situations existantes y compris clôturées, priorité aux
événements plus récents et conservation d'une nomination future dans l'historique.
Les droits opérateur et les optimisations de recherche restent en place.

Les nouveaux tests exercent les véritables événements clavier, la fenêtre
individuelle, la référence facultative, l'annulation et l'enregistrement MySQL
avec un compte opérateur sur des schémas synthétiques jetables.

Le contrôle intégré du portable comporte désormais un septième scénario :
il ouvre la fenêtre de nomination et y saisit au clavier une date avec des
séparateurs et une référence. Les anciens contrôles d'import et d'ouverture
d'interface ne couvraient pas cette interaction précise.

Les données existantes sont conservées. Aucun vidage ni réimport n'est requis.
Livraison : dossier portable complet, sans archive ZIP de distribution.

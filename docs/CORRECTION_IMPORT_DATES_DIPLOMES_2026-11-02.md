# Import des dates et lieux de diplôme — V2

Le champ Access `date_obtention` peut contenir du texte, par exemple
`(22/08/2023 CONGO)`, alors que la date du diplôme dans le dossier MySQL
doit être une date calendaire ou NULL. Une conversion directe de cette valeur
par MySQL produit l'erreur 1292.

## Comportement corrigé

- `(22/08/2023 CONGO)` devient la date du 22 août 2023 et le lieu CONGO.
- Le texte d'origine est conservé dans les observations du diplôme.
- Les dates impossibles ou ambiguës, notamment `28/07/29 Congo`, conservent
  le diplôme avec une date non confirmée et une observation à vérifier.
  Aucun siècle n'est inventé. Le lieu exploitable est conservé.
- Le compte rendu signale ces anomalies sans interrompre l'import.
- Le dialogue permet de conserver une date importée non confirmée lors de
  la modification des autres informations du diplôme.
- Le réimport complète les informations manquantes des diplômes automatiques
  sans remplacer une date ou un lieu déjà renseigné.
- Le remplacement d'un mois compare les attestations textuelles sans convertir
  le texte Access en DATE dans SQL. Un diplôme encore attesté sur un autre mois
  garde ses observations quand sa provenance est réattribuée.

Les contrôles stricts MySQL restent actifs. Les erreurs techniques autres que
les anomalies de contenu ne sont pas masquées. Cette correction ne modifie pas
les archives Access originales et ne lance aucun réimport sur la base réelle.

## Vérification

Tests unitaires du parseur, du compte rendu et du dialogue. Tests sur MySQL
jetable pour l'erreur 1292, l'enregistrement des dates et lieux, l'idempotence
du réimport et la conservation des preuves mensuelles et observations.

Résultats : 2 006 tests unitaires réussis, 10 tests MySQL réussis et six
contrôles de l'exécutable compilé réussis. Les tests utilisent des profils
isolés et une instance MySQL jetable ; aucun import réel n'a été exécuté.

Livraison : V2 0.1.5, dossier complet sans ZIP dans
`sme_python/dist/releases/20261102T204437224986Z/SME_Gendarmerie`.

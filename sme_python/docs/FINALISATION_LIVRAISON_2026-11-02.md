# Finalisation de la livraison — 2 novembre 2026

> Ce compte rendu concerne la version **0.1.1**. La version **0.1.2** et ses
> quatre évolutions sont documentées dans `MAINTENANCE_AUDIT_HISTORIQUE_2026-11-02.md`.

## État des corrections

Les corrections postérieures à l'audit initial couvrent la révocation et le
registre des sessions, la trace transactionnelle du vidage mensuel, le mois
des événements d'import, la recherche du journal avec les identités fusionnées,
la conservation des brouillons et la distinction entre présence et stage interne.
Les positions administratives vides restent présentes avec un avertissement,
conformément au choix métier conservé.

La recherche du journal possède une pagination stable et un export intégral
des résultats filtrés. L'ouverture d'un dossier est tracée avec l'auteur,
l'identité et le mois. Le test d'intégration qui échouait sur ce dernier point
réussit également dans la campagne groupée finale.

## Vérifications natives de livraison

Les essais utilisent des profils jetables et une instance MySQL créée sous
`build`, avec port distinct de 3306, liaison sur la boucle locale et vérification
du répertoire du serveur avant les opérations. Aucune donnée métier de la base
réelle n'est utilisée ou modifiée.

Campagne finale ciblée, avec les ACL Windows natives :

| Groupe | Résultat |
|---|---:|
| Correctifs, journaux, brouillons, synchronisation, exports Word/PDF/Excel et restauration locale | 218 réussis |
| Journal paginé, sessions, présence, recherche sensible et purge mensuelle sur MySQL jetable | 46 réussis |
| Export/restauration Unicode et binaire, annulation transactionnelle, initialisation ciblée et concurrence | 4 réussis |
| Total de cette campagne ciblée | 268 réussis |

Preuves : `build/release_checks_829605ac6879425f9d9c5420d7be63d7/` contient
les trois sorties et `validation.json`. Le serveur jetable a été arrêté.
Cette campagne ne représente pas l'ensemble des tests historiques de restauration :
quatre cas ont été sélectionnés dans ce fichier, et 554 n'y ont pas été exécutés.

Le premier essai en environnement restreint avait 19 échecs : 18 liés aux ACL
Windows et un double de dépôt ne fournissant plus `table_exists`. Ce double a
été mis à jour. Les 218 cas réussissent ensuite avec les vraies protections
Windows, sans modifier les fonctions de protection de l'application.

Le kit de partage a été exécuté avec deux processus locaux : exclusion mutuelle
dans les deux sens, remplacement croisé, 50 publications, 59 lectures complètes,
refus des sessions altérées et libération du verrou après expiration. Six
reprises de renommage ont été observées. Ce résultat ne valide ni SMB réel,
ni deux postes physiques, ni la synchronisation MySQL entre ces postes.
Preuves : `build/recette_smb_locale_20261102_final/`.

Contrôle syntaxique : 457 fichiers Python applicatifs et scripts valides.
Le contrôle des espaces de fin et conflits de fusion par `git diff --check`
réussit sur le périmètre applicatif.

## Campagne unitaire générale après remise en cohérence

**1 932 cas unitaires uniques validés dans 231 modules, sans échec, erreur ou
cas ignoré dans le résultat consolidé.** Les essais utilisent les ACL Windows
natives. Le résultat est constitué d'une campagne générale, de sa continuation
après arrêt et de relances complètes des modules corrigés. L'inventaire final
collecté a été comparé exactement aux identifiants de tests des XML retenus.
Les anciens échecs sont conservés dans les profils d'origine ; ils ne sont pas
comptés comme des réussites supplémentaires.

Les ajustements de tests couvrent les chemins de gabarits indépendants du dossier
courant, le délai d'attente et le redémarrage du cache après invalidation, les
contrats de fraîcheur des exports, le refus de changer de configuration en
présence de brouillons et la simulation de la nouvelle API Qt sur l'ancien Qt.
La comparaison Excel vérifie les valeurs et l'ordre des lignes, sans dépendre
du libellé d'un avertissement de pandas. Le moteur optionnel `python-calamine`
0.9.0 a été installé uniquement sous `build/test_dependencies`.

Le fichier `build/finalisation_20261102/validation.json` identifie le XML retenu
pour chaque module, les contrôles MySQL et les empreintes de la livraison.
Les 218 tests de la campagne ciblée sont inclus dans les 1 932 cas unitaires ;
ils ne s'ajoutent pas à ce total. Les 50 cas MySQL ciblés sont distincts.

## Portable final vérifié

- Version : **0.1.1**, Windows x64.
- Archive : `dist/releases/20261102T172711476221Z/SME_Gendarmerie_portable.zip`.
- Taille : **118 631 301 octets**.
- SHA-256 du ZIP : `e4b286210306bd0c21b1f335b8d6f0cb1bd21eb3a84f81d63a84c63534543200`.
- SHA-256 des sources Python : `c622dbffd736de46ac4f2c1fb36fca8ba90d6954c3643c94e36d94ed1a0e25f9`.

La construction finale s'est achevée sans modification des sources pendant
la compilation. L'autotest de l'exécutable compilé valide imports, ressources,
journal/graphiques, PDF, interface utilisateurs et boucle de connexion.
Les empreintes des sources actuelles et de l'exécutable correspondent au
manifeste. Le contrôle CRC de chaque membre du ZIP réussit. Les gabarits Access
sont vidés, compactés et vérifiés avant publication ; les fichiers source sont
conservés. Le ZIP contient la procédure de recette et le kit réseau, sans photos
d'agents. Une empreinte `.sha256` accompagne l'archive.

La génération de gabarits Access possède désormais un délai maximal de
90 secondes : un blocage DAO/Windows interrompt la publication du ZIP avec
un message explicite. Le test associé confirme la conservation du gabarit source.

## Outils et recette

`scripts/run_release_checks.py` fournit un point d'entrée reproductible pour
la campagne ciblée et le cycle de vie du serveur jetable. Le lanceur isolé
produit un XML JUnit par profil, bloque les connexions réelles et préserve les
résultats. Les versions de construction sont fixées dans `packaging/`.

`RECETTE_LIVRAISON.md` précise les étapes sur deux postes, les essais de coupure
réseau, la restauration sur copie de test, les ACL avec un autre compte Windows
et la vérification Word/Excel/papier. Le portable inclut cette procédure et le
kit `Test-ImpressionSmb.ps1`.

## Limites de la recette locale

Les vrais postes et le chemin UNC de travail ne sont pas disponibles dans cette
session. Les essais réseau sur place, l'impression papier, la mise en page dans
les versions Word installées et la vérification avec un second compte Windows
restent à consigner dans la fiche de recette. Les essais automatisés de fichiers,
SQL et PDF ne sont pas présentés comme leur équivalent.

Les autres recommandations de performances, d'exploitation et d'ergonomie du
bilan initial restent distinctes de cette finalisation de livraison.

# Barème des limites de carrière — V2 0.1.7

La version précédente utilisait les anciennes limites de carrière dans la
source commune des attestations et de l'avancement. Le texte communiqué par
l'utilisateur a été vérifié dans le [Journal officiel n° 23 du 8 juin 2023,
pages 822–823](https://www.sgg.cg/JO/2023/congo-jo-2023-23.pdf#page=6) :
article 98 nouveau de la loi n° 17-2023 du 27 mai 2023.

## Valeurs appliquées

| Grades | Âge limite | Durée limite de service |
|---|---:|---:|
| Généraux et équivalents maritimes | 70 | 52 |
| Commandant à colonel-major et équivalents maritimes | 65 | 47 |
| Sous-lieutenant à capitaine et équivalents maritimes | 60 | 42 |
| Adjudant à adjudant-major, major et équivalents maritimes | 58 | 40 |
| Sergent, sergent-chef, maréchal des logis, maréchal des logis-chef et équivalents maritimes | 55 | 37 |
| Soldat, caporal, caporal-chef et équivalents maritimes | 50 | 32 |

La table centrale alimente les valeurs initiales, l'alignement des grades
reconnus à la lecture, les calculs de carrière et la vigilance d'avancement.
L'écran de configuration indique la référence de 2023.

Les grades non explicitement reconnus par cette table conservent le traitement
historique ou leur configuration. En particulier, aucune assimilation automatique
de Gendarme, Gendarme adjoint, Brigadier, Brigadier-chef ou Aspirant à une
catégorie du tableau n'a été inventée. Les codes historiques distincts SCH et MAJ
sont conservés afin de ne pas créer d'ambiguïté dans les configurations existantes.

## Persistance lors des mises à jour

Le chargement applique en mémoire les seuils reconnus, sans réécrire les réglages
MySQL. Les grades personnalisés, le signataire, la ville, les options et les
présentations de noms composés restent conservés. Un enregistrement explicite
utilise le contrôle de version partagé existant.

Le remplacement de l'exécutable ne doit pas réinitialiser la configuration.
Tous les postes doivent utiliser la V2 0.1.7 : une ancienne version contient
encore le barème précédent et peut l'afficher à nouveau.

## Carrière et pension

Les seuils de service nécessaires à l'ouverture d'un droit à pension sont
distincts de ces limites. Ils ne sont pas introduits dans le calcul des dates
de fin de carrière. Aucun calcul de droit ou de montant de pension n'a été ajouté.
La convention de date au 31 décembre déjà utilisée par l'application est conservée.

## Contrôles

Les tests comparent les dix-neuf lignes fournies, leurs équivalents maritimes,
les dates de carrière et l'impact sur la vigilance d'avancement. Une instance
MySQL jetable vérifie deux clients, des chargements répétés et la conservation
des réglages avant/après un enregistrement explicite. Aucun accès ni import
sur la base de production n'a été effectué.

Résultats : 2 049 tests unitaires réussis, test MySQL de conservation des
réglages réussi, six autotests de l'exécutable réussis. Le barème extrait
de l'exécutable a également été contrôlé pour les six groupes de limites.

Dossier complet sans ZIP :
`sme_python/dist/releases/20261103T102436797264Z/SME_Gendarmerie`.

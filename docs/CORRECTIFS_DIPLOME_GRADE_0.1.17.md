Diplôme affiché, référence des grades et fin de stage — 0.1.17

La Liste générale, Édition SME et la synthèse de la fiche complète affichent
le dernier diplôme militaire ajouté au dossier parmi les diplômes applicables
au mois consulté. L'ordre d'ajout détermine le choix, et non la date d'obtention.
Un diplôme civil ajouté ensuite ne remplace pas le diplôme militaire affiché.
La lecture des situations existantes utilise également cette règle sans
obliger à ressaisir les diplômes déjà enregistrés. Les mois clôturés conservent
leur situation archivée. Un diplôme futur reste réservé aux mois concernés.

L'action groupée « Changer grade » dans Édition SME comporte le champ facultatif
« Ref/OG/Decret ». Une référence renseignée est enregistrée pour chaque agent,
dans la situation applicable et l'historique des grades. Une référence seule
peut être corrigée même si le grade et la date de nomination sont identiques.
Si le champ reste vide, les références existantes ne sont pas effacées.
La passation et sa référence constituent une seule entrée du journal de session.

La fin de stage ferme la fiche de stage, applique le retour à Présent et ajoute
le diplôme demandé dans une même transaction par agent. Un échec annule
l'ensemble pour l'agent. L'ajout d'un diplôme pour un agent déjà revenu à
Présent reste possible. Un test vérifie qu'un diplôme délivré en fin de stage
devient le diplôme militaire affiché même si un diplôme enregistré auparavant
porte une date d'obtention plus récente.

Les essais utilisent des bases MySQL synthétiques jetables. Aucun enregistrement
de la base d'exploitation n'est modifié par ces essais.

Livraison : conserver le dossier portable complet avec son exécutable,
ses bibliothèques et ses ressources. Aucune archive ZIP de distribution.

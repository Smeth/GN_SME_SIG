Précision de la règle du diplôme — 0.1.18

Cette livraison remplace la règle de sélection introduite en 0.1.17.

Dans la Liste générale, Édition SME et la synthèse de la fiche complète, le
diplôme affiché est le diplôme militaire dont la date d'obtention est la plus
récente parmi les diplômes applicables au mois consulté. À date d'obtention
identique, le dernier diplôme ajouté est retenu. Ajouter plus tard un ancien
diplôme ne remplace donc pas un diplôme obtenu plus récemment.

Depuis un mois de travail de décembre 2026, une saisie peut porter une date
d'obtention ou de nomination de juillet 2026. L'événement est conservé dans
l'historique et les situations mensuelles existantes sont recalculées à partir
de sa date d'effet. Les événements postérieurs restent prioritaires : une
promotion de novembre continue à déterminer le grade de décembre, et un
diplôme obtenu en novembre continue à être affiché en décembre.

Si le mois de prise d'effet est clôturé, il faut le rouvrir avant de modifier
son historique. Les archives clôturées ultérieures restent préservées. Un mois
mensuel absent n'est pas créé artificiellement pour tous les agents.

La référence facultative Ref/OG/Decret dans les passations groupées et
l'enregistrement transactionnel de la fin de stage et de son diplôme sont
conservés. Un diplôme de fin de stage est enregistré même si un diplôme obtenu
plus récemment reste affiché.

Validation : tests de saisies de juillet depuis décembre, maintien des
événements postérieurs, sélection par date et départage à date identique,
enregistrement des références groupées et du diplôme de fin de stage.
Les tests MySQL utilisent uniquement des bases synthétiques jetables.

Livraison : dossier portable complet sans ZIP de distribution.

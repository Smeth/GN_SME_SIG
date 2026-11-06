# Correctif 0.1.22 - Vidage mensuel

Le mois consulte, y compris le mois actif, utilise sa propre situation mensuelle. Un ancien segment ouvert appartenant a un autre mois ne peut plus repeupler un mois enregistre puis vide.

Le vidage et son apercu couvrent les anciennes notations YYYYMM et YYYY-MM du meme mois, y compris plusieurs periodes equivalentes. Les caches des listes sont invalides globalement. Une verification des lignes restantes precede la validation transactionnelle et le message de succes. Le bilan affiche les situations supprimees et restantes, ainsi que le mois effectivement nettoye.

Les identites des agents, les autres mois et les evenements importes encore attestes dans un autre mois sont conserves. Aucune remise a zero de la base de production ni aucun reimport global n'est necessaire. Aucune suppression de production n'a ete executee pendant ces controles.

Validation sur base MySQL jetable : mois actif, archive, ancien format de mois, doublons de periodes, rejet d'un faux succes et preservation des autres mois. Les corrections precedentes, dont Ref/OG/Decret et la saisie de date, sont incluses.

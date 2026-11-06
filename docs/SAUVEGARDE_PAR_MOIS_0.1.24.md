# Sauvegarde et restauration par mois - 0.1.24

Sauvegarde et restauration > Sauvegarde et restauration d'un seul mois.

Sauvegarder un mois : choisir un mois present dans MySQL, puis le fichier ZIP de sauvegarde mensuelle. Cette archive de donnees est distincte du dossier de distribution de l'executable, livre sans ZIP.

Restaurer un mois : choisir cette archive mensuelle, verifier son mois et la base cible, puis confirmer le mois. Une sauvegarde complete de securite est creee et verifiee sous maintenance avant la restauration transactionnelle. Le mois n'est pas deplace vers une autre periode.

L'archive contient les situations, diplomes/passations/affectations et evenements rattaches au mois, les lots et erreurs d'import, les taches et acquittements d'alertes. Elle emporte les identites et referentiels necessaires, ainsi que le dernier diplome militaire utile a la lecture du mois, meme obtenu avant celui-ci. Les comptes, reglages globaux, medias, exports et journaux d'audit ne sont pas remplaces.

Les identifiants techniques sont remappes. Les agents et references existants sont reutilises ; leurs valeurs communes aux autres mois ne sont pas ecrasees. Les liens automatiques de stage sont remappes. Si un evenement de position depend d'un stage d'un autre mois, ce stage doit deja exister de maniere non ambigue dans la base ; restaurer son mois d'origine en premier si necessaire.

Les autres mois sont compares avant et apres restauration, y compris leur lecture des diplomes partages. Si la restauration change leur affichage, elle est annulee. Un rapprochement d'identite ambigu provoque egalement une annulation explicite. Aucun changement de mois actif n'est impose et les statuts des periodes existantes sont conserves.

Cette fonction utilise une archive mensuelle creee par son bouton. Une ancienne sauvegarde complete reste utilisable avec la restauration complete ou partielle classique. Les formats sont distingues pour eviter une restauration globale accidentelle.

Aucune restauration de production n'a ete executee lors du developpement ou des tests.

# Jeu d'évaluation du matching

Sans mesure, chaque modification du matching est un pari. Ce dossier contient
le jeu de référence et l'outil qui le mesure.

## Constituer le jeu

```bash
python -m scripts.eval.build_goldenset --intents 40 --depth 15
```

Le script tire des intentions réelles en base, exécute la recherche actuelle et
écrit `goldenset.json` avec, pour chaque intention, les offres candidates **non
annotées**.

Il faut ensuite les annoter à la main. C'est le seul travail non automatisable,
et c'est lui qui donne sa valeur à tout le reste. Pour chaque offre, remplacer
`null` par :

| Valeur | Signification |
|--------|---------------|
| `2`    | pertinente — correspond vraiment à la demande |
| `1`    | acceptable — liée au domaine, mais décalée (niveau, lieu, type) |
| `0`    | hors sujet |

Trente à cinquante intentions suffisent : au-delà, l'effort d'annotation
augmente plus vite que la fiabilité de la mesure.

## Mesurer

```bash
python -m scripts.eval.run_eval
python -m scripts.eval.run_eval --baseline avant.json   # comparer deux états
```

Sortie : precision@5, recall@10, MRR et nDCG@10, globalement et par mode de
recherche.

## Interpréter

- **precision@5** — sur les 5 premières offres montrées, combien sont
  pertinentes. C'est la métrique qui reflète le mieux l'expérience réelle :
  personne ne lit au-delà des premières.
- **recall@10** — part des offres pertinentes du corpus effectivement remontées.
  Un recall faible avec une precision haute signale un rappel trop étroit.
- **MRR** — à quel rang apparaît la première bonne offre.
- **nDCG@10** — tient compte de l'ordre *et* des trois niveaux d'annotation.

## Méthode de travail

1. Mesurer **avant** toute modification, garder le fichier de résultats.
2. Modifier.
3. Re-mesurer avec `--baseline` sur le fichier d'avant.
4. Ne conserver le changement que si la comparaison est favorable.

Le jeu est à réannoter partiellement quand le corpus d'offres change beaucoup :
une offre pertinente désactivée ou expirée fausse le recall.

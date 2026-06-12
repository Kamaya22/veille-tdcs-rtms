# Données de la veille — codebook

Ce dossier transforme la veille en **données structurées**, pour pouvoir *vérifier* (et pas
seulement espérer) que la veille reste exhaustive, fiable et diversifiée dans la durée. Même
séparation des rôles que dans les autres dépôts d'automatisation : **le script extrait et
dédoublonne (reproductible), l'agent juge et étiquette, le script agrège et compte.**

## Fichiers

| Fichier | Rôle | Écrit par |
|---|---|---|
| `candidates/<YYYY-Www>.json` | **Tous** les candidats bruts récupérés par le script cette semaine (audit). | `tools/fetch_studies.py` |
| `reported/<YYYY-Www>.json` | Les études **réellement résumées** dans le bulletin, avec étiquetage clinique. | Agent |
| `seen.json` | Registre anti-doublon : toute étude déjà proposée (résumée **ou** écartée). | Agent |
| `registry.csv` | Registre canonique des **revues/venues** (éditeur, type, discipline, pays). **Auditable par Kamil.** | Agent (ajouts), Kamil (corrections) |
| `STATS.md` | Statistiques **déterministes**, régénérées à chaque push. **Ne pas éditer à la main.** | `tools/build_stats.py` (GitHub Action) |

## `candidates/<YYYY-Www>.json` (écrit par le script)

```json
{
  "week": "2026-W24",
  "generated_utc": "...",
  "window": {"from": "2026-06-04", "to": "2026-06-12", "days": 8},
  "sources_queried": ["pubmed", "medrxiv", "clinicaltrials"],
  "max_studies": 5,
  "counts": {"pubmed": 12, "medrxiv": 3, "clinicaltrials": 5,
             "raw_total": 25, "filtered_already_seen": 2, "new_after_dedup": 20},
  "studies": [
    {
      "id": "pmid:40123456",
      "source": "pubmed",          // pubmed | medrxiv | clinicaltrials
      "title": "...",
      "journal": "Brain Stimulation",
      "authors": "Smith J, ...",
      "year": 2026,
      "date": "2026-06-09",
      "doi": "10.xxxx/...",
      "pmid": "40123456",
      "url": "https://pubmed.ncbi.nlm.nih.gov/40123456/",
      "publication_types": ["Randomized Controlled Trial"],
      "peer_reviewed": true,
      "modalities": ["rTMS"],       // détecté depuis titre+résumé
      "indications": ["depression"],
      "abstract": "..."
    }
  ]
}
```

- **id** — identifiant stable, clé de dédoublonnage : `pmid:<id>`, `doi:<doi>` ou `nct:<NCT...>`.
- Le script **ne touche pas** à `seen.json` : il signale seulement les candidats nouveaux.

## `reported/<YYYY-Www>.json` (écrit par l'agent après rédaction)

Une entrée par étude **effectivement résumée** dans le bulletin de la semaine. C'est ce fichier
qu'agrège `build_stats.py`.

```json
{
  "week": "2026-W24",
  "reported": [
    {
      "id": "pmid:40123456",
      "venue_id": "brain-stimulation",   // doit exister dans registry.csv
      "source": "pubmed",
      "modality": "rTMS",                 // tDCS | rTMS
      "indication": "depression",
      "evidence_type": "rct",             // voir taxonomie ci-dessous
      "peer_reviewed": true,
      "open_access": "open",              // open | paywall | unknown
      "year": 2026
    }
  ]
}
```

- **evidence_type** — `meta-analysis` · `systematic-review` · `umbrella-review` · `rct` ·
  `controlled-trial` · `cohort` · `case-series` · `primary-study` · `preprint` · `trial-protocol`
  (ClinicalTrials.gov) · `to-verify`.
- **venue_id** — réutiliser un `id` de `registry.csv`. **Si la revue n'y figure pas**, ajouter une
  ligne (`id,name,publisher,type,discipline,country,notes`). En cas de doute sur `discipline`/`country`,
  écrire `to-verify` plutôt que deviner — Kamil tranche (remonté dans `STATS.md`).

## `seen.json` (écrit par l'agent — anti-doublon)

```json
{
  "last_run_utc": "2026-06-12T06:03:00+00:00",
  "seen": {
    "pmid:40123456": {"first_week": "2026-W24", "status": "resume", "title": "..."},
    "nct:NCT0xxxxxxx": {"first_week": "2026-W24", "status": "ecarte", "title": "..."}
  }
}
```

- **status** — `resume` (résumée dans le bulletin) ou `ecarte` (vue mais non retenue). Dans les
  deux cas l'étude **ne sera plus jamais re-proposée**.

## `registry.csv` — une ligne par venue

`id,name,publisher,type,discipline,country,notes`
- **type** — `journal` · `preprint-server` (medRxiv, **non revu**) · `registry` (ClinicalTrials.gov) ·
  `megajournal` · `institution`.
- **discipline** — `neuromodulation`, `psychiatry`, `neurology`, `general-medicine`, `multidisciplinary`…
- Valeur spéciale **`to-verify`** (dans `discipline`/`country`/`evidence_type`) = l'agent a hésité ;
  remontée en tête de `STATS.md` pour que Kamil décide. **Kamil a le dernier mot.**

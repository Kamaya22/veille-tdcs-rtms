#!/usr/bin/env python3
"""Veille tDCS / rTMS — récupération déterministe des nouvelles études.

Rôle : interroger des sources de recherche sérieuses sur les 7 derniers jours pour
la neuromodulation (tDCS / rTMS) en psychiatrie, dédoublonner contre les études déjà
traitées (`data/seen.json`), et écrire les candidats dans `data/candidates/<semaine>.json`.

Philosophie (reprise des dépôts d'automatisation existants) : **le script extrait et
dédoublonne (reproductible), l'agent juge et résume (français, clinique).** Le script
n'écrit donc JAMAIS dans `data/seen.json` — c'est l'agent qui valide ce qui a réellement
été traité.

Bibliothèque standard uniquement — aucune dépendance pip (robustesse de la routine cloud).

Sources :
  - Europe PMC (https://europepmc.org) : indexe PubMed/MEDLINE (revu par les pairs,
    `SRC:MED`) ET les preprints medRxiv (`SRC:PPR`), via une seule API JSON. Expose le
    PMID et permet de reconstruire l'URL PubMed.
  - ClinicalTrials.gov API v2 : essais cliniques nouveaux / récemment mis à jour.

Usage : python tools/fetch_studies.py
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Sortie console en UTF-8 (utile sous Windows où la console est en cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — best effort
        pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO_ROOT, "config", "query.json")
DATA_DIR = os.path.join(REPO_ROOT, "data")
SEEN = os.path.join(DATA_DIR, "seen.json")
CANDIDATES_DIR = os.path.join(DATA_DIR, "candidates")

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CTGOV = "https://clinicaltrials.gov/api/v2/studies"
USER_AGENT = "VeilleNeuromodulation/1.0 (psychiatry research monitoring; mailto:kamilmahmal22@gmail.com)"


# --------------------------------------------------------------------------- #
# Utilitaires HTTP / temps
# --------------------------------------------------------------------------- #
def http_get_json(url, params, retries=3, timeout=40):
    """GET JSON avec quelques réessais ; lève la dernière exception en cas d'échec."""
    full = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT,
                                                         "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — on veut une dégradation gracieuse
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err


def iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


# --------------------------------------------------------------------------- #
# Construction des requêtes à partir du config
# --------------------------------------------------------------------------- #
def quote_term(t):
    """Met entre guillemets les expressions à plusieurs mots (syntaxe Europe PMC / Essie)."""
    return f'"{t}"' if " " in t.strip() else t.strip()


def or_group(terms):
    return "(" + " OR ".join(quote_term(t) for t in terms) + ")"


def field_group(terms, fields=("TITLE", "ABSTRACT")):
    """Groupe OR exigeant le terme dans le titre ou le résumé (précision Europe PMC).

    Recherche par jeton (et non sous-chaîne) : les acronymes courts (OCD, PTSD…) sont sûrs."""
    parts = []
    for t in terms:
        q = quote_term(t)
        parts.extend(f"{fld}:{q}" for fld in fields)
    return "(" + " OR ".join(parts) + ")"


def all_modality_terms(cfg):
    terms = []
    for mod_terms in cfg["modalities"].values():
        terms.extend(mod_terms)
    return terms


def _word_match(term, text):
    """Vrai si `term` apparaît comme mot entier (évite « tES » dans « intestinal »)."""
    return re.search(r"\b" + re.escape(term) + r"\b", text, flags=re.IGNORECASE) is not None


def detect_modalities(cfg, text):
    text = text or ""
    return [mod for mod, terms in cfg["modalities"].items()
            if any(_word_match(t, text) for t in terms)]


def detect_indications(cfg, text):
    text = text or ""
    return [ind for ind in cfg["indications"] if _word_match(ind, text)]


# --------------------------------------------------------------------------- #
# Source 1 — Europe PMC (PubMed/MEDLINE + preprints medRxiv)
# --------------------------------------------------------------------------- #
def europepmc_search(cfg, src_clause, date_from, date_to, peer_reviewed, max_results, label):
    # Exiger la modalité ET une indication dans le titre/résumé : précision nettement
    # meilleure que la recherche plein texte par défaut (qui ramène du hors-sujet).
    stim = field_group(all_modality_terms(cfg))
    ind = field_group(cfg["indications"])
    query = (f"{stim} AND {ind} AND {src_clause} "
             f"AND (FIRST_PDATE:[{date_from} TO {date_to}])")
    results, cursor, fetched = [], "*", 0
    page_size = 100
    while fetched < max_results:
        try:
            data = http_get_json(EUROPEPMC, {
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": min(page_size, max_results - fetched),
                "cursorMark": cursor,
            })
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] erreur Europe PMC: {e}", file=sys.stderr)
            break
        hits = (data.get("resultList") or {}).get("result", []) or []
        if not hits:
            break
        for r in hits:
            results.append(normalize_epmc(cfg, r, peer_reviewed))
        fetched += len(hits)
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return results


def normalize_epmc(cfg, r, peer_reviewed):
    pmid = r.get("pmid")
    doi = r.get("doi")
    source = r.get("source", "")  # MED, PPR, PMC...
    title = (r.get("title") or "").strip()
    abstract = (r.get("abstractText") or "").strip()
    journal = ""
    ji = r.get("journalInfo") or {}
    if ji.get("journal"):
        journal = ji["journal"].get("title") or ji["journal"].get("medlineAbbreviation") or ""
    if not journal:
        journal = r.get("bookOrReportDetails", {}).get("publisher", "") or r.get("publisher", "")
    pub_types = []
    pt = r.get("pubTypeList") or {}
    if isinstance(pt, dict):
        pub_types = pt.get("pubType", []) or []
        if isinstance(pub_types, str):
            pub_types = [pub_types]
    # Identifiant stable et URL canonique
    if pmid:
        sid = f"pmid:{pmid}"
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    elif doi:
        sid = f"doi:{doi}"
        url = f"https://doi.org/{doi}"
    else:
        sid = f"epmc:{r.get('id', '')}"
        url = None
    text = f"{title} {abstract}"
    return {
        "id": sid,
        "source": "medrxiv" if source == "PPR" else "pubmed",
        "epmc_source": source,
        "title": title,
        "journal": journal,
        "publisher": (r.get("bookOrReportDetails", {}) or {}).get("publisher", ""),
        "authors": r.get("authorString", ""),
        "year": int(r["pubYear"]) if str(r.get("pubYear", "")).isdigit() else None,
        "date": r.get("firstPublishDate") or r.get("firstIndexDate"),
        "doi": doi,
        "pmid": pmid,
        "url": url,
        "publication_types": pub_types,
        "peer_reviewed": peer_reviewed,
        "modalities": detect_modalities(cfg, text),
        "indications": detect_indications(cfg, text),
        "abstract": abstract,
    }


# --------------------------------------------------------------------------- #
# Source 2 — ClinicalTrials.gov (API v2)
# --------------------------------------------------------------------------- #
def clinicaltrials_search(cfg, date_from, max_results, label):
    stim = or_group(all_modality_terms(cfg))
    ind = or_group(cfg["indications"])
    term = f"{stim} AND {ind}"
    results, token, fetched = [], None, 0
    while fetched < max_results:
        params = {
            "query.term": term,
            "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{date_from},MAX]",
            "pageSize": min(100, max_results - fetched),
            "fields": ("NCTId|BriefTitle|OfficialTitle|Condition|OverallStatus|StudyType|"
                       "Phase|LastUpdatePostDate|StartDate|BriefSummary|InterventionName"),
        }
        if token:
            params["pageToken"] = token
        try:
            data = http_get_json(CTGOV, params)
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] erreur ClinicalTrials.gov: {e}", file=sys.stderr)
            break
        studies = data.get("studies", []) or []
        if not studies:
            break
        for s in studies:
            norm = normalize_ctgov(cfg, s)
            if norm:
                results.append(norm)
        fetched += len(studies)
        token = data.get("nextPageToken")
        if not token:
            break
    return results


def normalize_ctgov(cfg, s):
    ps = s.get("protocolSection", {}) or {}
    idm = ps.get("identificationModule", {}) or {}
    nct = idm.get("nctId")
    if not nct:
        return None
    title = idm.get("briefTitle") or idm.get("officialTitle") or ""
    status = (ps.get("statusModule", {}) or {}).get("overallStatus", "")
    last_update = (ps.get("statusModule", {}) or {}).get("lastUpdatePostDateStruct", {}).get("date")
    design = ps.get("designModule", {}) or {}
    study_type = design.get("studyType", "")
    phases = design.get("phases", []) or []
    conds = (ps.get("conditionsModule", {}) or {}).get("conditions", []) or []
    summary = (ps.get("descriptionModule", {}) or {}).get("briefSummary", "")
    interventions = [i.get("name", "") for i in
                     (ps.get("armsInterventionsModule", {}) or {}).get("interventions", []) or []]
    text = f"{title} {summary} {' '.join(conds)} {' '.join(interventions)}"
    return {
        "id": f"nct:{nct}",
        "source": "clinicaltrials",
        "title": title.strip(),
        "journal": "ClinicalTrials.gov",
        "authors": "",
        "year": int(last_update[:4]) if last_update and last_update[:4].isdigit() else None,
        "date": last_update,
        "doi": None,
        "nct": nct,
        "url": f"https://clinicaltrials.gov/study/{nct}",
        "status": status,
        "study_type": study_type,
        "phases": phases,
        "conditions": conds,
        "interventions": interventions,
        "publication_types": [study_type] if study_type else [],
        "peer_reviewed": False,
        "modalities": detect_modalities(cfg, text),
        "indications": detect_indications(cfg, text),
        "abstract": summary.strip(),
    }


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #
def main():
    cfg = load_json(CONFIG, None)
    if cfg is None:
        print(f"Config introuvable: {CONFIG}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    week = iso_week(now)
    window = int(cfg.get("window_days", 8))
    date_to = now.date().isoformat()
    date_from = (now - timedelta(days=window)).date().isoformat()
    per_source = int(cfg.get("max_candidates_per_source", 60))
    sources = cfg.get("sources", {})

    seen_doc = load_json(SEEN, {"last_run_utc": None, "seen": {}})
    seen_ids = set(seen_doc.get("seen", {}).keys())

    print(f"Veille {week} — fenêtre {date_from} → {date_to} ({window} j). "
          f"{len(seen_ids)} étude(s) déjà connue(s).")

    raw = []
    if sources.get("pubmed", True):
        n = europepmc_search(cfg, "SRC:MED", date_from, date_to, True, per_source, "pubmed")
        print(f"  PubMed/MEDLINE : {len(n)} résultat(s) bruts")
        raw.extend(n)
    if sources.get("medrxiv", True):
        n = europepmc_search(cfg, '(SRC:PPR AND (PUBLISHER:"medRxiv" OR PUBLISHER:medRxiv))',
                             date_from, date_to, False, per_source, "medrxiv")
        print(f"  medRxiv (preprints) : {len(n)} résultat(s) bruts")
        raw.extend(n)
    if sources.get("clinicaltrials", True):
        n = clinicaltrials_search(cfg, date_from, per_source, "clinicaltrials")
        print(f"  ClinicalTrials.gov : {len(n)} résultat(s) bruts")
        raw.extend(n)

    # Dédoublonnage : entre sources (même id) + contre l'historique seen.json
    by_id, dup_seen = {}, 0
    for study in raw:
        sid = study["id"]
        if sid in seen_ids:
            dup_seen += 1
            continue
        if sid not in by_id:
            by_id[sid] = study
    deduped = list(by_id.values())

    # Filet de périmètre : ne garder que les études où une modalité tDCS/rTMS est réellement
    # présente dans le titre/résumé. No-op pour PubMed/medRxiv (déjà filtrés à la requête),
    # retire le bruit ClinicalTrials.gov (psilocybine, tACS/TUS, stimulation médullaire…).
    new_studies = [s for s in deduped if s.get("modalities")]
    dropped_no_modality = len(deduped) - len(new_studies)

    # Tri : preuve la plus forte d'abord, puis date décroissante
    def sort_key(s):
        pt = " ".join(s.get("publication_types", [])).lower()
        if "meta-analysis" in pt or "systematic review" in pt:
            rank = 0
        elif "randomized" in pt or s.get("source") == "clinicaltrials":
            rank = 1
        elif s.get("source") == "medrxiv":
            rank = 3
        else:
            rank = 2
        return (rank, "" if not s.get("date") else s["date"])
    new_studies.sort(key=lambda s: (sort_key(s)[0], (sort_key(s)[1] or "")), reverse=False)

    counts = {
        "pubmed": sum(1 for s in new_studies if s["source"] == "pubmed"),
        "medrxiv": sum(1 for s in new_studies if s["source"] == "medrxiv"),
        "clinicaltrials": sum(1 for s in new_studies if s["source"] == "clinicaltrials"),
        "raw_total": len(raw),
        "filtered_already_seen": dup_seen,
        "filtered_no_modality": dropped_no_modality,
        "new_after_dedup": len(new_studies),
    }

    out = {
        "week": week,
        "generated_utc": now.isoformat(),
        "window": {"from": date_from, "to": date_to, "days": window},
        "sources_queried": [k for k, v in sources.items() if v],
        "max_studies": int(cfg.get("max_studies", 5)),
        "counts": counts,
        "studies": new_studies,
    }
    os.makedirs(CANDIDATES_DIR, exist_ok=True)
    out_path = os.path.join(CANDIDATES_DIR, f"{week}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"→ {counts['new_after_dedup']} nouvelle(s) étude(s) après dédoublonnage "
          f"(écartées car déjà vues : {dup_seen}). Écrit : {out_path}")
    if counts["new_after_dedup"] == 0:
        print("Aucune nouveauté : l'agent ne doit créer aucun bulletin (pas d'email).")


if __name__ == "__main__":
    main()

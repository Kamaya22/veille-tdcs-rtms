#!/usr/bin/env python3
"""Veille tDCS / rTMS — récupération + dédoublonnage des nouvelles études.

Philosophie : **le script extrait et dédoublonne (reproductible), l'agent juge et résume.**

Deux modes de collecte réseau (la partie déterministe — normalisation, dédoublonnage via
`data/seen.json`, filtre de périmètre, tri — est identique dans les deux) :

  1. Mode « raw » (utilisé en cloud) : l'agent récupère le JSON des API via l'outil **WebFetch**
     (non bloqué par l'egress du runner) et l'écrit dans `data/raw/<semaine>.json` selon le
     contrat ci-dessous. Le script lit ce fichier — aucun appel réseau direct.
       - `python tools/fetch_studies.py --print-queries` affiche les URL exactes à WebFetcher.
       - puis `python tools/fetch_studies.py` lit `data/raw/<semaine>.json` et écrit les candidats.

  2. Mode HTTP direct (repli, surtout pour tests **locaux** où le réseau sortant fonctionne) :
     si `data/raw/<semaine>.json` est absent, le script interroge lui-même Europe PMC
     (PubMed/MEDLINE + medRxiv) et ClinicalTrials.gov via `urllib`.

Sortie : `data/candidates/<semaine>.json`. Bibliothèque standard uniquement (aucune dépendance pip).

Contrat de `data/raw/<semaine>.json` (écrit par l'agent en mode WebFetch) :
{
  "week": "2026-W24",
  "fetched_utc": "...",
  "studies": [
    {"source": "pubmed|medrxiv|clinicaltrials", "peer_reviewed": true,
     "title": "...", "journal": "...", "authors": "...", "year": 2026,
     "date": "YYYY-MM-DD", "doi": null, "pmid": "40123456", "nct": null,
     "url": "...", "publication_types": ["..."], "abstract": "...",
     "conditions": ["..."]}
  ]
}
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
RAW_DIR = os.path.join(DATA_DIR, "raw")

EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CTGOV = "https://clinicaltrials.gov/api/v2/studies"
USER_AGENT = "VeilleNeuromodulation/1.0 (psychiatry research monitoring; mailto:kamilmahmal22@gmail.com)"
EPMC_PAGE_SIZE = 25   # petit pour que WebFetch renvoie un JSON complet et fiable
CTGOV_PAGE_SIZE = 50
CTGOV_FIELDS = ("NCTId|BriefTitle|OfficialTitle|Condition|OverallStatus|StudyType|"
                "Phase|LastUpdatePostDate|StartDate|BriefSummary|InterventionName")


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def quote_term(t):
    return f'"{t}"' if " " in t.strip() else t.strip()


def or_group(terms):
    return "(" + " OR ".join(quote_term(t) for t in terms) + ")"


def field_group(terms, fields=("TITLE", "ABSTRACT")):
    """Exige le terme dans le titre ou le résumé (précision Europe PMC, recherche par jeton)."""
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
    return re.search(r"\b" + re.escape(term) + r"\b", text, flags=re.IGNORECASE) is not None


def detect_modalities(cfg, text):
    text = text or ""
    return [mod for mod, terms in cfg["modalities"].items()
            if any(_word_match(t, text) for t in terms)]


def detect_indications(cfg, text):
    text = text or ""
    return [ind for ind in cfg["indications"] if _word_match(ind, text)]


# --------------------------------------------------------------------------- #
# Construction des requêtes (URL + consigne WebFetch)
# --------------------------------------------------------------------------- #
def epmc_url(cfg, src_clause, date_from, date_to):
    stim = field_group(all_modality_terms(cfg))
    ind = field_group(cfg["indications"])
    query = f"{stim} AND {ind} AND {src_clause} AND (FIRST_PDATE:[{date_from} TO {date_to}])"
    params = {"query": query, "format": "json", "resultType": "core", "pageSize": EPMC_PAGE_SIZE}
    return EUROPEPMC + "?" + urllib.parse.urlencode(params)


def ctgov_url(cfg, date_from):
    stim = or_group(all_modality_terms(cfg))
    ind = or_group(cfg["indications"])
    params = {
        "query.term": f"{stim} AND {ind}",
        "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{date_from},MAX]",
        "pageSize": CTGOV_PAGE_SIZE,
        "fields": CTGOV_FIELDS,
    }
    return CTGOV + "?" + urllib.parse.urlencode(params)


def build_queries(cfg, date_from, date_to):
    sources = cfg.get("sources", {})
    q = []
    extract = ("Cette URL renvoie une réponse JSON d'API. Extrais CHAQUE étude/enregistrement "
               "en un tableau JSON. Pour chacun, un objet avec : title, journal, authors, "
               "year (entier), date (YYYY-MM-DD), doi (ou null), pmid (ou null), nct (ou null), "
               "url, publication_types (tableau), abstract, conditions (tableau, [] si absent). "
               "Réponds UNIQUEMENT par le tableau JSON, sans aucun texte autour.")
    if sources.get("pubmed", True):
        q.append({"source": "pubmed", "peer_reviewed": True,
                  "url": epmc_url(cfg, "SRC:MED", date_from, date_to), "webfetch_prompt": extract})
    if sources.get("medrxiv", True):
        q.append({"source": "medrxiv", "peer_reviewed": False,
                  "url": epmc_url(cfg, '(SRC:PPR AND (PUBLISHER:"medRxiv" OR PUBLISHER:medRxiv))',
                                  date_from, date_to), "webfetch_prompt": extract})
    if sources.get("clinicaltrials", True):
        q.append({"source": "clinicaltrials", "peer_reviewed": False,
                  "url": ctgov_url(cfg, date_from), "webfetch_prompt": extract})
    return q


# --------------------------------------------------------------------------- #
# Normalisation d'un enregistrement « raw » (fourni par l'agent ou par le HTTP local)
# --------------------------------------------------------------------------- #
def normalize_record(cfg, rec, default_source=None, default_peer=None):
    source = rec.get("source") or default_source
    pmid = rec.get("pmid") or None
    nct = rec.get("nct") or None
    doi = rec.get("doi") or None
    if pmid:
        sid, url = f"pmid:{pmid}", rec.get("url") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    elif nct:
        sid, url = f"nct:{nct}", rec.get("url") or f"https://clinicaltrials.gov/study/{nct}"
    elif doi:
        sid, url = f"doi:{doi}", rec.get("url") or f"https://doi.org/{doi}"
    else:
        return None  # pas d'identifiant stable → inexploitable pour le dédoublonnage
    abstract = (rec.get("abstract") or "").strip()
    title = (rec.get("title") or "").strip()
    conditions = rec.get("conditions") or []
    text = f"{title} {abstract} {' '.join(conditions)}"
    year = rec.get("year")
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    peer = rec.get("peer_reviewed")
    if peer is None:
        peer = default_peer if default_peer is not None else (source == "pubmed")
    return {
        "id": sid,
        "source": source,
        "title": title,
        "journal": (rec.get("journal") or "").strip(),
        "authors": rec.get("authors", ""),
        "year": year,
        "date": rec.get("date"),
        "doi": doi,
        "pmid": pmid,
        "nct": nct,
        "url": url,
        "publication_types": rec.get("publication_types") or [],
        "peer_reviewed": bool(peer),
        "modalities": detect_modalities(cfg, text),
        "indications": detect_indications(cfg, text),
        "abstract": abstract,
    }


def parse_raw(cfg, raw_doc):
    out = []
    for rec in raw_doc.get("studies", []):
        norm = normalize_record(cfg, rec)
        if norm:
            out.append(norm)
    return out


# --------------------------------------------------------------------------- #
# Mode HTTP direct (repli local) — Europe PMC + ClinicalTrials.gov
# --------------------------------------------------------------------------- #
def http_get_json(url, retries=3, timeout=40):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise last_err


def http_collect(cfg, date_from, date_to):
    studies = []
    for q in build_queries(cfg, date_from, date_to):
        try:
            data = http_get_json(q["url"])
        except Exception as e:  # noqa: BLE001
            print(f"  [{q['source']}] erreur réseau directe: {e}", file=sys.stderr)
            continue
        if q["source"] == "clinicaltrials":
            recs = [_ctgov_to_record(s) for s in data.get("studies", []) or []]
        else:
            recs = [_epmc_to_record(r) for r in (data.get("resultList") or {}).get("result", []) or []]
        for r in recs:
            if not r:
                continue
            r["source"] = q["source"]
            r["peer_reviewed"] = q["peer_reviewed"]
            norm = normalize_record(cfg, r)
            if norm:
                studies.append(norm)
        print(f"  {q['source']} : {len(recs)} résultat(s) bruts", file=sys.stderr)
    return studies


def _epmc_to_record(r):
    ji = r.get("journalInfo") or {}
    journal = ""
    if ji.get("journal"):
        journal = ji["journal"].get("title") or ji["journal"].get("medlineAbbreviation") or ""
    pt = r.get("pubTypeList") or {}
    pub_types = pt.get("pubType", []) if isinstance(pt, dict) else []
    if isinstance(pub_types, str):
        pub_types = [pub_types]
    return {"title": r.get("title"), "journal": journal, "authors": r.get("authorString", ""),
            "year": r.get("pubYear"), "date": r.get("firstPublishDate") or r.get("firstIndexDate"),
            "doi": r.get("doi"), "pmid": r.get("pmid"), "url": None,
            "publication_types": pub_types, "abstract": r.get("abstractText", "")}


def _ctgov_to_record(s):
    ps = s.get("protocolSection", {}) or {}
    idm = ps.get("identificationModule", {}) or {}
    nct = idm.get("nctId")
    if not nct:
        return None
    design = ps.get("designModule", {}) or {}
    return {"title": idm.get("briefTitle") or idm.get("officialTitle") or "",
            "journal": "ClinicalTrials.gov", "authors": "",
            "year": (ps.get("statusModule", {}) or {}).get("lastUpdatePostDateStruct", {}).get("date"),
            "date": (ps.get("statusModule", {}) or {}).get("lastUpdatePostDateStruct", {}).get("date"),
            "nct": nct, "url": f"https://clinicaltrials.gov/study/{nct}",
            "publication_types": [design.get("studyType", "")] if design.get("studyType") else [],
            "abstract": (ps.get("descriptionModule", {}) or {}).get("briefSummary", ""),
            "conditions": (ps.get("conditionsModule", {}) or {}).get("conditions", []) or []}


# --------------------------------------------------------------------------- #
# Finalisation commune : dédoublonnage, filtre de périmètre, tri, écriture
# --------------------------------------------------------------------------- #
def finalize(cfg, studies, week, date_from, date_to, window, sources, mode):
    seen_doc = load_json(SEEN, {"last_run_utc": None, "seen": {}})
    seen_ids = set(seen_doc.get("seen", {}).keys())

    by_id, dup_seen = {}, 0
    for s in studies:
        sid = s["id"]
        if sid in seen_ids:
            dup_seen += 1
            continue
        by_id.setdefault(sid, s)
    deduped = list(by_id.values())

    # Filet de périmètre : une modalité tDCS/rTMS doit réellement apparaître dans le titre/résumé.
    new_studies = [s for s in deduped if s.get("modalities")]
    dropped_no_modality = len(deduped) - len(new_studies)

    def rank(s):
        pt = " ".join(s.get("publication_types", [])).lower()
        if "meta-analysis" in pt or "systematic review" in pt:
            return 0
        if "randomized" in pt or s.get("source") == "clinicaltrials":
            return 1
        if s.get("source") == "medrxiv":
            return 3
        return 2
    new_studies.sort(key=lambda s: (rank(s), s.get("date") or ""))

    counts = {
        "pubmed": sum(1 for s in new_studies if s["source"] == "pubmed"),
        "medrxiv": sum(1 for s in new_studies if s["source"] == "medrxiv"),
        "clinicaltrials": sum(1 for s in new_studies if s["source"] == "clinicaltrials"),
        "raw_total": len(studies),
        "filtered_already_seen": dup_seen,
        "filtered_no_modality": dropped_no_modality,
        "new_after_dedup": len(new_studies),
    }
    out = {
        "week": week, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "collect_mode": mode,
        "window": {"from": date_from, "to": date_to, "days": window},
        "sources_queried": [k for k, v in sources.items() if v],
        "max_studies": int(cfg.get("max_studies", 5)),
        "counts": counts, "studies": new_studies,
    }
    os.makedirs(CANDIDATES_DIR, exist_ok=True)
    out_path = os.path.join(CANDIDATES_DIR, f"{week}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"→ {counts['new_after_dedup']} nouvelle(s) étude(s) après dédoublonnage "
          f"(déjà vues : {dup_seen} ; hors-périmètre : {dropped_no_modality}). Écrit : {out_path}")
    if counts["new_after_dedup"] == 0:
        print("Aucune nouveauté : l'agent ne doit créer aucun bulletin (pas d'email).")


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

    # Mode --print-queries : afficher les URL à WebFetcher (pour l'agent en cloud).
    if "--print-queries" in sys.argv:
        payload = {"week": week, "window": {"from": date_from, "to": date_to, "days": window},
                   "raw_target": os.path.join("data", "raw", f"{week}.json"),
                   "queries": build_queries(cfg, date_from, date_to)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    raw_path = os.path.join(RAW_DIR, f"{week}.json")
    if os.path.exists(raw_path):
        mode = "raw (WebFetch)"
        print(f"Veille {week} — mode {mode}, lecture de {raw_path}")
        studies = parse_raw(cfg, load_json(raw_path, {"studies": []}))
        print(f"  {len(studies)} enregistrement(s) exploitables dans le fichier raw")
    else:
        mode = "http (direct)"
        print(f"Veille {week} — mode {mode} (repli local ; pas de data/raw/{week}.json) — "
              f"fenêtre {date_from} → {date_to} ({window} j)")
        studies = http_collect(cfg, date_from, date_to)

    finalize(cfg, studies, week, date_from, date_to, window, cfg.get("sources", {}), mode)


if __name__ == "__main__":
    main()

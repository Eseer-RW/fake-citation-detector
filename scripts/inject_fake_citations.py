"""
inject_fake_citations.py — generate fake (hallucinated) citations and mix
them into real cited_sent JSON files to evaluate the detector's recall.

Each fake citation is a plausible-sounding but non-existent paper:
  - realistic title, authors, year, journal
  - deliberately NOT in OpenAlex or Crossref
  - designed to look like AI-hallucinated citations

Outputs:
  - <out_dir>/cited_sent/  : modified JSON files with fakes injected
  - <out_dir>/ground_truth.json : which citation IDs are fake
"""
import argparse
import copy
import json
import pathlib
import random
import sys

# ── Fake citation templates ─────────────────────────────────────────────────
# Designed to sound realistic but not match any real paper at ≥0.85 similarity.
# Varied by field so they blend in naturally.

FAKE_CITATIONS = [
    # Biomedical / epidemiology
    {
        "title": "Longitudinal assessment of systemic inflammatory markers in post-acute COVID-19 sequelae: a multicentre cohort study",
        "authors": ["Harrison, M.", "Okonkwo, T.", "Lindström, E."],
        "year": "2021", "journal": "The Lancet Infectious Diseases",
        "doi": "", "citation": "Harrison et al. (2021) Lancet Infect Dis."
    },
    {
        "title": "Epigenetic regulation of microglial activation in neurodegenerative disease: mechanisms and therapeutic targets",
        "authors": ["Patel, R.", "Yamamoto, K.", "Ferreira, A."],
        "year": "2020", "journal": "Nature Neuroscience",
        "doi": "", "citation": "Patel et al. (2020) Nat Neurosci."
    },
    {
        "title": "Gut microbiome dysbiosis as a mediator of systemic inflammation in type 2 diabetes: evidence from a prospective twin study",
        "authors": ["Nakamura, S.", "Osei-Bonsu, E.", "Walters, D."],
        "year": "2019", "journal": "Cell Metabolism",
        "doi": "", "citation": "Nakamura et al. (2019) Cell Metab."
    },
    {
        "title": "Association between childhood adversity exposure and telomere attrition across the lifespan: a systematic review and meta-analysis",
        "authors": ["Kowalski, B.", "Mensah, F.", "Tran, H."],
        "year": "2018", "journal": "JAMA Psychiatry",
        "doi": "", "citation": "Kowalski et al. (2018) JAMA Psychiatry."
    },
    {
        "title": "Randomised controlled trial of personalised dietary intervention based on gut microbiome profiling in patients with inflammatory bowel disease",
        "authors": ["Gupta, N.", "Andersen, L.", "Okafor, C."],
        "year": "2022", "journal": "New England Journal of Medicine",
        "doi": "", "citation": "Gupta et al. (2022) N Engl J Med."
    },
    # Computational biology / bioinformatics
    {
        "title": "ScaleFold: an efficient transformer architecture for protein tertiary structure prediction from evolutionary sequence information",
        "authors": ["Chen, W.", "Korolev, I.", "Bashir, M."],
        "year": "2022", "journal": "Nature Methods",
        "doi": "", "citation": "Chen et al. (2022) Nat Methods."
    },
    {
        "title": "Benchmarking single-cell RNA-seq integration methods across tissue types and sequencing platforms",
        "authors": ["Larsson, P.", "Diallo, A.", "Whitfield, J."],
        "year": "2021", "journal": "Genome Biology",
        "doi": "", "citation": "Larsson et al. (2021) Genome Biol."
    },
    {
        "title": "GraphReg: graph neural network-based prediction of gene regulatory networks from chromatin accessibility data",
        "authors": ["Huang, Z.", "Volkov, N.", "Santos, R."],
        "year": "2023", "journal": "Nature Computational Science",
        "doi": "", "citation": "Huang et al. (2023) Nat Comput Sci."
    },
    # Machine learning / CS
    {
        "title": "Efficient sparse attention mechanisms for long-context language modelling with linear memory complexity",
        "authors": ["Petrov, A.", "Kim, S.", "Nwosu, E."],
        "year": "2023", "journal": "Journal of Machine Learning Research",
        "doi": "", "citation": "Petrov et al. (2023) JMLR."
    },
    {
        "title": "Calibration of uncertainty estimates in Bayesian deep learning under distribution shift",
        "authors": ["Martinez, C.", "Johansson, F.", "Adeyemi, K."],
        "year": "2022", "journal": "Advances in Neural Information Processing Systems",
        "doi": "", "citation": "Martinez et al. (2022) NeurIPS."
    },
    # Physics / chemistry
    {
        "title": "Room-temperature superconductivity in nitrogen-doped lutetium hydride under moderate pressure conditions",
        "authors": ["Reinholt, G.", "Tanaka, M.", "Osei, A."],
        "year": "2023", "journal": "Physical Review Letters",
        "doi": "", "citation": "Reinholt et al. (2023) Phys Rev Lett."
    },
    {
        "title": "Electrocatalytic reduction of CO2 to formate using atomically dispersed copper on nitrogen-doped graphene under ambient conditions",
        "authors": ["Villanueva, P.", "Hoffmann, R.", "Kwame, D."],
        "year": "2021", "journal": "Journal of the American Chemical Society",
        "doi": "", "citation": "Villanueva et al. (2021) J Am Chem Soc."
    },
    # Economics / social science
    {
        "title": "Long-run effects of early childhood nutrition interventions on human capital formation: evidence from randomised trials in sub-Saharan Africa",
        "authors": ["Ogundimu, F.", "Svensson, L.", "Prakash, N."],
        "year": "2020", "journal": "American Economic Review",
        "doi": "", "citation": "Ogundimu et al. (2020) Am Econ Rev."
    },
    {
        "title": "Social network structure and the diffusion of misinformation: causal evidence from a field experiment",
        "authors": ["Abramowitz, J.", "Nkemdirim, C.", "Löfgren, K."],
        "year": "2022", "journal": "Quarterly Journal of Economics",
        "doi": "", "citation": "Abramowitz et al. (2022) Q J Econ."
    },
    # Meta-analysis / methodology
    {
        "title": "Optimal stopping rules for adaptive sequential meta-analysis in the presence of publication bias",
        "authors": ["Bergmann, U.", "Adebayo, T.", "Leung, P."],
        "year": "2019", "journal": "Statistics in Medicine",
        "doi": "", "citation": "Bergmann et al. (2019) Stat Med."
    },
    {
        "title": "A unified framework for causal inference in observational studies with time-varying confounding",
        "authors": ["Rashid, M.", "Eriksson, H.", "Owusu-Acheampong, D."],
        "year": "2021", "journal": "Biometrika",
        "doi": "", "citation": "Rashid et al. (2021) Biometrika."
    },
]

random.seed(42)


def inject_fakes(json_path: pathlib.Path, out_dir: pathlib.Path,
                 n_fakes: int, fake_pool: list) -> list:
    """
    Load a cited_sent JSON, inject n_fakes fake citations at random positions,
    write the modified file to out_dir, and return the list of injected fake entries.
    """
    citations = json.loads(json_path.read_text())
    fakes_used = random.sample(fake_pool, min(n_fakes, len(fake_pool)))
    injected = []

    for fake_template in fakes_used:
        fake = copy.deepcopy(fake_template)
        # Add a fake citing sentence so it looks natural
        fake["sentences"] = [
            f"As demonstrated in previous work [CITATION], this approach has been validated across multiple settings."
        ]
        fake["_is_fake"] = True   # internal ground-truth marker
        # Insert at a random position
        pos = random.randint(0, len(citations))
        citations.insert(pos, fake)
        injected.append(fake_template["title"])

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / json_path.name).write_text(json.dumps(citations, indent=2))
    return injected


YEAR_SHIFT = 7

_WRONG_JOURNALS = [
    "Journal of Clinical Investigation",
    "Nature Medicine",
    "Cell",
    "Science",
    "PLOS ONE",
    "JAMA Internal Medicine",
    "BMJ Open",
    "The Lancet Oncology",
    "Annals of Internal Medicine",
    "Proceedings of the National Academy of Sciences",
]


def inject_mismatches(
    json_path: pathlib.Path,
    out_dir: pathlib.Path,
    per_paper: int = 2,
) -> list[dict]:
    """
    Pick `per_paper` real citations that have a long title and a year, corrupt their
    metadata (year +7, wrong journal), write the modified list to out_dir, and return
    a record of each corruption for ground_truth_mismatches.json.

    The title is left intact so Solr's title-only fallback can still find the paper
    after the year shift causes title+year matching to fail.  The shifted year and
    wrong journal are what validate_metadata() should flag as FOUND_MISMATCH.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    citations = json.loads(json_path.read_text())

    # Require a distinctive title (≥30 chars) so title-only Solr lookup can find it.
    # Require year so there is something to shift.
    candidates = [
        (i, c) for i, c in enumerate(citations)
        if len((c.get("title") or "").strip()) >= 30
        and c.get("year") not in (None, "", "—")
    ]

    picks = random.sample(candidates, min(per_paper, len(candidates)))
    modified = list(citations)
    corrupted_records = []

    for idx, original in picks:
        entry = copy.deepcopy(original)

        # Shift year
        try:
            original_year = int(original["year"])
        except (ValueError, TypeError):
            continue
        entry["year"] = str(original_year + YEAR_SHIFT)

        # Pick a wrong journal that doesn't match the original (case-insensitive)
        original_journal = (original.get("journal") or "").strip()
        pool = [j for j in _WRONG_JOURNALS
                if j.lower() != original_journal.lower()]
        entry["journal"] = random.choice(pool or _WRONG_JOURNALS)

        modified[idx] = entry
        corrupted_records.append({
            "title":             original.get("title", ""),
            "original_year":     original_year,
            "corrupted_year":    original_year + YEAR_SHIFT,
            "original_journal":  original_journal,
            "corrupted_journal": entry["journal"],
        })

    (out_dir / json_path.name).write_text(json.dumps(modified, indent=2))
    return corrupted_records


def main():
    ap = argparse.ArgumentParser(
        description="Inject fake citations into cited_sent JSONs for detector evaluation")
    ap.add_argument("--cited-sent-dir", required=True,
                    help="Source cited_sent directory")
    ap.add_argument("--out-dir", required=True,
                    help="Output directory (will contain cited_sent/ and ground truth JSON)")
    ap.add_argument("--fakes-per-paper", type=int, default=3,
                    help="Number of fake citations to inject per paper (default 3)")
    ap.add_argument("--papers", type=int, default=None,
                    help="Only process this many papers (default: all)")
    ap.add_argument("--inject-mismatches", action="store_true",
                    help="Corrupt real citations instead of injecting phantoms: "
                         "shifts year by +7 and replaces journal, leaving DOI intact "
                         "so the record is found but validate_metadata() flags it")
    args = ap.parse_args()

    src_dir   = pathlib.Path(args.cited_sent_dir)
    out_cited = pathlib.Path(args.out_dir) / "cited_sent"

    json_files = sorted(src_dir.glob("*.json"))
    if args.papers:
        json_files = json_files[:args.papers]
    if not json_files:
        sys.exit(f"No JSON files found in {src_dir}")

    if args.inject_mismatches:
        gt_path      = pathlib.Path(args.out_dir) / "ground_truth_mismatches.json"
        ground_truth = {}
        total        = 0

        for path in json_files:
            records = inject_mismatches(path, out_cited)
            ground_truth[path.stem] = records
            total += len(records)
            print(f"  {path.stem}: corrupted {len(records)} citation(s)")

        gt_path.write_text(json.dumps(ground_truth, indent=2))
        print(f"\nDone. {total} citations corrupted across {len(json_files)} papers.")
        print(f"Ground truth written to: {gt_path}")
        print(f"Modified JSONs written to: {out_cited}")

    else:
        gt_path      = pathlib.Path(args.out_dir) / "ground_truth.json"
        ground_truth = {}
        total        = 0

        for path in json_files:
            injected = inject_fakes(path, out_cited, args.fakes_per_paper, FAKE_CITATIONS)
            ground_truth[path.stem] = injected
            total += len(injected)
            print(f"  {path.stem}: injected {len(injected)} fake(s)")

        gt_path.write_text(json.dumps(ground_truth, indent=2))
        print(f"\nDone. {total} fake citations injected across {len(json_files)} papers.")
        print(f"Ground truth written to: {gt_path}")
        print(f"Modified JSONs written to: {out_cited}")


if __name__ == "__main__":
    main()

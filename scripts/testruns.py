from parser import parse_citation
import requests

citations = [
    "Ahmed SK, Hussein S, Qurbani K, Ibrahim RH, Fareeq A, Mahmood KA, Mohamed MG (2024) Antimicrobial resistance: Impacts, challenges, and future prospects. J Med Surg Public Health 2:100081 [Google Scholar]",
    "Alcayaga-Miranda F, Cuenca J, Khoury M (2017) Antimicrobial activity of mesenchymal stem cells: current status and new perspectives of antimicrobial peptide-based therapies. Front Immunol 8:339 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Ali L, Shaaban F, Sokhn ES, Saleh FA (2025) A systematic review of preclinical studies on therapeutic potential of mesenchymal stem/stromal cells and their secretome in bacterial infections. Stem Cell Res Ther 16(1):456 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Arbab S, Ullah H, Wang W, Zhang J (2022) Antimicrobial drug resistance against Escherichia coli and its harmful effect on animal health. Vet Med Sci 8:1780-1786 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Bara JJ, Richards RG, Alini M, Stoddart MJ (2014) Concise review: bone marrow-derived mesenchymal stem cells change phenotype following in vitro culture: implications for basic research and the clinic. Stem Cells 32:1713-1723 [DOI] [PubMed] [Google Scholar]",
    "Bicer M, Sheard J, Iandolo D, Boateng SY, Cottrell GS, Widera D (2020) Electrical stimulation of adipose-derived stem cells in 3D nanofibrillar cellulose increases their osteogenic potential. Biomolecules 10(12):1696 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Bicer M, Cottrell GS, Widera D (2021) Impact of 3D cell culture on bone regeneration potential of mesenchymal stromal cells. Stem Cell Res Ther 12:31 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Brandau S, Jakob M, Bruderek K, Bootz F, Giebel B, Radtke S, Mauel K, Jäger M, Flohé SB, Lang S (2014) Mesenchymal stem cells augment the anti-bacterial activity of neutrophil granulocytes. PLoS ONE 9:e106903 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Cabrera-Sosa L, Ochoa TJ (2020) 46-Escherichia coli diarrhea. In: Ryan ET, Hill DT, Solomon NE, Aronson NE, Endy TP (eds) Hunter's tropical medicine and emerging infectious diseases (Tenth Edition). Elsevier, London, pp 481-485 [Google Scholar]",
    "Castro Ramos A, Widjaja Lomanto MY, Vuong CK, Ohneda O, Fukushige M (2024) Antibacterial effects of human mesenchymal stem cells and their derivatives: a systematic review. Front Microbiol 15:1430650 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Cheung GYC, Bae JS, Otto M (2021) Pathogenicity and virulence of Staphylococcus aureus. Virulence 12:547-569 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Chow L, Johnson V, Impastato R, Coy J, Strumpf A, Dow S (2020) Antibacterial activity of human mesenchymal stem cells mediated directly by constitutively secreted factors and indirectly by activation of innate immune effector cells. Stem Cells Transl Med 9:235-249 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "de Sousa T, Hébraud M, Dapkevicius M, Maltez L, Pereira JE, Capita R, Alonso-Calleja C, Igrejas G, Poeta P (2021) Genomic and metabolic characteristics of the pathogenicity in Pseudomonas aeruginosa. Int J Mol Sci 22(23):12892 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Dominici M, Le Blanc K, Mueller I, Slaper-Cortenbach I, Marini FC, Krause DS, Deans RJ, Keating A, Prockop DJ, Horwitz EM (2006) Minimal criteria for defining multipotent mesenchymal stromal cells. The International Society for Cellular Therapy position statement. Cytotherapy 8:315-317 [DOI] [PubMed] [Google Scholar]",
    "Hakki SS, Turaç G, Bozkurt SB, Kayis SA, Hakki EE, Sahin E, Subasi C, Karaoz E (2017) Comparison of different sources of mesenchymal stem cells: palatal versus lipoaspirated adipose tissue. Cells Tissues Organs 204:228-240 [DOI] [PubMed] [Google Scholar]",
    "Ho CS, Wong CTH, Aung TT, Lakshminarayanan R, Mehta JS, Rauz S, McNally A, Kintses B, Peacock SJ, de la Fuente-Nunez C, Hancock REW, Ting DSJ (2025) Antimicrobial resistance: a concise update. Lancet Microbe 6:100947 [DOI] [PubMed] [Google Scholar]",
    "Ibrahim D, Jabbour JF, Kanj SS (2020) Current choices of antibiotic treatment for Pseudomonas aeruginosa infections. Curr Opin Infect Dis 33:464-473 [DOI] [PubMed] [Google Scholar]",
    "Jessberger N, Dietrich R, Granum PE, Martlbauer E (2020) The Bacillus cereus food infection as multifactorial process. Toxins (Basel) 12(11):701 [DOI] [PMC free article] [PubMed] [Google Scholar]",
    "Jovanovic J, Ornelis VFM, Madder A, Rajkovic A (2021) Bacillus cereus food intoxication and toxicoinfection. Compr Rev Food Sci Food Saf 20:3719-3761 [DOI] [PubMed] [Google Scholar]",
    "Krasnodembskaya A, Song Y, Fang X, Gupta N, Serikov V, Lee JW, Matthay MA (2010) Antibacterial effect of human mesenchymal stem cells is mediated in part from secretion of the antimicrobial peptide LL-37. Stem Cells 28:2229-2238 [DOI] [PMC free article] [PubMed] [Google Scholar]",
]


def lookup_crossref(title: str, year: int = None) -> dict:
    """Query the Crossref API and return structured metadata for the top match.

    First tries with a +-1-year filter; if that returns nothing, retries without
    any date filter so papers with imprecise publication dates still resolve.
    """
    def _query(params):
        resp = requests.get(
            "https://api.crossref.org/works",
            params=params,
            timeout=10,
        )
        return resp.json()["message"]["items"]

    try:
        # First attempt: with year filter (+-1 year to handle early-online dates)
        params = {"query.title": title, "rows": 3}
        if year:
            params["filter"] = f"from-pub-date:{year - 1},until-pub-date:{year + 1}"
        items = _query(params)

        # Fallback: drop the date filter entirely
        if not items and year:
            params_no_year = {"query.title": title, "rows": 3}
            items = _query(params_no_year)

        if not items:
            return {}

        top = items[0]

        # Extract authors
        authors = []
        for a in top.get("author", []):
            given  = a.get("given", "")
            family = a.get("family", "")
            authors.append(f"{family}, {given}".strip(", "))

        # Extract year
        year_found = None
        try:
            year_found = top["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass

        # Extract journal
        ct = top.get("container-title", [])
        journal = ct[0] if ct else None

        return {
            "doi":       top.get("DOI"),
            "title":     top.get("title", [None])[0],
            "authors":   authors,
            "year":      year_found,
            "journal":   journal,
            "volume":    top.get("volume"),
            "issue":     top.get("issue"),
            "pages":     top.get("page"),
            "publisher": top.get("publisher"),
            "type":      top.get("type"),
            "score":     top.get("score"),
        }

    except Exception as e:
        print(f"API error: {e}")
        return {}


print("CROSSREF LOOKUP RESULTS")
print("=" * 60)

for i, citation in enumerate(citations, 1):
    c = parse_citation(citation)
    meta = lookup_crossref(c.title, year=c.year) if c.title else {}

    print(f"\n[{i}] PARSED")
    print(f"     Title:   {c.title}")
    print(f"     Authors: {', '.join(c.authors[:3])}{'...' if len(c.authors) > 3 else ''}")
    print(f"     Year:    {c.year}  Journal: {c.journal}")

    print(f"     CROSSREF MATCH (score={meta.get('score', '-')})")
    print(f"     DOI:       {meta.get('doi', 'not found')}")
    print(f"     Title:     {meta.get('title')}")
    print(f"     Authors:   {', '.join(meta.get('authors', [])[:3])}{'...' if len(meta.get('authors', [])) > 3 else ''}")
    print(f"     Year:      {meta.get('year')}  Journal: {meta.get('journal')}")
    print(f"     Volume:    {meta.get('volume')}  Issue: {meta.get('issue')}  Pages: {meta.get('pages')}")
    print(f"     Publisher: {meta.get('publisher')}")
    print(f"     Type:      {meta.get('type')}")

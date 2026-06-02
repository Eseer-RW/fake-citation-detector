from parser import parse_citation

c = parse_citation("Brandau S, Jakob M, Bruderek K, Bootz F, Giebel B, Radtke S, Mauel K, Jäger M, Flohé SB, Lang S (2014) Mesenchymal stem cells augment the anti-bacterial activity of neutrophil granulocytes. PLoS ONE 9:e106903 [DOI] [PMC free article] [PubMed] [Google Scholar]")

print("style:  ", c.style)
print("title:  ", c.title)
print("year:   ", c.year)
print("journal:", c.journal)
from parser import parse_all_citations

block = [ 
    ""

]

for r in parse_all_citations(block):
    print(r.title, r.year, r.authors, r.journal)
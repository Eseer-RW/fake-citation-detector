#!/usr/bin/python
# -*- coding: utf8 -*-

# This script is used to process the xml file from GROBID

import xml.etree.ElementTree as ET
import re
import os
import json
import grobid_tei_xml
import glob
import copy
from bs4 import BeautifulSoup
from NLPtools.SentenceTokenizer import LitcoinSentenceTokenizer as SentenceTokenizer

sentence_tokenizer = SentenceTokenizer()

import datetime as _dt
_YEAR_MAX    = _dt.date.today().year + 1  # current year + 1 (allow slightly ahead-of-print)
_YEAR_SEARCH = re.compile(r'(?:19|20)\d{2}')  # finds any 1900-2099 candidate in a string

def _year_valid(year_str: str) -> bool:
    """Return True if year_str is a 4-digit year between 1900 and current year + 1."""
    if not re.match(r'^(?:19|20)\d{2}$', year_str):
        return False
    return 1900 <= int(year_str) <= _YEAR_MAX
def parse_tei_xml(xml_path):
    # Use grobid_tei_xml API to get header and  reference list
    with open(xml_path, 'r') as xml_file:
        doc = grobid_tei_xml.parse_document_xml(xml_file.read())
    
    doc = doc.to_dict()
    #print(json.dumps(doc.to_dict(), indent=2, ensure_ascii=True))

    # Use beautifulsoup to process body
    with open(xml_path, 'r') as xml_file:
        soup = BeautifulSoup(xml_file.read(), 'xml')

    for ele in soup.find_all('formula'):    # remove formula
        ele.decompose()
    for ele in soup.find_all('table'):      # remove table
        ele.decompose()
    for ele in soup.find_all('figure'):     # remove figure
        ele.decompose()
    body = soup.find_all('body')[0]         # get text from body
    body_text = []
    for para in body.find_all('p'): # if split by section instead of paragraph, use 'div' instead of 'p'
        # replace citation to [CITATION citation number]
        text = para.get_text()
        for e in para.find_all('ref'):
            try:
                if e.get('type') != 'bibr': #citation marker for figure, table, equation will be ignor
                    continue
                if e.get('target'):
                    insert_string = '[CITATION '+e.get('target')+']'
                    e.insert_after(insert_string)
                    e.decompose()
                else:   # can not find the right reference from ref list
                    insert_string = '[CITATION]'
                    e.insert_after(insert_string)
                    e.decompose()
            except Exception:
                print("<None></None>")
        body_text.append(add_middle_citation(para.get_text(), doc['citations'][0]['id']))
        # split sentence (1) generate data for string detection (2) generate data for model training 
    doc.update({'body_text':body_text})
    # doc = {'grobid_version', 'grobid_timestamp', 'header', 'pdf_md5', 'language_code', 'citations', 'abstract', 'body', 'acknowledgement', 'body_text'}
    doc.update({'refined_citations': re_format_refDict(doc['citations'])})
    return doc


def get_pages(citation):
    # citation -> string e.g.'MARUOTTI, A. and RYDÉN, T. (2009). A semiparametric approach to hidden Markov models under longitudinal observations. Stat. Comput. 19 381-393. MR2565312'
    page_pattern = re.compile("[0-9]+\-[0-9]+", re.I)
    page_pattern2 = re.compile("[0-9]+\-\s[0-9]+", re.I)
    page = re.search(page_pattern, citation)
    page2 = re.search(page_pattern2, citation)
    if page:
        return page.group()
    elif page2:
        return page2.group().replace(' ','')
    else:
        return ''

def re_format_refDict(references):
    # references = list of {'authors', 'index', 'id', 'unstructured', 'date', 'title', 'publisher', 'pages', 'book_title','journal', 'volume', 'pages'}
    # optional: 'publisher', 'pages', 'book_title', 'journal', 'volume', 'pages'
    new_ref = {}
    for ref in references:
        ID = ref['id']
        if 'title' not in ref or 'date' not in ref:
            new_ref.update({ID:{}})
        else:
            authors = ';'.join([x['full_name'] for x in ref['authors']])
            ID = ref['id']
            citation = ref['unstructured']
            year = ref['date']
            # Step 1: GROBID sometimes returns full dates like "2020-01-15"; keep only the year.
            if len(year) > 4:
                m = _YEAR_SEARCH.search(year)
                year = m.group() if m else ''

            # Step 2: If the year is outside the plausible range 1900-2099, it is likely a
            # volume number, page number, or report number that GROBID mis-tagged as the date
            # (e.g. 1854, 1768, 2116, 2264 seen in real data). The original condition here
            # was inverted — it fired when a year WAS found, never catching bad values.
            # Fix: fall back to the raw citation string only when the year is INVALID.
            if not _year_valid(year):
                m = _YEAR_SEARCH.search(citation)
                year = m.group() if m else ''
            title = ref['title']
            new_ref.update({ID:{'title':title, 'authors':authors, 'year':year, 'citation':citation, 'id':ID}})
            if 'publisher' in ref:
                new_ref[ID].update({'publisher': ref['publisher']})
            page = get_pages(ref['unstructured'])
            if page == '' and 'pages' in ref:
                page = ref['pages']
                if '-' not in page and len(page)>5:
                    page = ''
            #if 'pages' in ref:
            #    page = ref['pages']
            #    if '-' not in page and len(page)>5:
            #        page = get_pages(ref['unstructured'])
            #else:
            #    page = ''
            new_ref[ID].update({'pages':page})
            if 'journal' in ref:
                new_ref[ID].update({'journal':ref['journal']})
            else:
                new_ref[ID].update({'journal':''})
            if 'volume' in ref:
                new_ref[ID].update({'volume':ref['volume']})
            else:
                new_ref[ID].update({'volume':''})
    return new_ref
           

def get_citation_num(sentence, Id): # Id is a example of citation IDs, normally should be the first key of reference list
    pt = '#b'   # just for grobid result
    ref_pattern = re.compile("(\[CITATION\s"+pt+"[0-999]+\])", re.I) # [CITATION R21] not include the space before or after
    #ref_pattern = re.compile("(\[CITATION\sR[0-999]+\])", re.I) # [CITATION R21] not include the space before or after
    result = []
    marker = [''.join(x) for x in set(re.findall(ref_pattern, sentence))]
    for ele in marker:
        ref_num = re.search(pt.upper()+"[0-999]+|"+pt.lower()+"[0-999]+", ele).group()
        new_sent = re.sub("\s"+pt.upper()+ref_num.replace(pt.upper(),'')+"]", ']', sentence)
        new_sent = re.sub("\s"+pt.lower()+ref_num.replace(pt.lower(),'')+"]", ']', new_sent)  #in some case, [CITATION R21] was written as [CITATION r21] by author
        result.append({'sent':new_sent, 'ref_num':ref_num})
    return result

def add_middle_citation(para, Id):
    # deal with the case such as [2-6] which will yield [2,3,4,5,6]
    # Id is a example of citation IDs, normally should be the first key of reference list, such as R0
    # para is the paragraph with already change citation marker to [CITATION R0]
    pt = re.sub('[0-9]+','',Id).strip()
    pattern = "\[CITATION\s"+pt+"[0-9]+\]\–\[CITATION\s"+pt+"[0-9]+\]"
    if re.search(pattern, para) is not None:
        for case in re.finditer(pattern, para):
            num = re.findall(r'\d+', case[0])  # \d+ captures whole numbers; \d would split '10' into ['1','0']
            if len(num) == 2:
                new_insert_num = [i for i in range (int(num[0]), int(num[1])+1)]
                new_insert = ','.join([f"[CITATION {pt}{i}]" for i in new_insert_num])
                para = para.replace(case[0], new_insert)
    return para


def generate_data_for_CitingSentenceModel(doc, doc_id):
    # doc = {'grobid_version', 'grobid_timestamp', 'header', 'pdf_md5', 'language_code', 'citations', 'abstract', 'body', 'acknowledgement', 'body_text', 'refined_citations'}
    # body_text = [paragraph, paragraph,...]
    marker_pattern = re.compile("(\s|)(\[CITATION)(\]|.+?\])(\s|)") # [CITATION] or [CITATION #b21] or [CITATION *],include the space before and after
    ref_pattern = re.compile("(\[CITATION\s\#b[0-999]+\])") # [CITATION #b21] not include the space before or after
    known_ref_pattern = re.compile("(\s|)(\[CITATION\])(\s|)") # [CITATION] include the space before or after
    body_text = doc['body_text']
    citations = doc['refined_citations']
    sentence_data = []
    sent_index = -1
    for paragraph in body_text:
        sentence_list = sentence_tokenizer.sentence_tokenize(paragraph)
        for i, sentence in enumerate(sentence_list):
            sent_index += 1
            if i == len(sentence)-1:    # last sentence
                sent_after = []
            else:
                sent_after = [remove_ref(re.sub(marker_pattern, ' ', x)) for x in sentence_list[i+1:]]  # remove [CITATION*] and other ref marker
            sent_before = [remove_ref(re.sub(marker_pattern, ' ', x)) for x in sentence_list[:i]]   # remove [CITATION*] and other ref marker
            if re.search(marker_pattern, sentence):
                label = 1
                # generate data for string detection dictionary
                candidate_sent = re.sub(known_ref_pattern, ' ', sentence)   # remove ref marker for unknown reference
                citing_sentences = get_citation_num(candidate_sent, list(citations.keys())[0])
                for citing in citing_sentences:
                    cited_index = 'b'+citing['ref_num'].replace('#b','').strip()
                    if cited_index not in citations:
                        continue
                    ref = citations[cited_index]
                    if ref == {}:
                        continue
                    if 'sentences' not in ref:
                        citations[cited_index].update({'sentences':[citing['sent']]})
                    else:
                        citations[cited_index]['sentences'].append(citing['sent'])
            else:
                label = 0
            sent = remove_ref(re.sub(marker_pattern, ' ', sentence))    # remove [CITATION*] and other ref marker
            sent_dict = {'doc_id':doc_id, 'sent_index': sent_index, 'sentence':sent, 'sent_before':sent_before, 'sent_after':sent_after, 'label':label}
            sentence_data.append(sent_dict)
        doc['refined_citations'] = citations
    return sentence_data, doc
            
def output_cited_sentence_json(doc, filename):
    data = []
    for x in doc['refined_citations']:
        if 'sentences' in doc['refined_citations'][x] and doc['refined_citations'][x]['title'] != '' and doc['refined_citations'][x]['authors']!='':
            data.append({k:v for k,v in doc['refined_citations'][x].items() if k in ['title', 'authors', 'year', 'journal', 'volume', 'pages', 'publisher','citation', 'sentences']})
    if data == [] or data == [{}]:
        return 0
    else:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return 1

def remove_ref(text):
    bracket = r'\s+\[[\d,\-\u2013\s]+\]'    # "[1,2]", "[1]", "[1-10]"
    author = r"(?:[A-Z][A-Za-zäëøñéβ'`-]+)"   # World with first letter upper case, for author detection, "James"
    etal = r"(?:et al\.?)"  # "et al."
    additional = f"(?:,? (?:(?:and |& )?{author}|{etal}))"
    year_num = "(?:15|16|17|18|19|20|21|22)[0-9][0-9]"    # year number, "2020"
    page_num = "(?:, p\.? [0-9]+)?"  # page number, Always optional
    year = fr"(?:,? *{year_num}{page_num}| *\({year_num}{page_num}\))" # year with "," or not
    regex = fr'\b(?!(?:Although|Also|Following)\b){author}{additional}*{year}'
    # "James (2020)", "Smith et al. (2020)", "Green, 2010", "Green 2020", "Green, Smith and Zhang (2022)"
    text = re.sub(regex, "", text)
    text = re.sub(bracket, "", text)
    regex = fr"\(\[CITATION\]\)|\(\[CITATION\]|\[CITATION\]\)|\[CITATION\]" #"([CITATION])", "([CITATION]", "[CITATION])", "[CITATION]"
    text = re.sub(regex, "", text)
    punctuation = ",\s?,\s?,?|\;\s?;\s?;?"      # ",,,", ",,", ", , ,,", ";;", ";;;", "; ; ;;" continuously punctuation 
    text = re.sub(punctuation, "", text)
    bracket_pattern = re.compile("\[(\s+|((\s+)?;(\s+)?))?\]|\((\s+|((\s+)?;(\s+)?))?\)")
    # "()", "( )", "(; )", "( ; )", "(   )","(;)", "[]", "[ ]", "[; ]", "[ ; ]", "[    ]", "[;]"
    text = re.sub(bracket_pattern, "", text)
    bracket_pattern = re.compile("\[(\s+|((\s+)?,(\s+)?))?\]|\((\s+|((\s+)?,(\s+)?))?\)")
    # "()", "( )", "(, )", "( , )", "(   )","(,)", "(,,,)", "[]", "[ ]", "[, ]", "[ , ]", "[    ]", "[,]", "[,,,]"
    text = re.sub(bracket_pattern, "", text)
    return text


def process_xml_fromPDF_singlefile(xml_path, output_cited_sent, output_model_dataset, output_header):
    print(xml_path)
    try:
        doc = parse_tei_xml(xml_path)
    except Exception:
        print('Cannot parse file:', xml_path, flush=True)
        return 0

    doc_id = xml_path.split('/')[-2]+"_"+xml_path.split('/')[-1][:-4]      # folder+file_index
    doc_header = doc['header']
    header_info = {}
    if 'title' not in doc_header:   # if cannot get the title, get rid of the article
        print('Cannot get title of this article:', xml_path, flush=True)
        return 0
    else:
        title = doc_header['title'].lower()
        if title.endswith('.'):
            title=title[:-1]
        title.replace('-',' ')
        header_info.update({'title':title})
    if 'authors' not in doc_header:   # no author information, get rid of the article
        print('Cannot get authors of this article:', xml_path, flush=True)
        return 0
    else:
        header_info.update({'authors':doc_header['authors']})
    if 'doi' in doc_header:
        header_info.update({'doi':doc_header['doi']})
    if 'issn' in doc_header:
        header_info.update({'issn':doc_header['issn']})
    sentence_data, doc = generate_data_for_CitingSentenceModel(doc, doc_id)
    if len(sentence_data) < 10:    # if document contains less than 10 sentence, discard this document and treat as empty file
        print('Empty file:', xml_path, flush=True)
        return 0
    cited_article = output_cited_sentence_json(doc, os.path.join(output_cited_sent, xml_path.split('/')[-1][:-4]+".json"))
    if cited_article:
        with open(os.path.join(output_model_dataset,xml_path.split('/')[-1][:-4]+".json"),'w') as f:
            json.dump(sentence_data, f, indent=4, ensure_ascii=False)
        with open(os.path.join(output_header,xml_path.split('/')[-1][:-4]+".json"),'w') as f:
            json.dump(header_info, f, indent=4, ensure_ascii=False)
    else:
        print('No citing sentence found:', xml_path, flush=True)
        return 0
    return 1


def process_xml_fromPDF(input_xml, output_cited_sent_fold, output_model_dataset_fold, output_header_fold, overwrite=0):
    if not os.path.isdir(output_cited_sent_fold):
        os.mkdir(output_cited_sent_fold)
    if not os.path.isdir(output_model_dataset_fold):
        os.mkdir(output_model_dataset_fold)
    if not os.path.isdir(output_header_fold):
        os.mkdir(output_header_fold)
    
    for input_path in glob.glob(input_xml+'*/'):
        journal = input_path[:-1].split('/')[-1]
        if not journal.startswith('17') and not journal.startswith('16') and not journal.startswith('15') and not journal.startswith('14') and not journal.startswith('13'):
            continue
        output_cited_sent = os.path.join(output_cited_sent_fold, journal)
        output_model_dataset = os.path.join(output_model_dataset_fold,journal)
        output_header = os.path.join(output_header_fold,journal)
        if not os.path.isdir(output_cited_sent):
            os.mkdir(output_cited_sent)
        if not os.path.isdir(output_model_dataset):
            os.mkdir(output_model_dataset)
        if not os.path.isdir(output_header):
            os.mkdir(output_header)
        for xml_path in glob.glob(input_path+"*.xml"):
            if overwrite==0 and os.path.isfile(os.path.join(output_cited_sent, xml_path.split('/')[-1][:-4]+".json")):
                continue
            process_xml_fromPDF_singlefile(xml_path, output_cited_sent, output_model_dataset, output_header)
        print(journal, 'finished.', flush=True)
    print('Job finished!')
    


if __name__ == "__main__":
    input_xml = "/data/yuan/10.citation/1.data/XML/xml_new/"
    output_cited_sent_fold = "/data/yuan/10.citation/1.data/cited_sent/statJournal_new/"
    output_model_dataset_fold = "/data/yuan/10.citation/1.data/model_dataset/statJournal_new/"
    output_header_fold = "/data/yuan/10.citation/1.data/header_info/statJournal_new/"
    process_xml_fromPDF(input_xml, output_cited_sent_fold, output_model_dataset_fold, output_header_fold)




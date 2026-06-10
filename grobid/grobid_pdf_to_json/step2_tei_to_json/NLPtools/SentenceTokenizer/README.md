# Sentence Tokenizer

A sentence tokenizer splits input text into sentences.

## Usage

To use a sentence tokenizer (Use LitcoinSentenceTokenizer as an example)

```python
>>> from NLPtools.SentenceTokenizer import LitcoinSentenceTokenizer as SentenceTokenizer

>>> sentence_tokenizer = SentenceTokenizer()
>>> sentence_tokenizer.sentence_tokenize("This is a sentence. This is another.")

["This is a sentence.", "This is another."]

>>> sentence_tokenizer.sentence_tokenize_with_offsets("This is a sentence. This is another.")

[{'text': 'This is a sentence.', 'offset_start': 0, 'offset_end': 19}, {'text': 'This is another.', 'offset_start': 20, 'offset_end': 36}]
```

## Adding More Tokenizers

A sentence tokenizer should be implemented in the following way

```python
from NLPtools.SentenceTokenizer.SentenceTokenizer import SentenceTokenizerBase 

class MySentenceTokenizer(SentenceTokenizerBase):
    
    def sentence_tokenize(text):
        # Code here to tokenize sentence
        pass
    
    def sentence_tokenize_with_offsets(text):
        # code here to tokenize sentence and also return offsets
        pass
```

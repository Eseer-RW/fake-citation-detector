from __future__ import annotations
import re
import logging
from collections.abc import Iterable
import nltk
from nltk.corpus import words

from .SentenceTokenizer import SentenceTokenizeWithOffsets, SentenceTokenizerBase

logger = logging.getLogger(__name__)


class LitcoinSentenceTokenizer(SentenceTokenizerBase):
    """
    Sentence tokenizer used for the Litcoin challenge.
    """

    def __init__(self) -> None:
        super().__init__()
        nltk.download("punkt")
        nltk.download("words")
        self.extra_abbreviations = set(
            [
                "e.g",
                "i.e",
                "i.m",
                "a.u",
                "p.o",
                "i.v",
                "i.p",
                "vivo",
                "p.o",
                "i.p",
                "Vmax",
                "i.c.v",
                ")(",
                "E.C",
                "sp",
                "al",
            ]
        )
        self.nltk_tokenizer = nltk.data.load("tokenizers/punkt/english.pickle")
        self.nltk_tokenizer._params.abbrev_types.update(self.extra_abbreviations)

    def add_extra_abbreviations(
        self, abbreviations: Iterable[str], warn_trailing_dots: bool = True
    ) -> None:
        """Add extra abbreviations to the underlying NLTK tokenizer.

        Extra abbreviations are tokens that the tokenizer shouldn't split on.

        Example: Coding languages, e.g. C, is good for human race.
        This sentence shouldn't be split right after abbreviation e.g.

        Current abbreviations added can be accessed via `LitcoinSentenceTokenizer.extra_abbreviations`.
        Added abbreviations should not include the trailing dot.

        Example: To add e.g., one should only include e.g

        Args:
            abbreviations (Iterable[str]): An iterable (list, set, etc) of abbreviations to add into the tokenizer.
            warn_trailing_dots (bool): Warn users about abbreviations having a trailing dot. Defaults to True.
        """

        for abbreviation in abbreviations:
            if abbreviation.endswith(".") and warn_trailing_dots:
                logger.warn(
                    f"Found an abbreviation with a trailing dot: {abbreviation} ."
                    " Please read the documentation for details. You can suppress this"
                    " warning by adding `warn_trailing_dots=False`"
                )
            self.extra_abbreviations.add(abbreviation)

        self.nltk_tokenizer._params.abbrev_types.update(abbreviations)

    def sentence_tokenize(self, text: str) -> list[str]:
        # This tokenizer doesn't have a different method for splitting without getting offsets
        split_sentences_with_offsets = self.sentence_tokenize_with_offsets(text)
        sentences = [
            sentence_with_offset["text"]
            for sentence_with_offset in split_sentences_with_offsets
        ]
        return sentences

    def sentence_tokenize_with_offsets(
        self, text: str
    ) -> list[SentenceTokenizeWithOffsets]:
        sentences = self.nltk_tokenizer.tokenize(text)

        sentences_with_offsets_list = []
        prev_end = 0
        for sent in sentences:
            start = text.find(sent, prev_end)
            end = start + len(sent)
            sentences_with_offsets_list.append(
                dict(text=sent, offset_start=start, offset_end=end)
            )
            prev_end = end

        refined_sentences_with_offsets_list = self.__post_nltk_processing(
            text, sentences_with_offsets_list
        )
        return refined_sentences_with_offsets_list

    def __post_nltk_processing(
        self, text: str, sentences_with_offsets_list: list[SentenceTokenizeWithOffsets]
    ) -> list[SentenceTokenizeWithOffsets]:
        """Post processing step of the sentence tokenizer.

        Args:
            text: the original input text
            sentences_with_offsets_list (list[SentenceTokenizeWithOffsets]): A list of sentences before refineing

        Returns:
            list[SentenceTokenizeWithOffsets]: A list of setntences after refining
        """
        ret = []
        prev_sent = ""
        prev_start = 0
        prev_end = 0
        MERGE = 0
        for tokenized_sentence in sentences_with_offsets_list:
            this_sent, this_start, this_end = (
                tokenized_sentence["text"].strip(),
                tokenized_sentence["offset_start"],
                tokenized_sentence["offset_end"],
            )
            if MERGE == 1:
                ret.append(
                    dict(
                        text=(prev_sent + text[prev_end:this_start] + this_sent).strip(),
                        offset_start=prev_start,
                        offset_end=this_end,
                    )
                )

                logger.debug("Fixed an error by concatenating two sentences below:")
                logger.debug(f"SENTENCE 1: {prev_sent}\t{prev_start}\t{prev_end}")
                logger.debug(f"SENTENCE 2: {this_sent}\t{this_start}\t{this_end}")
                logger.debug(
                    "MERGED:"
                    f" {prev_sent}{text[prev_end : this_start]}{this_sent}\t{prev_start}\t{this_end}"
                )

                MERGE = 0
                continue

            p = re.compile(r"[\.\?]\s([A-Z][a-z]*)\s")
            result = p.search(this_sent)
            if result is not None:
                tmpW = result.group(1)
                if tmpW != "" and tmpW.lower() in words.words():
                    # we should split this case
                    pos = re.search(r"[\.\?]\s([A-Z][a-z]*)\s", this_sent)
                    pos = pos.span()[0]  # TODO: pos may be None. Fix this
                    sent1 = this_sent[0 : pos + 1]
                    sent2 = this_sent[pos + 1 :]
                    ret.append(
                        dict(
                            text=sent1.strip(),
                            offset_start=this_start,
                            offset_end=this_start + pos + 1,
                        )
                    )
                    tmp = sent2.lstrip()
                    diff = len(sent2) - len(tmp)
                    ret.append(
                        dict(
                            text=tmp.strip(),
                            offset_start=this_start + pos + 1 + diff,
                            offset_end=this_end,
                        )
                    )

                    logger.debug("Fixed an error by splitting a sentence as below:")
                    logger.debug(f"{this_sent}\t{this_start}\t{this_end}")
                    logger.debug(
                        f"Split sentence 1: {sent1}\t{this_start}\t{this_start + pos}"
                    )
                    logger.debug(
                        f"Split sentence 2: {sent2}\t{this_start + pos + diff}\t{this_end}"
                    )
                else:
                    ret.append(
                        dict(
                            text=this_sent.strip(), offset_start=this_start, offset_end=this_end
                        )
                    )
            else:
                if this_sent.endswith(")"):
                    MERGE = 1
                    # the newline character needs to be removed
                    prev_sent = this_sent
                    prev_start = this_start
                    prev_end = this_end
                else:
                    ret.append(
                        dict(
                            text=this_sent.strip(), offset_start=this_start, offset_end=this_end
                        )
                    )
        return ret

from __future__ import annotations
#from typing import TypedDict
from typing_extensions import TypedDict


class SentenceTokenizeWithOffsets(TypedDict):
    """
    A dictionary with the following 3 fields:
    - text (str): sentence
    - offset_start (int): starting offset
    - offset_end (int): ending offset
    """

    text: str
    offset_start: int
    offset_end: int


class SentenceTokenizerBase(object):
    """The base sentence tokenizer class. A template for all other tokenizers. Do not call it directly."""

    def __init__(self) -> None:
        # If the user calles `tokenizer = SentenceTokenizerBase()` directly, we should throw an error.
        if self.__class__.__name__ == "SentenceTokenizerBase":
            raise NotImplementedError(
                "SentenceTokenizerBase should not be called directly. Please call other"
                " sentence tokenizers in the package."
            )

    def sentence_tokenize(self, text: str) -> list[str]:  # type: ignore
        """The main class for tokenizing a sentence.

        Args:
            text (str): Input text for tokenization
            with_offsets (bool)

        Returns:
            list[str]: A list of tokenized sentence.
            In case `text` is empty, the function should return an empty list
        """
        pass

    def sentence_tokenize_with_offsets(
        self, text: str
    ) -> list[SentenceTokenizeWithOffsets]:  # type: ignore
        """The main class for tokenizing a sentence, and return the offsets as well.

        Args:
            text (str): Input text for tokenization

        Returns:
            list[SentenceTokenizeWithOffsets]: A list of tokenized sentence,
            each is a dictionary of type `SentenceTokenizeWithOffsets`,
            including the following fields:
            - text (str): tokenized sentence
            - offset_start (int): starting offset
            - offset_end (int): ending offset
        """
        pass

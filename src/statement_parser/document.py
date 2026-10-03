"""A thin, lazy wrapper around a PDF that parsers read from.

Parsers should prefer ``Page.text`` and ``Page.lines``. Those work for both
real PDFs and documents built with :meth:`Document.from_text`, which is how the
test suite feeds synthetic statements to parsers without committing real PDFs.
Parsers that need character positions can use ``Page.plumber`` but then lose
that test path.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import IO, Any, Union

Source = Union[str, "os.PathLike[str]", bytes, IO[bytes]]


class Page:
    def __init__(self, document: Document, index: int, text: str | None = None) -> None:
        self._document = document
        self.index = index
        self._text = text

    @property
    def number(self) -> int:
        """1-based page number."""
        return self.index + 1

    @property
    def plumber(self) -> Any:
        """The underlying ``pdfplumber.page.Page``, or ``None`` for text documents."""
        pdf = self._document._open_pdf()
        return None if pdf is None else pdf.pages[self.index]

    @property
    def text(self) -> str:
        if self._text is None:
            page = self.plumber
            # Chase (and others) draw bold text twice with a tiny offset, which
            # extracts as "AACCCCOOUUNNTT". dedupe_chars collapses it.
            self._text = "" if page is None else (page.dedupe_chars().extract_text() or "")
        return self._text

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    def __repr__(self) -> str:
        return f"<Page {self.number}>"


class Document:
    """A statement document. Open with :meth:`open` or :meth:`from_text`."""

    def __init__(
        self,
        *,
        pdf: Any = None,
        texts: Sequence[str] | None = None,
        name: str | None = None,
    ) -> None:
        self._pdf = pdf
        self.name = name
        if texts is not None:
            self.pages = [Page(self, i, t) for i, t in enumerate(texts)]
        elif pdf is not None:
            self.pages = [Page(self, i) for i in range(len(pdf.pages))]
        else:
            raise ValueError("Document needs either a pdf or texts")

    @classmethod
    def open(cls, source: Source, password: str | None = None) -> Document:
        import pdfplumber

        name: str | None = None
        if isinstance(source, (str, os.PathLike)):
            name = os.fspath(source)
            pdf = pdfplumber.open(Path(source), password=password)
        elif isinstance(source, (bytes, bytearray)):
            pdf = pdfplumber.open(io.BytesIO(source), password=password)
        else:
            name = getattr(source, "name", None)
            pdf = pdfplumber.open(source, password=password)
        return cls(pdf=pdf, name=name)

    @classmethod
    def from_text(cls, pages: Sequence[str] | str, name: str | None = None) -> Document:
        """Build a document from already-extracted page text (handy in tests)."""
        if isinstance(pages, str):
            pages = [pages]
        return cls(texts=list(pages), name=name)

    def _open_pdf(self) -> Any:
        return self._pdf

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        """Text of all pages, separated by form feeds."""
        return "\f".join(p.text for p in self.pages)

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._pdf.metadata) if self._pdf is not None else {}

    def iter_lines(self) -> Iterator[tuple[Page, str]]:
        for page in self.pages:
            for line in page.lines:
                yield page, line

    def close(self) -> None:
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Document {self.name or '<memory>'} pages={self.page_count}>"

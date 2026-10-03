"""A thin, lazy wrapper around a PDF that parsers read from.

Parsers should prefer ``Page.text`` and ``Page.lines``. Those work for both
real PDFs and documents built with :meth:`Document.from_text`, which is how the
test suite feeds synthetic statements to parsers without committing real PDFs.
Parsers that need character positions can use ``Page.plumber`` but then lose
that test path.
"""

from __future__ import annotations

import io
import itertools
import os
from collections.abc import Iterator, Sequence
from operator import itemgetter
from pathlib import Path
from typing import IO, Any, Union

Source = Union[str, "os.PathLike[str]", bytes, IO[bytes]]


_PLAIN = (float, int, bool, str, type(None))

# Cleared the first time the fast char path disagrees with pdfplumber, which
# happens on pdfplumber releases older than 0.11.7.
_fast_chars_enabled = True


def _unique_chars(
    chars: list[dict[str, Any]], tolerance: float, extra_attrs: tuple[str, ...]
) -> list[dict[str, Any]]:
    """``chars`` without duplicates, in their original order (pdfplumber's algorithm)."""
    from pdfplumber.utils.clustering import cluster_objects

    key = itemgetter("upright", "text", *extra_attrs)
    pos_key = itemgetter("doctop", "x0")
    keep: set[int] = set()
    for _, group in itertools.groupby(sorted(chars, key=key), key=key):
        for y_cluster in cluster_objects(list(group), itemgetter("doctop"), tolerance):
            for x_cluster in cluster_objects(y_cluster, itemgetter("x0"), tolerance):
                keep.add(id(min(x_cluster, key=pos_key)))
    return [char for char in chars if id(char) in keep]


def dedupe_chars(
    page: Any, tolerance: float = 1, extra_attrs: tuple[str, ...] = ("fontname", "size")
) -> Any:
    """Return ``page`` without duplicate glyphs, like pdfplumber's ``Page.dedupe_chars``.

    The result is identical, but pdfplumber restores char order with
    ``sorted(chars, key=chars.index)``, which is quadratic in the number of chars
    and dominates parse time on dense pages. This filters the original list instead.
    """
    keep = {id(char) for char in _unique_chars(page.chars, tolerance, extra_attrs)}
    return page.filter(lambda obj: obj.get("object_type") != "char" or id(obj) in keep)


def layout_chars(page: Any) -> list[dict[str, Any]]:
    """The char dicts of a ``pdfplumber.page.Page``, equal to ``page.chars``.

    Cropped and filtered pages are passed straight to ``page.chars``.

    ``page.chars`` converts every layout object through a generic, recursive
    attribute resolver, which costs about as much as interpreting the page.
    This builds the same dicts for chars only and skips other objects.

    Each page's first char is also built by pdfplumber and compared. On any
    difference, or for settings this does not replicate, it returns
    ``page.chars`` instead.
    """
    global _fast_chars_enabled
    from pdfplumber.page import DerivedPage

    try:
        # Cropped and filtered pages inherit the parent's layout, so only base pages qualify.
        if not _fast_chars_enabled or isinstance(page, DerivedPage) or hasattr(page, "_objects"):
            return list(page.chars)
        # unicode_norm only exists from pdfplumber 0.11.1.
        if getattr(page.pdf, "unicode_norm", None) is not None or page.pdf.laparams is not None:
            return list(page.chars)
        chars, first = _fast_layout_chars(page)
        if first is not None and page.process_object(first) != chars[0]:
            _fast_chars_enabled = False
            return list(page.chars)
        return chars
    except Exception:
        return list(page.chars)


def _fast_layout_chars(page: Any) -> tuple[list[dict[str, Any]], Any]:
    """Char dicts for ``page`` and the first ``LTChar`` they were built from."""
    from pdfminer.layout import LTChar, LTContainer
    from pdfplumber.page import ALL_ATTRS
    from pdfplumber.utils.pdfinternals import resolve_all, resolve_and_decode

    mb_x0, mb_top = page.mediabox[:2]
    height = page.height
    doctop = page.initial_doctop
    page_number = page.page_number
    colorspaces: dict[Any, Any] = {}
    chars: list[dict[str, Any]] = []
    first: list[Any] = []

    def walk(objs: list[Any]) -> None:
        # Mirrors pdfplumber's Page.iter_layout_objects + process_object for LTChar.
        for obj in objs:
            if isinstance(obj, LTContainer):
                walk(obj._objs)
                continue
            if type(obj) is not LTChar:
                continue
            attr = {
                k: v if isinstance(v, _PLAIN) else resolve_all(v)
                for k, v in obj.__dict__.items()
                if k in ALL_ATTRS
            }
            if not isinstance(attr["fontname"], str):
                raise TypeError("non-str fontname")
            attr["object_type"] = "char"
            attr["page_number"] = page_number
            name = obj.ncs.name
            if name not in colorspaces:
                colorspaces[name] = resolve_and_decode(name)
            attr["ncs"] = colorspaces[name]
            attr["text"] = obj.get_text()
            gs = obj.graphicstate
            attr["stroking_color"] = gs.scolor if isinstance(gs.scolor, tuple) else (gs.scolor,)
            attr["non_stroking_color"] = gs.ncolor if isinstance(gs.ncolor, tuple) else (gs.ncolor,)
            top = (height - attr["y1"]) + mb_top
            attr["top"] = top
            attr["bottom"] = (height - attr["y0"]) + mb_top
            attr["doctop"] = doctop + top
            if mb_x0 != 0:
                attr["x0"] = attr["x0"] + mb_x0
                attr["x1"] = attr["x1"] + mb_x0
            if not first:
                first.append(obj)
            chars.append(attr)

    walk(page.layout._objs)
    return chars, (first[0] if first else None)


class Page:
    def __init__(self, document: Document, index: int, text: str | None = None) -> None:
        self._document = document
        self.index = index
        self._text = text
        self._deduped: Any = None

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
    def deduped(self) -> Any:
        """A pdfplumber page holding only this page's chars, duplicates removed.

        Chase (and others) draw bold text twice with a tiny offset, which
        extracts as "AACCCCOOUUNNTT". Deduping collapses it. Lines, rects and
        images are left out; use :attr:`plumber` for those. Cached; ``None``
        for text documents.
        """
        if self._deduped is None:
            page = self.plumber
            if page is None:
                return None
            from pdfplumber.page import FilteredPage

            deduped = FilteredPage(page, lambda obj: True)
            # Same shortcut pdfplumber's own Page.dedupe_chars takes.
            deduped._objects = {"char": _unique_chars(layout_chars(page), 1, ("fontname", "size"))}
            self._deduped = deduped
        return self._deduped

    @property
    def text(self) -> str:
        if self._text is None:
            page = self.deduped
            self._text = "" if page is None else (page.extract_text() or "")
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
            pdf = pdfplumber.open(source, password=password)  # type: ignore[arg-type]
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
            for page in self.pages:
                page._deduped = None

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Document {self.name or '<memory>'} pages={self.page_count}>"

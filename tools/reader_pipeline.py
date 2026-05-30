from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree


BOOK_PDF = Path(
    "Harry Potter and the Sorcerer's Stone, Book 1 (Unabridged)"
    "/Harry Potter and the Sorcerer's Sto (2597)"
    "/Harry Potter and the Sorcerer's Stone.pdf"
)
AUDIO_DIR = Path("Harry Potter and the Sorcerer's Stone, Book 1 (Unabridged)")
OUTPUT_DIR = Path("aligned_reader")
DEFAULT_BOOK_TITLE = "Harry Potter and the Sorcerer's Stone"
COLLAPSE_EPUB = Path(
    "CollapseComplexSocieties"
    "/The Collapse of Complex Societies ( (232)"
    "/The Collapse of Complex Societi - Joseph A. Tainter.epub"
)
COLLAPSE_PDF = Path("The Collapse of Complex Societi - Joseph A. Tainter.pdf")
COLLAPSE_AUDIO_DIR = Path("CollapseComplexSocieties")

CHAPTER_WORDS = [
    "ONE",
    "TWO",
    "THREE",
    "FOUR",
    "FIVE",
    "SIX",
    "SEVEN",
    "EIGHT",
    "NINE",
    "TEN",
    "ELEVEN",
    "TWELVE",
    "THIRTEEN",
    "FOURTEEN",
    "FIFTEEN",
    "SIXTEEN",
    "SEVENTEEN",
    "EIGHTEEN",
    "NINETEEN",
    "TWENTY",
    "TWENTY-ONE",
    "TWENTY-TWO",
    "TWENTY-THREE",
    "TWENTY-FOUR",
    "TWENTY-FIVE",
    "TWENTY-SIX",
    "TWENTY-SEVEN",
    "TWENTY-EIGHT",
    "TWENTY-NINE",
    "THIRTY",
    "THIRTY-ONE",
    "THIRTY-TWO",
    "THIRTY-THREE",
    "THIRTY-FOUR",
    "THIRTY-FIVE",
    "THIRTY-SIX",
    "THIRTY-SEVEN",
    "THIRTY-EIGHT",
]
WORD_TO_NUMBER = {word: index for index, word in enumerate(CHAPTER_WORDS, start=1)}
CHAPTER_TITLES = {
    1: "Dudley Demented",
    2: "A Peck of Owls",
    3: "The Advance Guard",
    4: "Number Twelve, Grimmauld Place",
    5: "The Order of the Phoenix",
    6: "The Noble and Most Ancient House of Black",
    7: "The Ministry of Magic",
    8: "The Hearing",
    9: "The Woes of Mrs. Weasley",
    10: "Luna Lovegood",
    11: "The Sorting Hat's New Song",
    12: "Professor Umbridge",
    13: "Detention with Dolores",
    14: "Percy and Padfoot",
    15: "The Hogwarts High Inquisitor",
    16: "In the Hog's Head",
    17: "Educational Decree Number Twenty-Four",
    18: "Dumbledore's Army",
    19: "The Lion and the Serpent",
    20: "Hagrid's Tale",
    21: "The Eye of the Snake",
    22: "St. Mungo's Hospital for Magical Maladies and Injuries",
    23: "Christmas on the Closed Ward",
    24: "Occlumency",
    25: "The Beetle at Bay",
    26: "Seen and Unforeseen",
    27: "The Centaur and the Sneak",
    28: "Snape's Worst Memory",
    29: "Career Advice",
    30: "Grawp",
    31: "O.W.L.s",
    32: "Out of the Fire",
    33: "Fight and Flight",
    34: "The Department of Mysteries",
    35: "Beyond the Veil",
    36: "The Only One He Ever Feared",
    37: "The Lost Prophecy",
    38: "The Second War Begins",
}

SORCERERS_STONE_CHAPTER_TITLES = {
    1: "The Boy Who Lived",
    2: "The Vanishing Glass",
    3: "The Letters from No One",
    4: "The Keeper of the Keys",
    5: "Diagon Alley",
    6: "The Journey from Platform Nine and Three-quarters",
    7: "The Sorting Hat",
    8: "The Potions Master",
    9: "The Midnight Duel",
    10: "Halloween",
    11: "Quidditch",
    12: "The Mirror of Erised",
    13: "Nicolas Flamel",
    14: "Norbert the Norwegian Ridgeback",
    15: "The Forbidden Forest",
    16: "Through the Trapdoor",
    17: "The Man with Two Faces",
}


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    body: str


@dataclass(frozen=True)
class BookConfig:
    title: str
    chapter_titles: dict[int, str]
    chapter_count: int


@dataclass(frozen=True)
class AudioChapterSpan:
    number: int
    title: str
    spine_index: int
    start: float
    end: float


@dataclass(frozen=True)
class EpubText:
    title: str
    text: str


@dataclass(frozen=True)
class TainterChapterSpec:
    number: int
    marker_title: str
    display_title: str
    audio_parts: tuple[int, ...]


DEFAULT_BOOK_CONFIG = BookConfig(
    title=DEFAULT_BOOK_TITLE,
    chapter_titles=SORCERERS_STONE_CHAPTER_TITLES,
    chapter_count=len(SORCERERS_STONE_CHAPTER_TITLES),
)

TAINTER_CHAPTER_SPECS = [
    TainterChapterSpec(1, "Introduction to collapse", "Introduction to Collapse", (2,)),
    TainterChapterSpec(2, "The nature of complex societies", "The Nature of Complex Societies", (3,)),
    TainterChapterSpec(3, "The study of collapse", "The Study of Collapse, Part One", (4, 5)),
    TainterChapterSpec(
        4,
        "Understanding collapse: the marginal productivity of sociopolitical change",
        "Understanding Collapse: The Marginal Productivity of Sociopolitical Change, Part One",
        (6,),
    ),
    TainterChapterSpec(
        5,
        "Evaluation: complexity and marginal returns in collapsing societies",
        "Evaluation: Complexity and Marginal Returns in Collapsing Societies, Part One",
        (7, 8, 9),
    ),
    TainterChapterSpec(6, "Summary and implications", "Summary and Implications", (10,)),
]
READER_TIMESTAMP_OVERRIDES = [
    {
        "chapter": 5,
        "text_prefix": "Over the short-term the collapse probably resulted in an improved standard of living",
        "local_begin": 2 * 3600 + 15 * 60 + 19,
    }
]


def clean_text(text: str) -> str:
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x0c", "\n\n")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", " - ")
        .replace("\u2013", "-")
        .replace("\x91", "")
        .replace("\x92", "")
        .replace("", "")
        .replace("", "")
    )


class EpubHtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg"}:
            self.skip_stack.append(tag)
            return
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_stack and self.skip_stack[-1] == tag:
            self.skip_stack.pop()
            return
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        value = html.unescape(data)
        if value.strip():
            self.parts.append(value)

    def text(self) -> str:
        lines = []
        for line in "".join(self.parts).splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)


def extract_epub_text(epub_path: Path) -> EpubText:
    if not epub_path.exists():
        raise RuntimeError(f"EPUB not found: {epub_path}")
    with zipfile.ZipFile(epub_path) as archive:
        opf_path = epub_opf_path(archive)
        opf_root = ElementTree.fromstring(archive.read(opf_path))
        title = epub_title(opf_root) or epub_path.stem
        manifest = epub_manifest(opf_root)
        spine_hrefs = epub_spine_hrefs(opf_root, manifest)
        opf_dir = Path(opf_path).parent
        texts = []
        for href in spine_hrefs:
            html_path = (opf_dir / href).as_posix()
            if html_path not in archive.namelist():
                continue
            texts.append(html_to_text(archive.read(html_path).decode("utf-8", errors="ignore")))
    text = "\n\n".join(part for part in texts if part.strip())
    if not text.strip():
        raise RuntimeError(f"No readable text found in EPUB: {epub_path}")
    return EpubText(title=title, text=clean_text(text))


def extract_pdf_reader_text(pdf_path: Path) -> EpubText:
    if not pdf_path.exists():
        raise RuntimeError(f"PDF not found: {pdf_path}")
    title = pdf_metadata_title(pdf_path) or pdf_path.stem
    result = subprocess.run(
        ["pdftotext", "-raw", str(pdf_path), "-"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    text = normalize_pdf_reader_text(result.stdout)
    if not text.strip():
        raise RuntimeError(f"No readable text found in PDF: {pdf_path}")
    return EpubText(title=title, text=text)


def normalize_pdf_reader_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in clean_text(text).splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            lines.append("")
            continue
        if lines and lines[-1] and should_join_pdf_line(lines[-1], line):
            separator = "" if lines[-1].endswith("-") and line[:1].islower() else " "
            if separator == "":
                lines[-1] = lines[-1][:-1] + line
            else:
                lines[-1] = lines[-1] + separator + line
        else:
            lines.append(line)
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def should_join_pdf_line(previous: str, current: str) -> bool:
    if previous.endswith("-") and current[:1].islower():
        return True
    if re.search(r"\b[A-Z]\.$", previous):
        return True
    if current[:1].islower():
        return True
    if previous.rstrip().endswith((".", "?", "!", ";", ":")):
        return False
    if looks_like_short_pdf_heading(previous) and re.match(r"[A-Z]", current):
        return False
    return True


def looks_like_short_pdf_heading(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z.'-]*", text)
    return 0 < len(words) <= 5 and len(text) <= 45


def pdf_metadata_title(pdf_path: Path) -> str | None:
    result = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Title:"):
            title = re.sub(r"\s+", " ", line.removeprefix("Title:")).strip()
            return title or None
    return None


def extract_reader_source_text(source_path: Path) -> EpubText:
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_reader_text(source_path)
    if suffix == ".epub":
        return extract_epub_text(source_path)
    raise RuntimeError(f"Unsupported reader source format: {source_path}")


def epub_opf_path(archive: zipfile.ZipFile) -> str:
    try:
        root = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    except KeyError:
        opf_files = [name for name in archive.namelist() if name.lower().endswith(".opf")]
        if not opf_files:
            raise RuntimeError("No OPF package file found in EPUB")
        return opf_files[0]
    for element in root.iter():
        if local_name(element.tag) == "rootfile":
            full_path = element.attrib.get("full-path")
            if full_path:
                return full_path
    raise RuntimeError("No rootfile entry found in EPUB container")


def epub_title(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        if local_name(element.tag) == "title" and element.text and element.text.strip():
            return re.sub(r"\s+", " ", element.text).strip()
    return None


def epub_manifest(root: ElementTree.Element) -> dict[str, str]:
    manifest = {}
    for element in root.iter():
        if local_name(element.tag) != "item":
            continue
        item_id = element.attrib.get("id")
        href = element.attrib.get("href")
        if item_id and href:
            manifest[item_id] = href
    return manifest


def epub_spine_hrefs(root: ElementTree.Element, manifest: dict[str, str]) -> list[str]:
    hrefs = []
    for element in root.iter():
        if local_name(element.tag) != "itemref":
            continue
        idref = element.attrib.get("idref")
        href = manifest.get(idref or "")
        if href and re.search(r"\.(?:x?html?)$", href, flags=re.IGNORECASE):
            hrefs.append(href)
    if not hrefs:
        hrefs = [href for href in manifest.values() if re.search(r"\.(?:x?html?)$", href, flags=re.IGNORECASE)]
    return hrefs


def html_to_text(source: str) -> str:
    parser = EpubHtmlTextParser()
    parser.feed(source)
    return parser.text()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def normalize_chapter_word(raw: str) -> str:
    return re.sub(r"\s+", "-", raw.strip().replace("—", "-").replace("–", "-")).upper()


def parse_chapter_number(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return WORD_TO_NUMBER.get(normalize_chapter_word(raw))


def display_chapter_word(number: int) -> str:
    return CHAPTER_WORDS[number - 1].replace("-", " ").title()


def title_case_heading(raw: str) -> str:
    words = " ".join(raw.split()).title().split()
    small_words = {"A", "An", "And", "At", "By", "For", "In", "Of", "On", "The", "To"}
    title = " ".join(
        word.lower() if index > 0 and word in small_words else word for index, word in enumerate(words)
    )
    title = title.replace("'S", "'s")
    title = title.replace("O.W.L.S", "O.W.L.s")
    title = title.replace("St. Mungo'S", "St. Mungo's")
    title = title.replace("Dumbledore'S", "Dumbledore's")
    title = title.replace("Snape'S", "Snape's")
    return title


def extract_chapters(
    raw_text: str,
    chapter_titles: dict[int, str] | None = None,
    chapter_count: int | None = None,
) -> list[Chapter]:
    titles = chapter_titles or CHAPTER_TITLES
    count = chapter_count or len(titles)
    text = clean_text(raw_text)
    heading_re = re.compile(
        r"(?im)^[^\w\n]*CHAPTER[ \t]+"
        r"(\d+|[A-Za-z]+(?:[ \t]+[A-Za-z]+|-[A-Za-z]+)?)"
        r"(?:[ \t]*[-:–—][^\n]*)?[^\w\n]*$"
    )
    matches = []
    expected_next = 1
    for match in heading_re.finditer(text):
        number = parse_chapter_number(match.group(1))
        if not matches and number is not None and number != expected_next:
            expected_next = number
        if number == expected_next:
            matches.append(match)
            expected_next += 1
        if expected_next > count:
            break

    chapters: list[Chapter] = []
    for index, match in enumerate(matches):
        number = parse_chapter_number(match.group(1))
        if number is None:
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : next_start]
        title, body = split_title_and_body(section, expected_title=titles.get(number))
        body = trim_back_matter(body)
        chapters.append(Chapter(number=number, title=title or titles[number], body=body.strip()))

    return chapters


def trim_back_matter(body: str) -> str:
    back_matter_re = re.compile(
        r"(?im)^\s*(?:"
        r"Titles available in\b.*|"
        r"Read on for the first chapter\b.*|"
        r"Text copyright\b.*"
        r")$"
    )
    match = back_matter_re.search(body)
    return body[: match.start()] if match else body


def looks_like_chapter_start(text: str, heading_end: int) -> bool:
    for line in text[heading_end:].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        letters = [char for char in stripped if char.isalpha()]
        if not letters:
            return False
        uppercase = sum(1 for char in letters if char.isupper())
        return uppercase / len(letters) >= 0.7
    return False


def split_title_and_body(section: str, expected_title: str | None = None) -> tuple[str, str]:
    lines = section.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1

    if expected_title:
        normalized_expected = normalize_title_for_match(expected_title)
        search_index = index
        while search_index < len(lines):
            line = lines[search_index].strip()
            matched_end = expected_title_match_end(lines, search_index, normalized_expected)
            if matched_end is not None:
                index = matched_end + 1
                while index < len(lines) and not lines[index].strip():
                    index += 1
                return expected_title, "\n".join(lines[index:])
            if line and not looks_like_title_line(line) and search_index > index + 8:
                break
            search_index += 1

    title_lines: list[str] = []
    while index < len(lines):
        line = lines[index].strip()
        if not title_lines and not looks_like_title_line(line):
            break
        if not line and title_lines:
            index += 1
            break
        if line:
            title_lines.append(line)
        index += 1

    return title_case_heading(" ".join(title_lines)), "\n".join(lines[index:])


def expected_title_match_end(lines: Sequence[str], start: int, normalized_expected: str) -> int | None:
    if not lines[start].strip():
        return None

    collected: list[str] = []
    for offset in range(0, 6):
        index = start + offset
        if index >= len(lines):
            return None
        line = lines[index].strip()
        if not line:
            return None
        collected.append(line)
        if normalize_title_for_match(" ".join(collected)) == normalized_expected:
            return index
    return None


def normalize_title_for_match(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", clean_text(value).upper())


def looks_like_title_line(line: str) -> bool:
    letters = [char for char in line if char.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for char in letters if char.isupper())
    if uppercase / len(letters) >= 0.7:
        return True
    return looks_like_title_case_line(line)


def looks_like_title_case_line(line: str) -> bool:
    line = clean_text(line).strip()
    if len(line) > 90 or line.endswith((".", "?", "!", ",", ";", ":")):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z.']*", line)
    if not words or len(words) > 12:
        return False
    small_words = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to"}
    return all(word[0].isupper() or word.lower() in small_words for word in words)


def normalize_paragraphs(body: str, running_headers: Iterable[str] = ()) -> list[str]:
    header_set = {header.strip().upper() for header in running_headers}
    lines = []
    for raw_line in clean_text(body).splitlines():
        line = raw_line.strip()
        if is_page_artifact(line):
            continue
        if is_running_header(line, header_set):
            continue
        line = re.sub(r"\b([A-Z])\s{2,}([a-z])", r"\1\2", line)
        lines.append((raw_line, line))

    paragraphs: list[str] = []
    current: list[str] = []
    current_started_indented = False
    for raw_line, line in lines:
        if not line:
            flush_paragraph(current, paragraphs)
            current = []
            current_started_indented = False
            continue
        line_starts_indented = is_indented_paragraph_start(raw_line)
        if current and line_starts_indented:
            flush_paragraph(current, paragraphs)
            current = []
            current_started_indented = True
        elif current and not current_started_indented and is_left_aligned_paragraph_start_after_terminal(current[-1], raw_line, line):
            flush_paragraph(current, paragraphs)
            current = []
            current_started_indented = False
        elif not current:
            current_started_indented = line_starts_indented
        current.append(line)
    flush_paragraph(current, paragraphs)
    return paragraphs


def is_running_header(line: str, header_set: set[str]) -> bool:
    upper = line.upper()
    if upper in header_set:
        return True
    for header in header_set:
        if re.fullmatch(rf"{re.escape(header)}\s+\d{{1,4}}", upper):
            return True
    return False


def is_page_artifact(line: str) -> bool:
    if not line:
        return False
    if re.fullmatch(r"J\.K\.\s+Rowling\s+HARRY POTTER(?:\s+AND\s+THE\s+[A-Z '\-]+)?", clean_text(line), flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"\d{1,4}", line):
        return True
    if re.fullmatch(r"[·.\- ]*\d{1,4}[·.\- ]*", line):
        return True
    if re.fullmatch(r"\d{1,4}\s+[A-Z][A-Z .'\-]+", clean_text(line)):
        return True
    if re.fullmatch(r"CHAPTER\s+[A-Za-z]+(?:[- ]+[A-Za-z]+)?", line, flags=re.IGNORECASE):
        return True
    return False


def is_indented_paragraph_start(raw_line: str) -> bool:
    expanded = raw_line.expandtabs(4)
    stripped = expanded.lstrip()
    indent = len(expanded) - len(stripped)
    return 2 <= indent <= 5 and bool(stripped)


def is_left_aligned_paragraph_start_after_terminal(previous_line: str, raw_line: str, line: str) -> bool:
    expanded = raw_line.expandtabs(4)
    indent = len(expanded) - len(expanded.lstrip())
    if indent > 1:
        return False
    if not previous_line.rstrip().endswith((".", "?", "!", '."', '?"', '!"')):
        return False
    return bool(re.match(r"[\"']?[A-Z]", line))


def flush_paragraph(lines: list[str], paragraphs: list[str]) -> None:
    if not lines:
        return
    paragraph = lines[0]
    for line in lines[1:]:
        if paragraph.endswith("-") and line[:1].islower():
            trailing_word = paragraph.rsplit(" ", 1)[-1]
            if trailing_word.count("-") > 1:
                paragraph += line
            else:
                paragraph = paragraph[:-1] + line
        else:
            paragraph += " " + line
    paragraph = re.sub(r"\s+", " ", paragraph).strip()
    if paragraph:
        paragraphs.append(paragraph)


def split_text_into_sentences(text: str) -> list[str]:
    fragments: list[str] = []
    abbreviation_re = re.compile(r"\b(?:[A-Z]|Dr|Mr|Mrs|Ms|Prof|St|Jr|Sr|vs|cf|e\.g|i\.e)\.$", flags=re.IGNORECASE)
    for raw_line in clean_text(text).splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if looks_like_standalone_fragment(line):
            fragments.append(line)
            continue
        start = 0
        index = 0
        while index < len(line):
            char = line[index]
            if char not in ".!?":
                index += 1
                continue
            end = index + 1
            while end < len(line) and line[end] in "\"')]}":
                end += 1
            candidate = line[start:end].strip()
            next_char = line[end : end + 1]
            if next_char and not next_char.isspace():
                index += 1
                continue
            if abbreviation_re.search(candidate):
                index += 1
                continue
            if candidate:
                fragments.append(candidate)
            start = end
            while start < len(line) and line[start].isspace():
                start += 1
            index = start
        tail = line[start:].strip()
        if tail:
            fragments.append(tail)
    return [fragment for fragment in fragments if has_word(fragment)]


def looks_like_standalone_fragment(text: str) -> bool:
    if len(text) <= 100 and re.match(r"^(?:\d+[.)]\s+)?[A-Z]", text) and not text.endswith((".", "?", "!")):
        return True
    return False


def has_word(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def split_fragments_by_durations(fragments: Sequence[str], durations: Sequence[float]) -> list[list[str]]:
    if not durations:
        raise RuntimeError("No audio durations supplied")
    if not fragments:
        raise RuntimeError("No text fragments supplied")
    if len(fragments) < len(durations):
        raise RuntimeError(f"Need at least {len(durations)} text fragments, found {len(fragments)}")
    total_duration = sum(max(0.0, duration) for duration in durations)
    if total_duration <= 0:
        raise RuntimeError("Audio durations must be positive")
    buckets: list[list[str]] = []
    cursor = 0
    total_fragments = len(fragments)
    for index, duration in enumerate(durations):
        remaining_buckets = len(durations) - index
        remaining_fragments = total_fragments - cursor
        if index == len(durations) - 1:
            end = total_fragments
        else:
            target = round(total_fragments * (sum(durations[: index + 1]) / total_duration))
            min_end = cursor + 1
            max_end = total_fragments - (remaining_buckets - 1)
            end = max(min_end, min(target, max_end))
        buckets.append(list(fragments[cursor:end]))
        cursor = end
    return buckets


def chapter_fragments(chapter: Chapter) -> list[str]:
    heading = f"Chapter {display_chapter_word(chapter.number)}. {chapter.title}."
    return [heading, *normalize_paragraphs(chapter.body, running_headers=running_headers_for(chapter))]


def running_headers_for(chapter: Chapter) -> set[str]:
    headers = {chapter.title.upper()}
    headers.update(title.upper() for title in CHAPTER_TITLES.values())
    headers.update(title.upper() for title in SORCERERS_STONE_CHAPTER_TITLES.values())
    headers.add("THE ADVANCED GUARD")
    return headers


def write_chapter_text_files(chapters: Sequence[Chapter], text_dir: Path) -> list[Path]:
    text_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for chapter in chapters:
        fragments = chapter_fragments(chapter)
        path = text_dir / f"chapter_{chapter.number:03d}.txt"
        path.write_text("\n".join(fragments) + "\n", encoding="utf-8")
        written.append(path)
    return written


def write_full_text_file(chapters: Sequence[Chapter], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fragments = []
    for chapter in chapters:
        fragments.extend(chapter_fragments(chapter))
    path.write_text("\n".join(fragments) + "\n", encoding="utf-8")
    return path


def extract_pdf_text(pdf_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftotext", "-layout", str(pdf_path), str(output_path)], check=True)


def audio_parts(audio_dir: Path) -> list[Path]:
    part_files = sorted(audio_dir.glob("Part *.mp3"), key=audio_sort_key)
    if part_files:
        return part_files
    numbered_files = [path for path in audio_dir.glob("*.mp3") if re.match(r"\d{3}\b", path.name)]
    if numbered_files:
        return sorted(numbered_files, key=audio_sort_key)
    return sorted(audio_dir.glob("*.mp3"), key=audio_sort_key)


def audio_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"(?:Part\s+)?(\d+)", path.stem, flags=re.IGNORECASE)
    if match:
        return int(match.group(1)), path.name
    return 10_000, path.name


def ffprobe_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return float(result.stdout.strip())


def prepare_inputs(pdf_path: Path, audio_dir: Path, output_dir: Path, book_config: BookConfig) -> list[Chapter]:
    raw_text_path = output_dir / "book.txt"
    extract_pdf_text(pdf_path, raw_text_path)
    chapters = extract_chapters(
        raw_text_path.read_text(encoding="utf-8"),
        chapter_titles=book_config.chapter_titles,
        chapter_count=book_config.chapter_count,
    )
    validate_chapters(chapters, book_config.chapter_count)
    parts = audio_parts(audio_dir)
    if not parts:
        raise RuntimeError(f"No audio parts found in {audio_dir}")
    write_chapter_text_files(chapters, output_dir / "text")
    write_full_text_file(chapters, output_dir / "text" / "book.txt")
    return chapters


def validate_chapters(chapters: Sequence[Chapter], chapter_count: int = len(CHAPTER_TITLES)) -> None:
    numbers = [chapter.number for chapter in chapters]
    expected = list(range(1, chapter_count + 1))
    if numbers != expected:
        raise RuntimeError(f"Expected chapters 1-{chapter_count} in order, found {numbers}")
    small = [chapter.number for chapter in chapters if len(normalize_paragraphs(chapter.body)) < 5]
    if small:
        raise RuntimeError(f"Suspiciously small chapter extraction: {small}")


def run_aeneas(audio_path: Path, text_path: Path, output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    config = "task_language=eng|is_text_type=plain|os_task_file_format=json"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "UTF-8"
    subprocess.run(
        [
            "conda",
            "run",
            "-n",
            "aeneas39",
            "python",
            "-m",
            "aeneas.tools.execute_task",
            str(audio_path),
            str(text_path),
            config,
            str(output_path),
        ],
        check=True,
        env=env,
    )


def concatenate_audio_parts(parts: Sequence[Path], output_path: Path, force: bool = False) -> Path:
    if output_path.exists() and not force:
        return output_path
    if not parts:
        raise RuntimeError("No audio parts to concatenate")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = output_path.parent / "book_parts.txt"
    concat_list.write_text(
        "\n".join(f"file '{ffmpeg_concat_path(path)}'" for path in parts) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(output_path),
        ],
        check=True,
    )
    return output_path


def ffmpeg_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def align_all(
    audio_dir: Path,
    output_dir: Path,
    book_config: BookConfig,
    metadata: dict | None = None,
    force: bool = False,
) -> None:
    align_dir = output_dir / "alignments"
    text_dir = output_dir / "text"
    raw_parts = audio_parts(audio_dir)
    if metadata or len(raw_parts) >= book_config.chapter_count:
        parts = chapter_audio_parts(audio_dir, output_dir, book_config, metadata, force=force)
        if len(parts) != book_config.chapter_count:
            raise RuntimeError(f"Expected {book_config.chapter_count} chapter audio parts, found {len(parts)}")
        for index, audio_path in enumerate(parts, start=1):
            text_path = text_dir / f"chapter_{index:03d}.txt"
            output_path = align_dir / f"chapter_{index:03d}.json"
            print(f"aligning chapter {index:03d}: {audio_path.name}", flush=True)
            run_aeneas(audio_path, text_path, output_path, force=force)
            validate_alignment_file(output_path, ffprobe_duration(audio_path))
        return

    full_audio = concatenate_audio_parts(raw_parts, output_dir / "audio" / "book.mp3", force=force)
    full_text = text_dir / "book.txt"
    output_path = align_dir / "book.json"
    print(f"aligning whole book: {full_audio.name}", flush=True)
    run_aeneas(full_audio, full_text, output_path, force=force)
    validate_alignment_file(output_path, ffprobe_duration(full_audio))


def chapter_audio_parts(
    audio_dir: Path,
    output_dir: Path,
    book_config: BookConfig,
    metadata: dict | None = None,
    force: bool = False,
) -> list[Path]:
    if metadata:
        return split_audio_by_metadata(audio_dir, output_dir / "audio", metadata, force=force)
    parts = audio_parts(audio_dir)[: book_config.chapter_count]
    if len(parts) != book_config.chapter_count:
        raise RuntimeError(f"Expected {book_config.chapter_count} chapter audio parts, found {len(parts)}")
    return parts


def split_audio_by_metadata(audio_dir: Path, output_audio_dir: Path, metadata: dict, force: bool = False) -> list[Path]:
    parts = audio_parts(audio_dir)
    spans = audio_chapter_spans_from_metadata(metadata)
    output_audio_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for span in spans:
        if span.spine_index >= len(parts):
            raise RuntimeError(f"Metadata references missing audio spine {span.spine_index}")
        duration = span.end - span.start
        if duration <= 0:
            raise RuntimeError(f"Invalid duration for chapter {span.number}: {duration}")
        output_path = output_audio_dir / f"chapter_{span.number:03d}.mp3"
        if force or not output_path.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{span.start:.3f}",
                    "-t",
                    f"{duration:.3f}",
                    "-i",
                    str(parts[span.spine_index]),
                    "-vn",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    str(output_path),
                ],
                check=True,
            )
        outputs.append(output_path)
    return outputs


def load_metadata(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def book_config_from_metadata(metadata: dict) -> BookConfig:
    chapter_titles = {}
    for entry in metadata.get("chapters", []):
        parsed = parse_metadata_chapter_title(str(entry.get("title", "")))
        if parsed is None:
            continue
        number, title = parsed
        chapter_titles[number] = title
    if not chapter_titles:
        raise RuntimeError("No numbered chapters found in metadata")
    return BookConfig(
        title=str(metadata.get("title") or DEFAULT_BOOK_TITLE),
        chapter_titles=dict(sorted(chapter_titles.items())),
        chapter_count=max(chapter_titles),
    )


def parse_metadata_chapter_title(title: str) -> tuple[int, str] | None:
    match = re.match(r"\s*Chapter\s+(\d+)\s*:\s*(.+?)\s*$", title)
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip()


def audio_chapter_spans_from_metadata(metadata: dict) -> list[AudioChapterSpan]:
    spine_durations = [float(item["duration"]) for item in metadata.get("spine", [])]
    raw_entries = metadata.get("chapters", [])
    spans = []
    real_chapters = []
    for entry in raw_entries:
        parsed = parse_metadata_chapter_title(str(entry.get("title", "")))
        if parsed is None:
            continue
        number, title = parsed
        real_chapters.append(
            {
                "number": number,
                "title": title,
                "spine": int(entry["spine"]),
                "offset": float(entry["offset"]),
            }
        )

    for index, chapter in enumerate(real_chapters):
        spine_index = chapter["spine"]
        end = spine_durations[spine_index]
        for later in raw_entries:
            if int(later.get("spine", -1)) != spine_index:
                continue
            later_offset = float(later.get("offset", 0))
            if later_offset > chapter["offset"]:
                end = later_offset
                break
        spans.append(
            AudioChapterSpan(
                number=chapter["number"],
                title=chapter["title"],
                spine_index=spine_index,
                start=chapter["offset"],
                end=end,
            )
        )
    return spans


def validate_alignment_file(path: Path, duration: float) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    last_end = 0.0
    for fragment in data.get("fragments", []):
        begin = float(fragment["begin"])
        end = float(fragment["end"])
        if begin < last_end - 0.001 or end < begin:
            raise RuntimeError(f"Non-monotonic timestamps in {path}")
        last_end = end
    if last_end <= 0:
        raise RuntimeError(f"No usable timestamps in {path}")
    if last_end > duration + 5:
        raise RuntimeError(f"Alignment exceeds audio duration in {path}: {last_end} > {duration}")


def build_reader_manifest(
    chapters: Sequence[Chapter],
    audio_files: Sequence[Path],
    alignment_dir: Path,
    durations: Sequence[float],
    title: str = DEFAULT_BOOK_TITLE,
    outro_audio: Path | None = None,
    outro_duration: float | None = None,
) -> dict:
    manifest = {"title": title, "duration": 0.0, "chapters": []}
    offset = 0.0
    for chapter, audio_path, duration in zip(chapters, audio_files, durations, strict=True):
        alignment_path = alignment_dir / f"chapter_{chapter.number:03d}.json"
        data = json.loads(alignment_path.read_text(encoding="utf-8"))
        paragraphs = []
        for fragment in data.get("fragments", []):
            begin = float(fragment["begin"])
            end = float(fragment["end"])
            text = " ".join(fragment.get("lines", [])).strip()
            paragraphs.append(
                {
                    "id": f"c{chapter.number:03d}_{fragment.get('id', len(paragraphs))}",
                    "text": text,
                    "begin": round(offset + begin, 3),
                    "end": round(offset + end, 3),
                    "localBegin": round(begin, 3),
                    "localEnd": round(end, 3),
                }
            )
        manifest["chapters"].append(
            {
                "kind": "chapter",
                "number": chapter.number,
                "title": chapter.title,
                "audio": audio_path.as_posix(),
                "start": round(offset, 3),
                "end": round(offset + duration, 3),
                "duration": round(duration, 3),
                "paragraphs": paragraphs,
            }
        )
        offset += duration

    if outro_audio is not None and outro_duration is not None:
        manifest["chapters"].append(
            {
                "kind": "outro",
                "number": None,
                "title": "Outro",
                "audio": outro_audio.as_posix(),
                "start": round(offset, 3),
                "end": round(offset + outro_duration, 3),
                "duration": round(outro_duration, 3),
                "paragraphs": [],
            }
        )
        offset += outro_duration

    manifest["duration"] = round(offset, 3)
    return manifest


def apply_reader_timestamp_overrides(manifest: dict) -> None:
    for override in READER_TIMESTAMP_OVERRIDES:
        chapter_number = override["chapter"]
        text_prefix = override["text_prefix"]
        local_begin = float(override["local_begin"])
        for chapter in manifest.get("chapters", []):
            if chapter.get("number") != chapter_number:
                continue
            paragraphs = chapter.get("paragraphs", [])
            for index, paragraph in enumerate(paragraphs):
                if not str(paragraph.get("text", "")).startswith(text_prefix):
                    continue
                delta = local_begin - float(paragraph["localBegin"])
                for shifted in paragraphs[index:]:
                    shifted["localBegin"] = round(float(shifted["localBegin"]) + delta, 3)
                    shifted["localEnd"] = round(float(shifted["localEnd"]) + delta, 3)
                    shifted["begin"] = round(float(chapter["start"]) + float(shifted["localBegin"]), 3)
                    shifted["end"] = round(float(chapter["start"]) + float(shifted["localEnd"]), 3)
                return
        raise RuntimeError(f"Reader timestamp override not found: chapter {chapter_number} {text_prefix!r}")


def apply_alignment_timestamp_overrides(alignment: dict, chapter_number: int) -> None:
    for override in READER_TIMESTAMP_OVERRIDES:
        if override["chapter"] != chapter_number:
            continue
        text_prefix = override["text_prefix"]
        local_begin = float(override["local_begin"])
        fragments = alignment.get("fragments", [])
        for index, fragment in enumerate(fragments):
            text = " ".join(fragment.get("lines", [])).strip()
            if not text.startswith(text_prefix):
                continue
            delta = local_begin - float(fragment["begin"])
            for shifted in fragments[index:]:
                shifted["begin"] = f"{float(shifted['begin']) + delta:.3f}"
                shifted["end"] = f"{float(shifted['end']) + delta:.3f}"
            return
        raise RuntimeError(f"Alignment timestamp override not found: chapter {chapter_number} {text_prefix!r}")


def build_reader_manifest_from_single_alignment(
    chapters: Sequence[Chapter],
    audio_file: Path,
    alignment_path: Path,
    duration: float,
    title: str = DEFAULT_BOOK_TITLE,
) -> dict:
    data = json.loads(alignment_path.read_text(encoding="utf-8"))
    fragments = data.get("fragments", [])
    manifest = {"title": title, "duration": round(duration, 3), "chapters": []}
    cursor = 0
    for chapter in chapters:
        expected_count = len(chapter_fragments(chapter))
        chapter_fragments_data = fragments[cursor : cursor + expected_count]
        if len(chapter_fragments_data) != expected_count:
            raise RuntimeError(
                f"Alignment has too few fragments for chapter {chapter.number}: "
                f"expected {expected_count}, found {len(chapter_fragments_data)}"
            )
        cursor += expected_count
        paragraphs = []
        for fragment in chapter_fragments_data:
            begin = float(fragment["begin"])
            end = float(fragment["end"])
            text = " ".join(fragment.get("lines", [])).strip()
            paragraphs.append(
                {
                    "id": f"c{chapter.number:03d}_{fragment.get('id', len(paragraphs))}",
                    "text": text,
                    "begin": round(begin, 3),
                    "end": round(end, 3),
                    "localBegin": round(begin, 3),
                    "localEnd": round(end, 3),
                }
            )
        start = paragraphs[0]["localBegin"] if paragraphs else 0.0
        manifest["chapters"].append(
            {
                "kind": "chapter",
                "number": chapter.number,
                "title": chapter.title,
                "audio": audio_file.as_posix(),
                "audioStart": start,
                "start": start,
                "end": start,
                "duration": 0.0,
                "paragraphs": paragraphs,
            }
        )

    for index, chapter in enumerate(manifest["chapters"]):
        next_start = (
            manifest["chapters"][index + 1]["start"]
            if index + 1 < len(manifest["chapters"])
            else round(duration, 3)
        )
        chapter["end"] = next_start
        chapter["duration"] = round(next_start - chapter["start"], 3)
    return manifest


def build_reader(output_dir: Path, audio_dir: Path, book_config: BookConfig, metadata: dict | None = None) -> None:
    raw_text = (output_dir / "book.txt").read_text(encoding="utf-8")
    chapters = extract_chapters(
        raw_text,
        chapter_titles=book_config.chapter_titles,
        chapter_count=book_config.chapter_count,
    )
    validate_chapters(chapters, book_config.chapter_count)
    full_alignment = output_dir / "alignments" / "book.json"
    if full_alignment.exists():
        full_audio = output_dir / "audio" / "book.mp3"
        if not full_audio.exists():
            full_audio = concatenate_audio_parts(audio_parts(audio_dir), full_audio)
        manifest = build_reader_manifest_from_single_alignment(
            chapters=chapters,
            audio_file=relative_to_output(full_audio, output_dir),
            alignment_path=full_alignment,
            duration=ffprobe_duration(full_audio),
            title=book_config.title,
        )
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (output_dir / "index.html").write_text(build_reader_html(manifest), encoding="utf-8")
        return
    if metadata:
        parts = sorted((output_dir / "audio").glob("chapter_*.mp3"))[: book_config.chapter_count]
        outro_audio = None
        outro_duration = None
    else:
        raw_parts = audio_parts(audio_dir)
        outro_audio = relative_to_output(raw_parts[book_config.chapter_count], output_dir) if len(raw_parts) > book_config.chapter_count else None
        outro_duration = ffprobe_duration(raw_parts[book_config.chapter_count]) if len(raw_parts) > book_config.chapter_count else None
        parts = raw_parts[: book_config.chapter_count]
    if len(parts) != book_config.chapter_count:
        raise RuntimeError(f"Expected {book_config.chapter_count} chapter audio parts, found {len(parts)}")
    chapter_audio = [relative_to_output(path, output_dir) for path in parts]
    durations = [ffprobe_duration(path) for path in parts]
    manifest = build_reader_manifest(
        chapters=chapters,
        audio_files=chapter_audio,
        alignment_dir=output_dir / "alignments",
        durations=durations,
        title=book_config.title,
        outro_audio=outro_audio,
        outro_duration=outro_duration,
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(build_reader_html(manifest), encoding="utf-8")


def relative_to_output(path: Path, output_dir: Path) -> Path:
    return Path(os.path.relpath(path.resolve(), output_dir.resolve()))


def reset_reader_output(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child_name in ("alignments", "audio", "text"):
        child = output_dir / child_name
        if child.exists():
            shutil.rmtree(child)
        child.mkdir(parents=True, exist_ok=True)
    for filename in ("manifest.json", "index.html", "book.txt"):
        path = output_dir / filename
        if path.exists():
            path.unlink()


def stage_audio_units(parts: Sequence[Path], output_audio_dir: Path, force: bool = False) -> list[Path]:
    output_audio_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for index, source in enumerate(parts, start=1):
        target = output_audio_dir / f"chapter_{index:03d}.mp3"
        if force and target.exists():
            target.unlink()
        if not target.exists():
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        staged.append(target)
    return staged


def write_mp3_unit_text_files(fragment_buckets: Sequence[Sequence[str]], text_dir: Path) -> list[Chapter]:
    text_dir.mkdir(parents=True, exist_ok=True)
    chapters = []
    all_fragments: list[str] = []
    for index, fragments in enumerate(fragment_buckets, start=1):
        title = f"Unit {index:03d}"
        path = text_dir / f"chapter_{index:03d}.txt"
        body = "\n".join(fragments).strip()
        path.write_text(body + "\n", encoding="utf-8")
        chapters.append(Chapter(number=index, title=title, body=body))
        all_fragments.extend(fragments)
    (text_dir / "book.txt").write_text("\n".join(all_fragments) + "\n", encoding="utf-8")
    return chapters


def generate_mp3_unit_reader(source_path: Path, audio_dir: Path, output_dir: Path, force: bool = False) -> None:
    reset_reader_output(output_dir)
    source_text = extract_reader_source_text(source_path)
    fragments = split_text_into_sentences(source_text.text)
    if not fragments:
        raise RuntimeError(f"No sentence fragments found in {source_path}")
    raw_parts = audio_parts(audio_dir)
    if not raw_parts:
        raise RuntimeError(f"No MP3 files found in {audio_dir}")
    durations = [ffprobe_duration(path) for path in raw_parts]
    fragment_buckets = split_fragments_by_durations(fragments, durations)
    chapters = write_mp3_unit_text_files(fragment_buckets, output_dir / "text")
    (output_dir / "book.txt").write_text("\n".join(fragments) + "\n", encoding="utf-8")
    staged_audio = stage_audio_units(raw_parts, output_dir / "audio", force=force)
    align_dir = output_dir / "alignments"
    for chapter, audio_path in zip(chapters, staged_audio, strict=True):
        text_path = output_dir / "text" / f"chapter_{chapter.number:03d}.txt"
        alignment_path = align_dir / f"chapter_{chapter.number:03d}.json"
        print(f"aligning unit {chapter.number:03d}: {audio_path.name}", flush=True)
        run_aeneas(audio_path, text_path, alignment_path, force=force)
        validate_alignment_file(alignment_path, durations[chapter.number - 1])
    manifest = build_reader_manifest(
        chapters=chapters,
        audio_files=[relative_to_output(path, output_dir) for path in staged_audio],
        alignment_dir=align_dir,
        durations=durations,
        title=source_text.title,
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(build_reader_html(manifest), encoding="utf-8")


def extract_tainter_chapters(text: str) -> list[Chapter]:
    chapters: list[Chapter] = []
    starts: list[tuple[TainterChapterSpec, re.Match[str]]] = []
    search_from = 0
    for spec in TAINTER_CHAPTER_SPECS:
        pattern = re.compile(
            rf"(?:^|\n)\s*{spec.number}\s*\n+\s*{space_flexible_pattern(spec.marker_title)}",
            flags=re.IGNORECASE,
        )
        match = pattern.search(text, search_from)
        if match is None:
            raise RuntimeError(f"Could not find Tainter chapter {spec.number}: {spec.marker_title}")
        starts.append((spec, match))
        search_from = match.end()

    for index, (spec, match) in enumerate(starts):
        end = starts[index + 1][1].start() if index + 1 < len(starts) else tainter_back_matter_start(text, match.end())
        body = text[match.end() : end].strip()
        chapters.append(Chapter(number=spec.number, title=spec.display_title, body=body))
    return chapters


def space_flexible_pattern(value: str) -> str:
    return r"\s+".join(re.escape(part) for part in value.split())


def tainter_back_matter_start(text: str, start: int) -> int:
    match = re.search(r"\n\s*(?:REFERENCES|INDEX)\s*(?:\n|$)", text[start:])
    return start + match.start() if match else len(text)


def chapter_sentence_fragments(chapter: Chapter) -> list[str]:
    body_fragments = split_text_into_sentences(chapter.body)
    if chapter.number == 5:
        body_fragments = normalize_tainter_chapter_5_fragments(body_fragments)
    return [
        f"Chapter {chapter.number}. {chapter.title}.",
        *body_fragments,
    ]


def normalize_tainter_chapter_5_fragments(fragments: Sequence[str]) -> list[str]:
    cleaned = remove_tainter_chapter_5_figure_blocks(fragments)
    cleaned = repair_tainter_dangling_figure_fragments(cleaned)
    return merge_split_citation_fragments(cleaned)


def remove_tainter_chapter_5_figure_blocks(fragments: Sequence[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(fragments):
        if fragments[index].strip() != "Fig.":
            result.append(fragments[index])
            index += 1
            continue
        index += 1
        while index < len(fragments) and is_tainter_chapter_5_figure_caption_fragment(fragments[index]):
            index += 1
    return result


def repair_tainter_dangling_figure_fragments(fragments: Sequence[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(fragments):
        current = fragments[index].strip()
        repaired = remove_dangling_tainter_figure_reference(current)
        if repaired is not None:
            index += 1
            if index < len(fragments):
                continuation = prose_after_tainter_figure_number(fragments[index].strip())
                if continuation is not None:
                    index += 1
                    if continuation:
                        result.append(ensure_sentence_terminal(f"{repaired} {continuation}".strip()))
                    else:
                        result.append(ensure_sentence_terminal(repaired))
                    continue
            if repaired:
                result.append(ensure_sentence_terminal(repaired))
            continue
        result.append(current)
        index += 1
    return result


def remove_dangling_tainter_figure_reference(fragment: str) -> str | None:
    text = fragment.strip()
    patterns = (
        r"\s*\((?:see\s+)?(?:Table\s+\d+\s+and\s+)?Figs?\.$",
        r"\s+in\s+Figs?\.$",
    )
    for pattern in patterns:
        if re.search(pattern, text):
            return re.sub(pattern, "", text).strip()
    return None


def prose_after_tainter_figure_number(fragment: str) -> str | None:
    text = fragment.strip()
    match = re.match(r"^\d+\)\s*(.*)$", text)
    if match:
        return match.group(1).strip()
    if re.match(r"^\d+(?:\s+and\s+\d+)?(?:,\s+which\b.*)?\.?$", text):
        return ""
    return None


def ensure_sentence_terminal(fragment: str) -> str:
    text = fragment.strip()
    if not text:
        return text
    if text.endswith((".", "?", "!")):
        return text
    return text + "."


def merge_split_citation_fragments(fragments: Sequence[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(fragments):
        current = fragments[index].strip()
        if result and is_orphan_citation_fragment(current) and not result[-1].endswith((".", "?", "!")):
            result[-1] = f"{result[-1]} {current}"
            index += 1
            continue
        if re.search(r"\b[A-Z][A-Za-z' -]+ \d{4}:$", current):
            pieces = [current]
            index += 1
            while index < len(fragments):
                next_fragment = fragments[index].strip()
                if not is_citation_continuation_fragment(next_fragment):
                    break
                pieces.append(next_fragment)
                if next_fragment.endswith((".", "?", "!")):
                    break
                index += 1
            if len(pieces) == 1:
                stripped = strip_dangling_citation_reference(current)
                if stripped:
                    result.append(stripped)
            else:
                result.append(" ".join(piece for piece in pieces if piece))
                index += 1
            continue
        result.append(current)
        index += 1
    return result


def is_citation_continuation_fragment(fragment: str) -> bool:
    text = fragment.strip()
    if re.match(r"^\d+[\),;:-]", text):
        return True
    if re.match(r"^(?:and\s+)?[A-Z](?:\.\s*)?\s*[A-Z][A-Za-z' -]+ \d{4}:", text):
        return True
    if re.match(r"^[A-Z][A-Za-z' -]+ \d{4}:", text):
        return True
    return False


def strip_dangling_citation_reference(fragment: str) -> str:
    text = fragment.strip()
    stripped = re.sub(r"\s*\([^()]*\b[A-Z][A-Za-z' -]+ \d{4}:$", "", text).strip()
    return ensure_sentence_terminal(stripped) if stripped else ""


def is_orphan_citation_fragment(fragment: str) -> bool:
    text = fragment.strip()
    if not re.match(r"^(?:[A-Z](?:\.\s*)?\s*)?[A-Z][A-Za-z' -]+ \d{4}:", text):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    citation_words = {
        "and",
        "cf",
        "et",
        "al",
        "n",
        "d",
        "p",
        "pp",
        "Vol",
    }
    authorish = [word for word in words if word not in citation_words]
    return len(authorish) <= 6 or bool(re.search(r"\d{4}:", text))


def is_tainter_chapter_5_figure_caption_fragment(fragment: str) -> bool:
    text = fragment.strip()
    caption_prefixes = (
        "Debasement of the denarius",
        "Silver percentages plotted",
        "For A. D. 69 and 193",
        "The Mayan area, showing",
        "Reproduced by permission",
        "One katun is approximately",
        "Construction of dated monuments",
        "Occupation of Classic Maya centers",
        "Southern Lowland Maya radiocarbon dates",
        "and Raymond Sidrys from",
        "Nature, Vol.",
        "277, p.",
        "Copyright",
        "San Juan Basin and surrounding terrain",
        "The Chacoan regional system",
        "Courtesy of the U.S. National Park Service",
    )
    return text.startswith(caption_prefixes)


def audit_tainter_chapter_5_fragments(fragments: Sequence[str], durations: Sequence[float] | None = None) -> dict:
    fragment_list = [fragment.strip() for fragment in fragments if fragment.strip()]
    suspicious = []
    for index, fragment in enumerate(fragment_list, start=1):
        reason = suspicious_tainter_chapter_5_fragment_reason(fragment)
        if reason:
            suspicious.append({"index": index, "reason": reason, "fragment": fragment})

    report = {
        "fragment_count": len(fragment_list),
        "word_count": sum(len(fragment.split()) for fragment in fragment_list),
        "suspicious_fragments": suspicious,
        "segments": [],
    }
    if durations:
        boundaries = chapter_5_segment_boundaries(fragment_list, durations)
        for segment_index, ((start, end), duration) in enumerate(zip(boundaries, durations, strict=True), start=1):
            segment = fragment_list[start:end]
            word_count = sum(len(fragment.split()) for fragment in segment)
            minutes = duration / 60 if duration else 0
            report["segments"].append(
                {
                    "index": segment_index,
                    "start": start,
                    "end": end,
                    "fragment_count": len(segment),
                    "word_count": word_count,
                    "duration": round(float(duration), 3),
                    "words_per_minute": round(word_count / minutes, 1) if minutes > 0 else None,
                }
            )
    return report


def suspicious_tainter_chapter_5_fragment_reason(fragment: str) -> str | None:
    text = fragment.strip()
    if remove_dangling_tainter_figure_reference(text) is not None:
        return "dangling figure/table reference"
    if text == "Fig." or is_tainter_chapter_5_figure_caption_fragment(text):
        return "standalone figure/caption fragment"
    if is_orphan_citation_fragment(text):
        return "citation-only orphan fragment"
    if re.search(r"\bet al\.$", text):
        return "dangling et al. fragment"
    if text.count("(") > text.count(")"):
        return "unmatched opening parenthesis"
    if text.count(")") > text.count("(") and len(re.findall(r"[A-Za-z]+", text)) <= 8:
        return "short unmatched closing parenthesis fragment"
    return None


def write_sentence_chapter_text_files(chapters: Sequence[Chapter], text_dir: Path) -> None:
    text_dir.mkdir(parents=True, exist_ok=True)
    all_fragments: list[str] = []
    for chapter in chapters:
        fragments = chapter_sentence_fragments(chapter)
        (text_dir / f"chapter_{chapter.number:03d}.txt").write_text("\n".join(fragments) + "\n", encoding="utf-8")
        all_fragments.extend(fragments)
    (text_dir / "book.txt").write_text("\n".join(all_fragments) + "\n", encoding="utf-8")


def build_tainter_audio_chapters(audio_dir: Path, output_audio_dir: Path, force: bool = False) -> tuple[list[Path], list[float]]:
    parts = audio_parts(audio_dir)
    parts_by_number = {audio_sort_key(path)[0]: path for path in parts}
    output_audio_dir.mkdir(parents=True, exist_ok=True)
    chapter_audio: list[Path] = []
    durations: list[float] = []
    for spec in TAINTER_CHAPTER_SPECS:
        sources = []
        for part_number in spec.audio_parts:
            source = parts_by_number.get(part_number)
            if source is None:
                raise RuntimeError(f"Missing audio part {part_number:03d} in {audio_dir}")
            sources.append(source)
        output_path = output_audio_dir / f"chapter_{spec.number:03d}.mp3"
        if len(sources) == 1:
            if force and output_path.exists():
                output_path.unlink()
            if not output_path.exists():
                try:
                    os.link(sources[0], output_path)
                except OSError:
                    shutil.copy2(sources[0], output_path)
        else:
            concatenate_audio_parts(sources, output_path, force=force)
        chapter_audio.append(output_path)
        durations.append(sum(ffprobe_duration(source) for source in sources))
    return chapter_audio, durations


def chapter_5_segment_boundaries(fragments: Sequence[str], durations: Sequence[float]) -> list[tuple[int, int]]:
    if len(durations) != 3:
        raise RuntimeError(f"Chapter 5 segmentation expects three audio durations, found {len(durations)}")
    if len(fragments) < 3:
        raise RuntimeError("Chapter 5 segmentation needs at least three text fragments")
    anchor_index = next(
        (
            index
            for index, fragment in enumerate(fragments)
            if str(fragment).startswith(READER_TIMESTAMP_OVERRIDES[0]["text_prefix"])
        ),
        None,
    )
    if anchor_index is None or anchor_index < 2:
        buckets = split_fragments_by_durations(fragments, durations)
        cursor = 0
        boundaries = []
        for bucket in buckets:
            boundaries.append((cursor, cursor + len(bucket)))
            cursor += len(bucket)
        return boundaries

    # The known anchor is about 61s before the 008 -> 009 boundary, so start
    # part 3 at the anchor and split the preceding text across 007 and 008.
    first_two_duration = durations[0] + durations[1]
    first_boundary = round(anchor_index * (durations[0] / first_two_duration)) if first_two_duration > 0 else 1
    first_boundary = max(1, min(first_boundary, anchor_index - 1))
    return [(0, first_boundary), (first_boundary, anchor_index), (anchor_index, len(fragments))]


def merge_segment_alignments(
    segment_alignments: Sequence[dict],
    durations: Sequence[float],
    chapter_number: int | None = None,
) -> dict:
    if len(segment_alignments) != len(durations):
        raise RuntimeError("Segment alignment and duration counts must match")
    fragments = []
    offset = 0.0
    for segment_index, (alignment, duration) in enumerate(zip(segment_alignments, durations, strict=True), start=1):
        for fragment in alignment.get("fragments", []):
            merged = dict(fragment)
            merged["id"] = f"f{len(fragments) + 1:06d}"
            merged["begin"] = f"{float(fragment['begin']) + offset:.3f}"
            merged["end"] = f"{float(fragment['end']) + offset:.3f}"
            fragments.append(merged)
        offset += float(duration)
    merged_alignment = {"fragments": fragments}
    if chapter_number is not None:
        apply_alignment_timestamp_overrides(merged_alignment, chapter_number)
    return merged_alignment


def run_whisper_cpp(
    audio_path: Path,
    transcript_path: Path,
    whisper_command: str,
    model_path: Path,
    force: bool = False,
) -> None:
    if transcript_path.exists() and not force:
        return
    if not model_path.exists():
        raise RuntimeError(
            f"Whisper model not found: {model_path}. Provide --whisper-model pointing to a local whisper.cpp ggml model."
        )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    output_prefix = transcript_path.with_suffix("")
    subprocess.run(
        [
            whisper_command,
            "-m",
            str(model_path),
            "-f",
            str(audio_path),
            "-l",
            "en",
            "-oj",
            "-of",
            str(output_prefix),
        ],
        check=True,
    )


def load_whisper_transcript(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_segments = data.get("segments")
    if raw_segments is None:
        raw_segments = data.get("transcription")
    if raw_segments is None and isinstance(data, list):
        raw_segments = data
    if raw_segments is None:
        raise RuntimeError(f"No Whisper segments found in {path}")

    segments = []
    for item in raw_segments:
        if "start" in item and "end" in item:
            start = float(item["start"])
            end = float(item["end"])
        elif "timestamps" in item:
            timestamps = item["timestamps"]
            start = parse_whisper_timestamp(str(timestamps["from"]))
            end = parse_whisper_timestamp(str(timestamps["to"]))
        elif "offsets" in item:
            offsets = item["offsets"]
            start = float(offsets["from"]) / 1000
            end = float(offsets["to"]) / 1000
        else:
            raise RuntimeError(f"Whisper segment lacks timestamps in {path}: {item!r}")
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        if text and end >= start:
            segments.append({"start": start, "end": end, "text": text})
    if not segments:
        raise RuntimeError(f"No usable Whisper segments found in {path}")
    return segments


def parse_whisper_timestamp(value: str) -> float:
    cleaned = value.replace(",", ".")
    parts = cleaned.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    return float(cleaned)


def normalize_alignment_text(text: str) -> str:
    value = clean_text(text).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def align_fragments_to_whisper_segments(fragments: Sequence[str], whisper_segments: Sequence[dict]) -> dict:
    if not fragments:
        raise RuntimeError("No text fragments supplied for Whisper alignment")
    if not whisper_segments:
        raise RuntimeError("No Whisper segments supplied for alignment")

    normalized_segments = [normalize_alignment_text(str(segment.get("text", ""))) for segment in whisper_segments]
    assignments: list[int | None] = []
    scores: list[float] = []
    cursor = 0
    for fragment in fragments:
        normalized_fragment = normalize_alignment_text(fragment)
        best_index = None
        best_score = 0.0
        for index in range(cursor, len(whisper_segments)):
            score = alignment_text_similarity(normalized_fragment, normalized_segments[index])
            if score > best_score:
                best_index = index
                best_score = score
            if score >= 0.92:
                break
        if best_index is not None and best_score >= 0.58:
            assignments.append(best_index)
            scores.append(best_score)
            cursor = best_index
        else:
            assignments.append(None)
            scores.append(best_score)

    times: list[tuple[float, float] | None] = [None] * len(fragments)
    for segment_index, segment in enumerate(whisper_segments):
        indexes = [index for index, assigned in enumerate(assignments) if assigned == segment_index]
        if not indexes:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        durations = proportional_durations([fragments[index] for index in indexes], max(0.001, end - start))
        cursor_time = start
        for fragment_index, duration in zip(indexes, durations, strict=True):
            fragment_end = cursor_time + duration
            times[fragment_index] = (cursor_time, fragment_end)
            cursor_time = fragment_end
        last_index = indexes[-1]
        begin, _ = times[last_index] or (start, end)
        times[last_index] = (begin, end)

    interpolate_unmatched_fragment_times(times, whisper_segments)

    output_fragments = []
    unmatched = []
    low_confidence = []
    for index, (fragment, timing, assigned, score) in enumerate(zip(fragments, times, assignments, scores, strict=True), start=1):
        if timing is None:
            raise RuntimeError(f"Could not assign timestamp for fragment {index}: {fragment!r}")
        begin, end = timing
        if assigned is None:
            unmatched.append({"index": index, "fragment": fragment})
        elif score < 0.7:
            low_confidence.append({"index": index, "score": round(score, 3), "fragment": fragment})
        output_fragments.append(
            {
                "id": f"f{index:06d}",
                "begin": f"{begin:.3f}",
                "end": f"{end:.3f}",
                "lines": [fragment],
            }
        )
    return {
        "fragments": output_fragments,
        "audit": {
            "unmatched_fragments": unmatched,
            "low_confidence_fragments": low_confidence,
        },
    }


def alignment_text_similarity(fragment: str, segment: str) -> float:
    if not fragment or not segment:
        return 0.0
    if fragment in segment or segment in fragment:
        return 1.0
    return difflib.SequenceMatcher(None, fragment, segment).ratio()


def proportional_durations(fragments: Sequence[str], total_duration: float) -> list[float]:
    weights = [max(1, len(normalize_alignment_text(fragment))) for fragment in fragments]
    total_weight = sum(weights)
    return [total_duration * (weight / total_weight) for weight in weights]


def interpolate_unmatched_fragment_times(times: list[tuple[float, float] | None], whisper_segments: Sequence[dict]) -> None:
    index = 0
    while index < len(times):
        if times[index] is not None:
            index += 1
            continue
        start_index = index
        while index < len(times) and times[index] is None:
            index += 1
        end_index = index
        previous_end = times[start_index - 1][1] if start_index > 0 and times[start_index - 1] is not None else float(whisper_segments[0]["start"])
        next_begin = times[end_index][0] if end_index < len(times) and times[end_index] is not None else float(whisper_segments[-1]["end"])
        count = end_index - start_index
        span = max(0.001, next_begin - previous_end)
        step = span / count
        cursor = previous_end
        for fill_index in range(start_index, end_index):
            times[fill_index] = (cursor, cursor + step)
            cursor += step


def chapter_5_anchor_times(duration: float, interval_seconds: float = 600.0, window_seconds: float = 30.0) -> list[dict]:
    if duration <= 0:
        raise RuntimeError("Chapter 5 duration must be positive")
    if interval_seconds <= 0 or window_seconds <= 0:
        raise RuntimeError("Anchor interval and window must be positive")
    anchors = []
    start = 0.0
    while start < duration:
        anchors.append({"start": round(start, 3), "duration": round(min(window_seconds, duration - start), 3)})
        start += interval_seconds
    return anchors


def cut_audio_window(audio_path: Path, start: float, duration: float, output_path: Path, force: bool = False) -> Path:
    if output_path.exists() and not force:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(audio_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(output_path),
        ],
        check=True,
    )
    return output_path


def run_openai_transcription(
    audio_path: Path,
    output_path: Path,
    model: str,
    api_key: str,
    force: bool = False,
) -> None:
    if output_path.exists() and not force:
        return
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for --chapter5-aligner openai-anchors")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    boundary = "----audio-text-sync-boundary"
    body = build_openai_transcription_multipart_body(audio_path, model, boundary)
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        output_path.write_bytes(response.read())


def build_openai_transcription_multipart_body(audio_path: Path, model: str, boundary: str) -> bytes:
    fields = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
        ("timestamp_granularities[]", "word"),
    ]
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(f"{value}\r\n".encode("utf-8"))
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        "Content-Type: audio/mpeg\r\n\r\n".encode("utf-8")
    )
    parts.append(audio_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts)


def load_openai_transcript(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = [
        {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": re.sub(r"\s+", " ", str(segment.get("text", ""))).strip(),
        }
        for segment in data.get("segments", [])
        if str(segment.get("text", "")).strip()
    ]
    words = [
        {
            "start": float(word["start"]),
            "end": float(word["end"]),
            "text": re.sub(r"\s+", " ", str(word.get("word", word.get("text", "")))).strip(),
        }
        for word in data.get("words", [])
        if str(word.get("word", word.get("text", ""))).strip()
    ]
    text = re.sub(r"\s+", " ", str(data.get("text") or " ".join(segment["text"] for segment in segments))).strip()
    return {"text": text, "segments": segments, "words": words}


def match_anchor_transcript_to_fragments(
    transcript: dict,
    fragments: Sequence[str],
    clip_start: float,
    min_score: float = 0.58,
) -> dict | None:
    transcript_text = normalize_alignment_text(str(transcript.get("text", "")))
    if not transcript_text:
        return None
    best_index = None
    best_score = 0.0
    for index, fragment in enumerate(fragments):
        fragment_text = normalize_alignment_text(fragment)
        direct_score = alignment_text_similarity(transcript_text, fragment_text)
        joined = fragment_text
        for lookahead in range(index + 1, min(len(fragments), index + 4)):
            joined = f"{joined} {normalize_alignment_text(fragments[lookahead])}".strip()
        score = max(direct_score, alignment_text_similarity(transcript_text, joined) * 0.92)
        if score > best_score:
            best_index = index
            best_score = score
    if best_index is None or best_score < min_score:
        return None
    local_start = 0.0
    if transcript.get("words"):
        local_start = float(transcript["words"][0]["start"])
    elif transcript.get("segments"):
        local_start = float(transcript["segments"][0]["start"])
    return {
        "fragment_index": best_index,
        "chapter_time": round(float(clip_start) + local_start, 3),
        "score": round(best_score, 3),
        "text": str(transcript.get("text", "")),
    }


def build_chapter_5_openai_anchors(
    chapter_audio_path: Path,
    fragments: Sequence[str],
    duration: float,
    anchor_dir: Path,
    model: str,
    api_key: str,
    interval_seconds: float = 600.0,
    window_seconds: float = 30.0,
    force: bool = False,
) -> list[dict]:
    anchor_dir.mkdir(parents=True, exist_ok=True)
    anchors = []
    skipped = []
    for window in chapter_5_anchor_times(duration, interval_seconds=interval_seconds, window_seconds=window_seconds):
        start = float(window["start"])
        label = f"{int(round(start)):06d}"
        clip_path = anchor_dir / f"clip_{label}.mp3"
        transcript_path = anchor_dir / f"transcript_{label}.json"
        cut_audio_window(chapter_audio_path, start, float(window["duration"]), clip_path, force=force)
        run_openai_transcription(clip_path, transcript_path, model, api_key, force=force)
        transcript = load_openai_transcript(transcript_path)
        anchor = match_anchor_transcript_to_fragments(transcript, fragments, clip_start=start)
        if anchor is None:
            skipped.append({"clip_start": start, "transcript": transcript.get("text", "")})
            continue
        anchor["clip_start"] = start
        anchors.append(anchor)
    payload = {"anchors": anchors, "skipped": skipped}
    (anchor_dir / "anchors.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return anchors


def chapter_5_boundaries_from_anchors(fragments: Sequence[str], anchors: Sequence[dict], duration: float) -> list[dict]:
    points = [{"fragment_index": 0, "time": 0.0, "source": "start"}]
    for anchor in anchors:
        points.append(
            {
                "fragment_index": int(anchor["fragment_index"]),
                "time": float(anchor["chapter_time"]),
                "source": "openai",
            }
        )
    manual_prefix = READER_TIMESTAMP_OVERRIDES[0]["text_prefix"]
    manual_index = next((index for index, fragment in enumerate(fragments) if fragment.startswith(manual_prefix)), None)
    if manual_index is not None:
        points.append({"fragment_index": manual_index, "time": float(READER_TIMESTAMP_OVERRIDES[0]["local_begin"]), "source": "manual"})
    points.append({"fragment_index": len(fragments), "time": float(duration), "source": "end"})
    points.sort(key=lambda item: (item["time"], item["fragment_index"]))

    monotonic = []
    last_fragment = -1
    last_time = -1.0
    for point in points:
        fragment_index = int(point["fragment_index"])
        time = float(point["time"])
        if fragment_index <= last_fragment or time <= last_time:
            continue
        monotonic.append(point)
        last_fragment = fragment_index
        last_time = time

    boundaries = []
    for start, end in zip(monotonic, monotonic[1:]):
        if end["fragment_index"] <= start["fragment_index"] or end["time"] <= start["time"]:
            continue
        boundaries.append(
            {
                "fragment_start": start["fragment_index"],
                "fragment_end": end["fragment_index"],
                "time_start": round(start["time"], 3),
                "time_end": round(end["time"], 3),
                "source_start": start["source"],
                "source_end": end["source"],
            }
        )
    if not boundaries:
        raise RuntimeError("No usable chapter 5 anchor boundaries generated")
    return boundaries


def merge_interval_alignments(intervals: Sequence[dict], chapter_number: int | None = None) -> dict:
    fragments = []
    for interval in intervals:
        time_start = float(interval["time_start"])
        for fragment in interval["alignment"].get("fragments", []):
            merged = dict(fragment)
            merged["id"] = f"f{len(fragments) + 1:06d}"
            merged["begin"] = f"{time_start + float(fragment['begin']):.3f}"
            merged["end"] = f"{time_start + float(fragment['end']):.3f}"
            fragments.append(merged)
    merged_alignment = {"fragments": fragments}
    if chapter_number is not None:
        apply_alignment_timestamp_overrides(merged_alignment, chapter_number)
    return merged_alignment


def align_tainter_chapter_5_by_openai_anchors(
    chapter: Chapter,
    audio_dir: Path,
    text_dir: Path,
    align_dir: Path,
    force: bool = False,
    openai_transcribe_model: str = "whisper-1",
    anchor_interval_seconds: float = 600.0,
    anchor_window_seconds: float = 30.0,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for --chapter5-aligner openai-anchors")
    chapter_audio_path = align_dir.parent / "audio" / "chapter_005.mp3"
    if not chapter_audio_path.exists():
        raise RuntimeError(f"Chapter 5 audio not found: {chapter_audio_path}")
    duration = ffprobe_duration(chapter_audio_path)
    fragments = chapter_sentence_fragments(chapter)
    text_dir.mkdir(parents=True, exist_ok=True)
    align_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "chapter_005.txt").write_text("\n".join(fragments) + "\n", encoding="utf-8")
    anchor_dir = align_dir.parent / "anchors" / "chapter_005"
    anchors = build_chapter_5_openai_anchors(
        chapter_audio_path,
        fragments,
        duration,
        anchor_dir,
        openai_transcribe_model,
        api_key,
        interval_seconds=anchor_interval_seconds,
        window_seconds=anchor_window_seconds,
        force=force,
    )
    boundaries = chapter_5_boundaries_from_anchors(fragments, anchors, duration)
    interval_alignments = []
    interval_dir = align_dir / "chapter_005_intervals"
    interval_text_dir = text_dir / "chapter_005_intervals"
    for index, boundary in enumerate(boundaries, start=1):
        interval_fragments = fragments[boundary["fragment_start"] : boundary["fragment_end"]]
        audio_path = interval_dir / f"chapter_005_interval_{index:03d}.mp3"
        text_path = interval_text_dir / f"chapter_005_interval_{index:03d}.txt"
        alignment_path = interval_dir / f"chapter_005_interval_{index:03d}.json"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text("\n".join(interval_fragments) + "\n", encoding="utf-8")
        cut_audio_window(
            chapter_audio_path,
            float(boundary["time_start"]),
            float(boundary["time_end"]) - float(boundary["time_start"]),
            audio_path,
            force=force,
        )
        run_aeneas(audio_path, text_path, alignment_path, force=force)
        interval_alignments.append({**boundary, "alignment": json.loads(alignment_path.read_text(encoding="utf-8"))})
    merged = merge_interval_alignments(interval_alignments, chapter_number=5)
    (align_dir / "chapter_005.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")


def align_tainter_chapter_5_with_whisper(
    chapter: Chapter,
    audio_dir: Path,
    text_dir: Path,
    align_dir: Path,
    force: bool = False,
    whisper_command: str = "whisper-cli",
    whisper_model: Path = Path("models/ggml-medium.en.bin"),
) -> None:
    parts = audio_parts(audio_dir)
    parts_by_number = {audio_sort_key(path)[0]: path for path in parts}
    source_audio = []
    for part_number in (7, 8, 9):
        source = parts_by_number.get(part_number)
        if source is None:
            raise RuntimeError(f"Missing audio part {part_number:03d} in {audio_dir}")
        source_audio.append(source)

    durations = [ffprobe_duration(path) for path in source_audio]
    fragments = chapter_sentence_fragments(chapter)
    boundaries = chapter_5_segment_boundaries(fragments, durations)
    transcript_dir = align_dir.parent / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    align_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    segment_alignments = []
    for index, ((start, end), audio_path) in enumerate(zip(boundaries, source_audio, strict=True), start=1):
        segment_fragments = fragments[start:end]
        if not segment_fragments:
            raise RuntimeError(f"Chapter 5 segment {index} has no text fragments")
        text_path = text_dir / f"chapter_005_part_{index:03d}.txt"
        transcript_path = transcript_dir / f"chapter_005_part_{index:03d}.json"
        text_path.write_text("\n".join(segment_fragments) + "\n", encoding="utf-8")
        print(f"transcribing chapter 005 segment {index:03d}: {audio_path.name}", flush=True)
        run_whisper_cpp(audio_path, transcript_path, whisper_command, whisper_model, force=force)
        whisper_segments = load_whisper_transcript(transcript_path)
        alignment = align_fragments_to_whisper_segments(segment_fragments, whisper_segments)
        (align_dir / f"chapter_005_part_{index:03d}.json").write_text(json.dumps(alignment, indent=2), encoding="utf-8")
        segment_alignments.append(alignment)

    merged = merge_segment_alignments(segment_alignments, durations, chapter_number=5)
    (align_dir / "chapter_005.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")


def align_tainter_chapter_5_by_audio_parts(
    chapter: Chapter,
    audio_dir: Path,
    text_dir: Path,
    align_dir: Path,
    force: bool = False,
) -> None:
    parts = audio_parts(audio_dir)
    parts_by_number = {audio_sort_key(path)[0]: path for path in parts}
    source_audio = []
    for part_number in (7, 8, 9):
        source = parts_by_number.get(part_number)
        if source is None:
            raise RuntimeError(f"Missing audio part {part_number:03d} in {audio_dir}")
        source_audio.append(source)

    durations = [ffprobe_duration(path) for path in source_audio]
    fragments = chapter_sentence_fragments(chapter)
    boundaries = chapter_5_segment_boundaries(fragments, durations)
    segment_alignments = []
    align_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    for index, ((start, end), audio_path, duration) in enumerate(zip(boundaries, source_audio, durations, strict=True), start=1):
        segment_fragments = fragments[start:end]
        if not segment_fragments:
            raise RuntimeError(f"Chapter 5 segment {index} has no text fragments")
        text_path = text_dir / f"chapter_005_part_{index:03d}.txt"
        alignment_path = align_dir / f"chapter_005_part_{index:03d}.json"
        text_path.write_text("\n".join(segment_fragments) + "\n", encoding="utf-8")
        print(f"aligning chapter 005 segment {index:03d}: {audio_path.name}", flush=True)
        run_aeneas(audio_path, text_path, alignment_path, force=force)
        validate_alignment_file(alignment_path, duration)
        segment_alignments.append(json.loads(alignment_path.read_text(encoding="utf-8")))

    merged = merge_segment_alignments(segment_alignments, durations, chapter_number=5)
    (align_dir / "chapter_005.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")


def generate_tainter_chapter_reader(
    pdf_path: Path,
    audio_dir: Path,
    output_dir: Path,
    force: bool = False,
    chapter5_aligner: str = "openai-anchors",
    whisper_command: str = "whisper-cli",
    whisper_model: Path = Path("models/ggml-medium.en.bin"),
    openai_transcribe_model: str = "whisper-1",
    anchor_interval_seconds: float = 600.0,
    anchor_window_seconds: float = 30.0,
) -> None:
    reset_reader_output(output_dir)
    source_text = extract_pdf_reader_text(pdf_path)
    chapters = extract_tainter_chapters(source_text.text)
    write_sentence_chapter_text_files(chapters, output_dir / "text")
    (output_dir / "book.txt").write_text((output_dir / "text" / "book.txt").read_text(encoding="utf-8"), encoding="utf-8")
    chapter_audio, durations = build_tainter_audio_chapters(audio_dir, output_dir / "audio", force=force)
    align_dir = output_dir / "alignments"
    for chapter, audio_path, duration in zip(chapters, chapter_audio, durations, strict=True):
        text_path = output_dir / "text" / f"chapter_{chapter.number:03d}.txt"
        alignment_path = align_dir / f"chapter_{chapter.number:03d}.json"
        if chapter.number == 5:
            if chapter5_aligner == "openai-anchors":
                align_tainter_chapter_5_by_openai_anchors(
                    chapter,
                    audio_dir,
                    output_dir / "text",
                    align_dir,
                    force=force,
                    openai_transcribe_model=openai_transcribe_model,
                    anchor_interval_seconds=anchor_interval_seconds,
                    anchor_window_seconds=anchor_window_seconds,
                )
            elif chapter5_aligner == "whisper":
                align_tainter_chapter_5_with_whisper(
                    chapter,
                    audio_dir,
                    output_dir / "text",
                    align_dir,
                    force=force,
                    whisper_command=whisper_command,
                    whisper_model=whisper_model,
                )
            elif chapter5_aligner == "aeneas":
                align_tainter_chapter_5_by_audio_parts(chapter, audio_dir, output_dir / "text", align_dir, force=force)
            else:
                raise RuntimeError(f"Unsupported chapter 5 aligner: {chapter5_aligner}")
            continue
        print(f"aligning chapter {chapter.number:03d}: {audio_path.name}", flush=True)
        run_aeneas(audio_path, text_path, alignment_path, force=force)
        validate_alignment_file(alignment_path, duration)
    manifest = build_reader_manifest(
        chapters=chapters,
        audio_files=[relative_to_output(path, output_dir) for path in chapter_audio],
        alignment_dir=align_dir,
        durations=durations,
        title=source_text.title,
    )
    apply_reader_timestamp_overrides(manifest)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(build_reader_html(manifest), encoding="utf-8")


def build_reader_html(manifest: dict) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False)
    title = html.escape(manifest["title"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f5f4;
      --surface: #ffffff;
      --line: #d7d3cc;
      --text: #1c1917;
      --muted: #6b6258;
      --active: #fff3c4;
      --active-line: #b45309;
      --button: #1c1917;
      --button-text: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
      font-size: 16px;
      line-height: 1.55;
    }}
    .app {{
      min-height: 100vh;
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--surface);
      height: 100vh;
      position: sticky;
      top: 0;
      overflow: auto;
      padding: 20px 16px;
    }}
    .book-title {{
      font-size: 15px;
      font-weight: 700;
      margin: 0 0 16px;
      line-height: 1.3;
    }}
    .chapter-list {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .chapter-link {{
      width: 100%;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--text);
      cursor: pointer;
      display: grid;
      grid-template-columns: 32px 1fr;
      gap: 8px;
      padding: 8px;
      text-align: left;
      font: inherit;
      line-height: 1.3;
    }}
    .chapter-link:hover {{ background: #f0eee9; }}
    .chapter-link.active {{
      background: #e7e1d8;
      font-weight: 650;
    }}
    .chapter-number {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    main {{
      min-width: 0;
      padding: 28px 32px 112px;
    }}
    .topbar {{
      max-width: 840px;
      margin: 0 auto 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      font-size: 22px;
      line-height: 1.25;
      margin: 0;
      font-weight: 750;
    }}
    .time {{
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    .reader {{
      max-width: 840px;
      margin: 0 auto;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 28px 34px;
    }}
    .chapter-heading {{
      font-size: 20px;
      margin: 0 0 22px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }}
    .paragraph {{
      margin: 0 0 14px;
      padding: 3px 6px;
      border-left: 3px solid transparent;
      border-radius: 4px;
      cursor: pointer;
    }}
    .paragraph:hover {{ background: #f8f6f1; }}
    .paragraph.active {{
      background: var(--active);
      border-left-color: var(--active-line);
    }}
    .outro {{
      color: var(--muted);
      margin: 0;
    }}
    .player {{
      position: fixed;
      left: 280px;
      right: 0;
      bottom: 0;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      display: grid;
      grid-template-columns: auto auto auto minmax(120px, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 12px 20px;
    }}
    button.control {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      cursor: pointer;
      min-height: 36px;
      padding: 0 12px;
      font: inherit;
      font-weight: 650;
    }}
    button.primary {{
      background: var(--button);
      color: var(--button-text);
      border-color: var(--button);
      min-width: 68px;
    }}
    input[type="range"] {{
      width: 100%;
      accent-color: #b45309;
    }}
    @media (max-width: 760px) {{
      .app {{ display: block; }}
      aside {{
        position: static;
        height: auto;
        max-height: 240px;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      main {{ padding: 20px 16px 120px; }}
      .reader {{ padding: 22px 18px; }}
      .topbar {{ align-items: flex-start; flex-direction: column; }}
      .player {{
        left: 0;
        grid-template-columns: auto auto auto;
      }}
      .player input[type="range"] {{
        grid-column: 1 / -1;
      }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <p class="book-title">{title}</p>
      <nav class="chapter-list" id="chapterList"></nav>
    </aside>
    <main>
      <div class="topbar">
        <h1 id="chapterTitle"></h1>
        <div class="time" id="timeLabel">0:00 / 0:00</div>
      </div>
      <article class="reader" id="reader"></article>
    </main>
  </div>
  <div class="player">
    <button class="control" id="prevButton" type="button">Prev</button>
    <button class="control primary" id="playButton" type="button">Play</button>
    <button class="control" id="nextButton" type="button">Next</button>
    <input id="seekBar" type="range" min="0" max="1000" value="0" aria-label="Seek">
    <span class="time" id="chapterTime">0:00</span>
  </div>
  <audio id="audio" preload="metadata"></audio>
  <script>
    const manifest = {manifest_json};
    const PROGRESS_KEY = `aligned-reader-progress:${{manifest.title}}`;
    const audio = document.getElementById('audio');
    const chapterList = document.getElementById('chapterList');
    const reader = document.getElementById('reader');
    const chapterTitle = document.getElementById('chapterTitle');
    const playButton = document.getElementById('playButton');
    const prevButton = document.getElementById('prevButton');
    const nextButton = document.getElementById('nextButton');
    const seekBar = document.getElementById('seekBar');
    const timeLabel = document.getElementById('timeLabel');
    const chapterTime = document.getElementById('chapterTime');
    let currentIndex = 0;
    let currentParagraphId = null;

    function saveProgress(paragraph) {{
      if (!paragraph) return;
      localStorage.setItem(PROGRESS_KEY, JSON.stringify({{
        chapterIndex: currentIndex,
        paragraphId: paragraph.id,
        currentTime: paragraph.localBegin,
      }}));
    }}

    function loadProgress() {{
      try {{
        return JSON.parse(localStorage.getItem(PROGRESS_KEY) || 'null');
      }} catch {{
        return null;
      }}
    }}

    function formatTime(seconds) {{
      seconds = Math.max(0, Math.floor(seconds || 0));
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      return h ? `${{h}}:${{String(m).padStart(2, '0')}}:${{String(s).padStart(2, '0')}}` : `${{m}}:${{String(s).padStart(2, '0')}}`;
    }}

    function renderNav() {{
      chapterList.innerHTML = '';
      manifest.chapters.forEach((chapter, index) => {{
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chapter-link' + (index === currentIndex ? ' active' : '');
        const number = chapter.kind === 'chapter' ? String(chapter.number).padStart(2, '0') : '--';
        button.innerHTML = `<span class="chapter-number">${{number}}</span><span>${{chapter.title}}</span>`;
        button.addEventListener('click', () => loadChapter(index, true));
        chapterList.appendChild(button);
      }});
    }}

    function chapterAudioStart(chapter) {{
      return chapter.audioStart || 0;
    }}

    function loadChapter(index, autoplay = false, seek = true) {{
      currentIndex = Math.max(0, Math.min(index, manifest.chapters.length - 1));
      const chapter = manifest.chapters[currentIndex];
      currentParagraphId = null;
      const nextSource = new URL(chapter.audio, window.location.href).href;
      const sourceChanged = audio.src !== nextSource;
      if (sourceChanged) {{
        audio.src = chapter.audio;
      }}
      chapterTitle.textContent = chapter.kind === 'chapter' ? `Chapter ${{chapter.number}}. ${{chapter.title}}` : chapter.title;
      reader.innerHTML = '';
      if (chapter.paragraphs.length) {{
        chapter.paragraphs.forEach((paragraph) => {{
          const node = document.createElement('p');
          node.className = 'paragraph';
          node.id = paragraph.id;
          node.textContent = paragraph.text;
          node.addEventListener('click', () => {{
            saveProgress(paragraph);
            audio.currentTime = paragraph.localBegin;
            audio.play();
          }});
          reader.appendChild(node);
        }});
      }} else {{
        const node = document.createElement('p');
        node.className = 'outro';
        node.textContent = 'Audio outro';
        reader.appendChild(node);
      }}
      renderNav();
      updateTimes();
      const startPlayback = () => {{
        if (seek) {{
          audio.currentTime = chapterAudioStart(chapter);
        }}
        if (autoplay) {{
          audio.play();
        }}
      }};
      if (sourceChanged && audio.readyState < 1) {{
        audio.addEventListener('loadedmetadata', startPlayback, {{ once: true }});
      }} else {{
        startPlayback();
      }}
    }}

    function updateTimes() {{
      const chapter = manifest.chapters[currentIndex];
      const audioClock = audio.currentTime || 0;
      const local = Math.max(0, audioClock - chapterAudioStart(chapter));
      timeLabel.textContent = `${{formatTime(chapter.start + local)}} / ${{formatTime(manifest.duration)}}`;
      chapterTime.textContent = `${{formatTime(local)}} / ${{formatTime(chapter.duration)}}`;
      seekBar.value = chapter.duration ? String(Math.round((local / chapter.duration) * 1000)) : '0';
      updateHighlight(audioClock);
      const nextChapter = manifest.chapters[currentIndex + 1];
      if (!audio.paused && nextChapter && nextChapter.audio === chapter.audio && local >= chapter.duration - 0.15) {{
        loadChapter(currentIndex + 1, true, false);
      }}
    }}

    function updateHighlight(local) {{
      const chapter = manifest.chapters[currentIndex];
      let paragraph = null;
      for (let index = chapter.paragraphs.length - 1; index >= 0; index -= 1) {{
        const item = chapter.paragraphs[index];
        if (local >= item.localBegin && local < item.localEnd) {{
          paragraph = item;
          break;
        }}
      }}
      const nextId = paragraph ? paragraph.id : null;
      if (nextId === currentParagraphId) return;
      if (currentParagraphId) {{
        document.getElementById(currentParagraphId)?.classList.remove('active');
      }}
      currentParagraphId = nextId;
      if (currentParagraphId) {{
        const node = document.getElementById(currentParagraphId);
        node?.classList.add('active');
        node?.scrollIntoView({{ block: 'center', behavior: 'smooth' }});
        saveProgress(paragraph);
      }}
    }}

    playButton.addEventListener('click', () => {{
      if (audio.paused) {{
        audio.play();
      }} else {{
        audio.pause();
      }}
    }});
    prevButton.addEventListener('click', () => loadChapter(currentIndex - 1, !audio.paused));
    nextButton.addEventListener('click', () => loadChapter(currentIndex + 1, !audio.paused));
    seekBar.addEventListener('input', () => {{
      const chapter = manifest.chapters[currentIndex];
      audio.currentTime = chapterAudioStart(chapter) + (Number(seekBar.value) / 1000) * chapter.duration;
    }});
    audio.addEventListener('play', () => playButton.textContent = 'Pause');
    audio.addEventListener('pause', () => playButton.textContent = 'Play');
    audio.addEventListener('timeupdate', updateTimes);
    audio.addEventListener('loadedmetadata', updateTimes);
    audio.addEventListener('ended', () => {{
      if (currentIndex < manifest.chapters.length - 1) {{
        loadChapter(currentIndex + 1, true);
      }}
    }});

    const initialProgress = loadProgress();
    if (initialProgress && Number.isInteger(initialProgress.chapterIndex)) {{
      loadChapter(initialProgress.chapterIndex, false);
      if (Number.isFinite(initialProgress.currentTime)) {{
        audio.addEventListener('loadedmetadata', () => audio.currentTime = initialProgress.currentTime, {{ once: true }});
      }}
    }} else {{
      loadChapter(0, false);
    }}
  </script>
</body>
</html>
"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a local synchronized audiobook reader.")
    parser.add_argument("command", choices=["prepare", "align", "build", "all"])
    parser.add_argument("--pdf", type=Path, default=COLLAPSE_PDF)
    parser.add_argument("--epub", type=Path, default=COLLAPSE_EPUB)
    parser.add_argument("--audio-dir", type=Path, default=COLLAPSE_AUDIO_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--legacy-pdf", action="store_true", help="use the older PDF/logical-chapter pipeline")
    parser.add_argument("--metadata", type=Path, default=None, help="audiobook metadata JSON; defaults to audio-dir/metadata/metadata.json when present")
    parser.add_argument("--title", default=None, help="override the reader title")
    parser.add_argument("--chapter5-aligner", choices=["openai-anchors", "whisper", "aeneas"], default="openai-anchors", help="alignment backend for Tainter chapter 5")
    parser.add_argument("--whisper-command", default="whisper-cli", help="whisper.cpp command to run for chapter 5 transcription")
    parser.add_argument("--whisper-model", type=Path, default=Path("models/ggml-medium.en.bin"), help="local whisper.cpp ggml model path")
    parser.add_argument("--openai-transcribe-model", default="whisper-1", help="OpenAI audio transcription model for chapter 5 anchors")
    parser.add_argument("--anchor-interval-seconds", type=float, default=600.0, help="seconds between OpenAI anchor clips for chapter 5")
    parser.add_argument("--anchor-window-seconds", type=float, default=30.0, help="duration of each OpenAI anchor clip for chapter 5")
    parser.add_argument("--force", action="store_true", help="rerun existing Aeneas alignment files")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.legacy_pdf:
        if args.command != "all":
            raise RuntimeError("The EPUB/MP3-unit pipeline supports the 'all' command. Use --legacy-pdf for prepare/align/build.")
        source_path = args.pdf if args.pdf.exists() else args.epub
        if source_path.suffix.lower() == ".pdf" and source_path.name == COLLAPSE_PDF.name:
            generate_tainter_chapter_reader(
                source_path,
                args.audio_dir,
                args.output_dir,
                force=args.force,
                chapter5_aligner=args.chapter5_aligner,
                whisper_command=args.whisper_command,
                whisper_model=args.whisper_model,
                openai_transcribe_model=args.openai_transcribe_model,
                anchor_interval_seconds=args.anchor_interval_seconds,
                anchor_window_seconds=args.anchor_window_seconds,
            )
        else:
            generate_mp3_unit_reader(source_path, args.audio_dir, args.output_dir, force=args.force)
        print(f"reader ready: {args.output_dir / 'index.html'}")
        return 0

    metadata_path = args.metadata
    if metadata_path is None:
        candidate = args.audio_dir / "metadata" / "metadata.json"
        metadata_path = candidate if candidate.exists() else None
    metadata = load_metadata(metadata_path)
    book_config = book_config_from_metadata(metadata) if metadata else DEFAULT_BOOK_CONFIG
    if args.title:
        book_config = BookConfig(
            title=args.title,
            chapter_titles=book_config.chapter_titles,
            chapter_count=book_config.chapter_count,
        )
    if args.command in {"prepare", "all"}:
        prepare_inputs(args.pdf, args.audio_dir, args.output_dir, book_config)
    if args.command in {"align", "all"}:
        align_all(args.audio_dir, args.output_dir, book_config, metadata=metadata, force=args.force)
    if args.command in {"build", "all"}:
        build_reader(args.output_dir, args.audio_dir, book_config, metadata=metadata)
    if args.command in {"build", "all"}:
        print(f"reader ready: {args.output_dir / 'index.html'}")
    else:
        print(f"{args.command} complete: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

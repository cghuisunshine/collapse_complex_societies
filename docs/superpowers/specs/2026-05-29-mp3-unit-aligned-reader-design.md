# MP3-Unit Aligned Reader Design

## Goal

Update the aligned reader so `The Collapse of Complex Societies` is built from the local EPUB and audiobook files, with each numbered MP3 represented as one reader chapter and aligned at sentence level within that unit.

## Source Files

- EPUB: `The Collapse of Complex Societies (New Studies in -- Joseph A_ Tainter -- New Studies in Archaeology, 2008 -- Cambridge University Press (Virtual -- isbn13 9780521340922 -- 84237840522c3a9e8f584570a855572d -- Anna’s Archive.epub`
- Audio directory: `The Collapse of Complex Societies New Studies in Archaeology, Book 8`
- Output directory: `aligned_reader`

## Approach

The numbered MP3 files are the canonical reader units. The pipeline will list and sort `001` through `011`, extract readable text from the EPUB spine, split the extracted text into sentence fragments, distribute those fragments across the 11 audio units, run aeneas once per MP3, and build the existing manifest/HTML reader from those alignments.

This intentionally does not use the EPUB table of contents as the reader chapter structure. The EPUB contains six logical content chapters and back matter, while the audiobook is already split into 11 files including short intro/outro units. Using the MP3 files directly gives predictable playback and avoids fragile logical-chapter/audio remapping.

## Components

- `tools/reader_pipeline.py`
  - Add EPUB extraction helpers using Python standard-library ZIP/XML/HTML parsing.
  - Add sentence-level splitting for prose fragments.
  - Add MP3-unit preparation and manifest building.
  - Add or adapt a CLI path so `./run.sh` can generate this reader.

- `tests/test_reader_pipeline.py`
  - Add focused unit tests for EPUB spine extraction, sentence splitting, fragment distribution, and MP3-unit manifest shape.

- `aligned_reader/*`
  - Regenerated output: `text/chapter_###.txt`, `audio/chapter_###.mp3` or direct references, `alignments/chapter_###.json`, `manifest.json`, and `index.html`.

## Data Flow

1. Sort MP3 files by numeric prefix.
2. Extract EPUB metadata title.
3. Read EPUB spine HTML files in order.
4. Convert body HTML to plain text, skipping navigation/front matter noise where practical.
5. Split text into sentence fragments.
6. Allocate fragments to MP3 units by duration proportion, keeping at least one fragment per unit.
7. Write one text file per MP3 unit.
8. Run aeneas per MP3 text/audio pair.
9. Build the existing manifest with one reader chapter per MP3.
10. Generate `aligned_reader/index.html`.

## Error Handling

The pipeline should fail early if the EPUB is missing, no numbered MP3 files are found, no usable text fragments are extracted, or the number of generated text units does not match the number of MP3 files. Existing alignment validation continues to reject empty or non-monotonic timestamps.

## Testing

Use TDD for code changes. Unit tests should avoid invoking ffmpeg, ffprobe, aeneas, or large real files where mocks or temporary mini EPUBs are enough. After implementation, run `python3 -m unittest tests.test_reader_pipeline`.

## Constraints

This workspace is not a git repository, so the spec cannot be committed here.

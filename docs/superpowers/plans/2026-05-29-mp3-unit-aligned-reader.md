# MP3-Unit Aligned Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `aligned_reader` from the local EPUB and numbered MP3 files, with sentence-level alignment inside each MP3 unit.

**Architecture:** Extend `tools/reader_pipeline.py` with EPUB text extraction, sentence fragment generation, duration-based MP3-unit text allocation, and a manifest builder that treats each MP3 as one reader chapter. Reuse the current HTML reader and aeneas alignment flow.

**Tech Stack:** Python standard library, unittest, ffmpeg/ffprobe, aeneas via existing conda command, existing single-file HTML reader.

---

## File Structure

- Modify `tools/reader_pipeline.py`: EPUB parsing, sentence splitting, MP3-unit preparation, CLI defaults.
- Modify `tests/test_reader_pipeline.py`: unit tests for new helpers and manifest behavior.
- Regenerate `aligned_reader/*`: final reader assets after tests pass.

### Task 1: EPUB Text Extraction

**Files:**
- Modify: `tests/test_reader_pipeline.py`
- Modify: `tools/reader_pipeline.py`

- [ ] **Step 1: Write failing tests**

Add tests that create a tiny EPUB ZIP with `content.opf`, two HTML spine files, and metadata title. Assert that the helper returns the title and plain text in spine order.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_reader_pipeline`

- [ ] **Step 3: Implement minimal EPUB extraction**

Use `zipfile`, `xml.etree.ElementTree`, and `html.parser.HTMLParser` to find OPF, read spine item hrefs, strip tags, and normalize text.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_reader_pipeline`

### Task 2: Sentence Fragments and MP3 Unit Allocation

**Files:**
- Modify: `tests/test_reader_pipeline.py`
- Modify: `tools/reader_pipeline.py`

- [ ] **Step 1: Write failing tests**

Add tests for sentence splitting and duration-proportional allocation across three mock MP3 durations, ensuring all fragments are preserved and each unit receives text.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_reader_pipeline`

- [ ] **Step 3: Implement minimal helpers**

Add `split_text_into_sentences` and `split_fragments_by_durations`.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_reader_pipeline`

### Task 3: MP3-Unit Reader Pipeline

**Files:**
- Modify: `tests/test_reader_pipeline.py`
- Modify: `tools/reader_pipeline.py`

- [ ] **Step 1: Write failing tests**

Mock `run_aeneas`, `ffprobe_duration`, and `build_reader_html`. Assert that MP3-unit generation writes per-unit text files, alignment paths, manifest chapters, and index HTML.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_reader_pipeline`

- [ ] **Step 3: Implement the pipeline**

Add `generate_mp3_unit_reader(epub_path, audio_dir, output_dir, force=False)` and wire it into CLI command `all`.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_reader_pipeline`

### Task 4: Regenerate Reader

**Files:**
- Regenerate: `aligned_reader/*`

- [ ] **Step 1: Run pipeline**

Run: `./run.sh --force`

- [ ] **Step 2: Verify output**

Run: `python3 -m unittest tests.test_reader_pipeline`

Check: `aligned_reader/manifest.json` has 11 chapters and each chapter has sentence fragments.

## Constraints

The workspace is not a git repository, so commit steps are intentionally omitted.

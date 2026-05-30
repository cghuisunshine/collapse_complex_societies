import json
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest.mock import call, patch

from tools import reader_pipeline


class ReaderPipelineTests(unittest.TestCase):
    def write_minimal_epub(self, root: Path) -> Path:
        epub_path = root / "book.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("META-INF/container.xml", """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
                  <rootfiles>
                    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
                  </rootfiles>
                </container>
            """)
            archive.writestr("OEBPS/content.opf", """<?xml version="1.0"?>
                <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
                  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                    <dc:title>Example EPUB</dc:title>
                  </metadata>
                  <manifest>
                    <item id="first" href="chapter1.html" media-type="application/xhtml+xml"/>
                    <item id="second" href="chapter2.html" media-type="application/xhtml+xml"/>
                  </manifest>
                  <spine>
                    <itemref idref="first"/>
                    <itemref idref="second"/>
                  </spine>
                </package>
            """)
            archive.writestr("OEBPS/chapter1.html", "<html><body><h1>One</h1><p>First sentence. Second sentence?</p></body></html>")
            archive.writestr("OEBPS/chapter2.html", "<html><body><p>Third sentence!</p><script>ignored()</script></body></html>")
        return epub_path

    def test_aligned_reader_persists_latest_paragraph_in_local_storage(self):
        html = Path("aligned_reader/index.html").read_text(encoding="utf-8")

        self.assertIn("localStorage.setItem(PROGRESS_KEY", html)
        self.assertIn("localStorage.getItem(PROGRESS_KEY", html)
        self.assertIn("saveProgress(paragraph)", html)
        self.assertIn("loadChapter(initialProgress.chapterIndex", html)

    def test_extract_epub_text_reads_title_and_spine_html_in_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = self.write_minimal_epub(Path(temp_dir))

            result = reader_pipeline.extract_epub_text(epub_path)

        self.assertEqual(result.title, "Example EPUB")
        self.assertIn("One", result.text)
        self.assertLess(result.text.index("First sentence."), result.text.index("Third sentence!"))
        self.assertNotIn("ignored()", result.text)

    def test_split_text_into_sentences_preserves_headings_and_sentence_boundaries(self):
        fragments = reader_pipeline.split_text_into_sentences(
            "Chapter 1 Introduction\nFirst sentence. Second sentence? Third sentence!"
        )

        self.assertEqual(
            fragments,
            ["Chapter 1 Introduction", "First sentence.", "Second sentence?", "Third sentence!"],
        )

    def test_normalize_pdf_reader_text_repairs_wrapped_lines_before_sentence_split(self):
        normalized = reader_pipeline.normalize_pdf_reader_text(
            "Series editors\n"
            "Colin Renfrew, University of Cambridge\n"
            "This sentence wraps across\n"
            "two PDF lines before it ends.\n"
            "Stephen A.\n"
            "Kowalewski wrote the next title.\n"
        )

        fragments = reader_pipeline.split_text_into_sentences(normalized)

        self.assertIn("Series editors", fragments)
        self.assertIn("Colin Renfrew, University of Cambridge", fragments)
        self.assertIn("This sentence wraps across two PDF lines before it ends.", fragments)
        self.assertIn("Stephen A. Kowalewski wrote the next title.", fragments)

    def test_split_text_into_sentences_drops_bare_page_numbers(self):
        fragments = reader_pipeline.split_text_into_sentences("One sentence.\n27\nNext sentence.")

        self.assertEqual(fragments, ["One sentence.", "Next sentence."])

    def test_split_fragments_by_durations_preserves_all_fragments_with_one_bucket_per_audio(self):
        buckets = reader_pipeline.split_fragments_by_durations(
            ["one", "two", "three", "four", "five", "six"],
            [1.0, 2.0, 3.0],
        )

        self.assertEqual(len(buckets), 3)
        self.assertTrue(all(buckets))
        self.assertEqual([item for bucket in buckets for item in bucket], ["one", "two", "three", "four", "five", "six"])

    def test_generate_mp3_unit_reader_writes_text_alignments_manifest_and_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub_path = self.write_minimal_epub(root)
            audio_dir = root / "audio"
            output_dir = root / "reader"
            audio_dir.mkdir()
            first = audio_dir / "001 - Example.mp3"
            second = audio_dir / "002 - Example.mp3"
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")

            def fake_run_aeneas(audio_path, text_path, output_path, force=False):
                lines = [line for line in text_path.read_text(encoding="utf-8").splitlines() if line]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(
                        {
                            "fragments": [
                                {
                                    "id": f"f{index:06d}",
                                    "begin": f"{index - 1}.000",
                                    "end": f"{index}.000",
                                    "lines": [line],
                                }
                                for index, line in enumerate(lines, start=1)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            with (
                patch.object(reader_pipeline, "ffprobe_duration", side_effect=[1.0, 2.0, 1.0, 2.0]),
                patch.object(reader_pipeline, "run_aeneas", side_effect=fake_run_aeneas) as run_aeneas,
                patch.object(reader_pipeline, "build_reader_html", return_value="<html></html>"),
            ):
                reader_pipeline.generate_mp3_unit_reader(epub_path, audio_dir, output_dir)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], "Example EPUB")
            self.assertEqual([chapter["number"] for chapter in manifest["chapters"]], [1, 2])
            self.assertEqual([chapter["title"] for chapter in manifest["chapters"]], ["Unit 001", "Unit 002"])
            self.assertTrue((output_dir / "text" / "chapter_001.txt").exists())
            self.assertTrue((output_dir / "alignments" / "chapter_002.json").exists())
            self.assertEqual(run_aeneas.call_count, 2)

    def test_generate_mp3_unit_reader_accepts_pdf_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "book.pdf"
            pdf_path.write_text("", encoding="utf-8")
            audio_dir = root / "audio"
            output_dir = root / "reader"
            audio_dir.mkdir()
            first = audio_dir / "001 - Example.mp3"
            second = audio_dir / "002 - Example.mp3"
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")

            def fake_run_aeneas(audio_path, text_path, output_path, force=False):
                lines = [line for line in text_path.read_text(encoding="utf-8").splitlines() if line]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(
                        {
                            "fragments": [
                                {
                                    "id": f"f{index:06d}",
                                    "begin": f"{index - 1}.000",
                                    "end": f"{index}.000",
                                    "lines": [line],
                                }
                                for index, line in enumerate(lines, start=1)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            with (
                patch.object(
                    reader_pipeline,
                    "extract_pdf_reader_text",
                    return_value=reader_pipeline.EpubText(
                        title="PDF Title",
                        text="First sentence. Second sentence. Third sentence. Fourth sentence.",
                    ),
                ) as extract_pdf_reader_text,
                patch.object(reader_pipeline, "ffprobe_duration", side_effect=[1.0, 1.0, 1.0, 1.0]),
                patch.object(reader_pipeline, "run_aeneas", side_effect=fake_run_aeneas),
                patch.object(reader_pipeline, "build_reader_html", return_value="<html></html>"),
            ):
                reader_pipeline.generate_mp3_unit_reader(pdf_path, audio_dir, output_dir)

            extract_pdf_reader_text.assert_called_once_with(pdf_path)
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], "PDF Title")
            self.assertEqual([chapter["number"] for chapter in manifest["chapters"]], [1, 2])

    def test_extract_tainter_chapters_uses_pdf_chapter_ranges_and_stops_before_references(self):
        source = """
        Front matter

        1

        Introduction to collapse
        First chapter first sentence. First chapter second sentence.

        2

        The nature of complex societies
        Second chapter sentence.

        3

        The study of collapse
        Third chapter sentence.

        4

        Understanding collapse: the marginal productivity of sociopolitical change
        Fourth chapter sentence.

        5

        Evaluation: complexity and marginal returns in collapsing societies
        Fifth chapter sentence.

        6

        Summary and implications
        Sixth chapter sentence.

        REFERENCES
        Reference text should not be included.
        """

        chapters = reader_pipeline.extract_tainter_chapters(source)

        self.assertEqual([chapter.number for chapter in chapters], [1, 2, 3, 4, 5, 6])
        self.assertEqual(chapters[2].title, "The Study of Collapse, Part One")
        self.assertIn("First chapter first sentence.", chapters[0].body)
        self.assertNotIn("Reference text", chapters[-1].body)

    def test_generate_tainter_chapter_reader_uses_openai_anchors_for_chapter_5_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "book.pdf"
            pdf_path.write_text("", encoding="utf-8")
            audio_dir = root / "audio"
            output_dir = root / "reader"
            audio_dir.mkdir()
            for index in range(1, 12):
                (audio_dir / f"{index:03d} - Example.mp3").write_text("", encoding="utf-8")

            source = """
            1

            Introduction to collapse
            One one. One two.

            2

            The nature of complex societies
            Two one. Two two.

            3

            The study of collapse
            Three one. Three two.

            4

            Understanding collapse: the marginal productivity of sociopolitical change
            Four one. Four two.

            5

            Evaluation: complexity and marginal returns in collapsing societies
            Five one. Over the short-term the collapse probably resulted in an improved standard of living for a peasant population suddenly relieved of the burden of supporting a hierarchy (cf. Sanders 1973).

            6

            Summary and implications
            Six one. Six two.

            REFERENCES
            References.
            """

            def fake_run_aeneas(audio_path, text_path, output_path, force=False):
                lines = [line for line in text_path.read_text(encoding="utf-8").splitlines() if line]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(
                        {
                            "fragments": [
                                {
                                    "id": f"f{index:06d}",
                                    "begin": f"{index - 1}.000",
                                    "end": f"{index}.000",
                                    "lines": [line],
                                }
                                for index, line in enumerate(lines, start=1)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            def fake_concat(parts, output_path, force=False):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("+".join(path.name[:3] for path in parts), encoding="utf-8")
                return output_path

            def fake_duration(path):
                return float(int(path.name[:3]))

            def fake_align_chapter_5_by_openai_anchors(
                chapter,
                audio_dir_arg,
                text_dir,
                align_dir,
                force=False,
                openai_transcribe_model="whisper-1",
                anchor_interval_seconds=600.0,
                anchor_window_seconds=30.0,
            ):
                fragments = reader_pipeline.chapter_sentence_fragments(chapter)
                (align_dir / "chapter_005.json").write_text(
                    json.dumps(
                        {
                            "fragments": [
                                {
                                    "id": f"f{index:06d}",
                                    "begin": f"{index - 1}.000",
                                    "end": f"{index}.000",
                                    "lines": [line],
                                }
                                for index, line in enumerate(fragments, start=1)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            with (
                patch.object(reader_pipeline, "extract_pdf_reader_text", return_value=reader_pipeline.EpubText("PDF Title", source)),
                patch.object(reader_pipeline, "ffprobe_duration", side_effect=fake_duration),
                patch.object(reader_pipeline, "run_aeneas", side_effect=fake_run_aeneas) as run_aeneas,
                patch.object(reader_pipeline, "align_tainter_chapter_5_by_openai_anchors", side_effect=fake_align_chapter_5_by_openai_anchors) as align_chapter_5_by_openai_anchors,
                patch.object(reader_pipeline, "concatenate_audio_parts", side_effect=fake_concat) as concatenate_audio_parts,
                patch.object(reader_pipeline, "build_reader_html", return_value="<html></html>"),
            ):
                reader_pipeline.generate_tainter_chapter_reader(pdf_path, audio_dir, output_dir, force=True)

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], "PDF Title")
            self.assertEqual([chapter["number"] for chapter in manifest["chapters"]], [1, 2, 3, 4, 5, 6])
            self.assertEqual([chapter["audio"] for chapter in manifest["chapters"]], [f"audio/chapter_{index:03d}.mp3" for index in range(1, 7)])
            self.assertEqual([chapter["duration"] for chapter in manifest["chapters"]], [2.0, 3.0, 9.0, 6.0, 24.0, 10.0])
            self.assertEqual(run_aeneas.call_count, 5)
            align_chapter_5_by_openai_anchors.assert_called_once_with(
                reader_pipeline.Chapter(
                    5,
                    "Evaluation: Complexity and Marginal Returns in Collapsing Societies, Part One",
                    "Five one. Over the short-term the collapse probably resulted in an improved standard of living for a peasant population suddenly relieved of the burden of supporting a hierarchy (cf. Sanders 1973).",
                ),
                audio_dir,
                output_dir / "text",
                output_dir / "alignments",
                force=True,
                openai_transcribe_model="whisper-1",
                anchor_interval_seconds=600.0,
                anchor_window_seconds=30.0,
            )
            concatenate_audio_parts.assert_has_calls(
                [
                    call([audio_dir / "004 - Example.mp3", audio_dir / "005 - Example.mp3"], output_dir / "audio" / "chapter_003.mp3", force=True),
                    call(
                        [
                            audio_dir / "007 - Example.mp3",
                            audio_dir / "008 - Example.mp3",
                            audio_dir / "009 - Example.mp3",
                        ],
                        output_dir / "audio" / "chapter_005.mp3",
                        force=True,
                    ),
                ]
            )
            self.assertFalse((output_dir / "audio" / "chapter_007.mp3").exists())

    def test_generate_tainter_chapter_reader_can_use_aeneas_for_chapter_5(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "book.pdf"
            pdf_path.write_text("", encoding="utf-8")
            audio_dir = root / "audio"
            output_dir = root / "reader"
            audio_dir.mkdir()
            for index in range(1, 12):
                (audio_dir / f"{index:03d} - Example.mp3").write_text("", encoding="utf-8")
            source = """
            1
            Introduction to collapse
            One one.
            2
            The nature of complex societies
            Two one.
            3
            The study of collapse
            Three one.
            4
            Understanding collapse: the marginal productivity of sociopolitical change
            Four one.
            5
            Evaluation: complexity and marginal returns in collapsing societies
            Five one. Over the short-term the collapse probably resulted in an improved standard of living for a peasant population.
            6
            Summary and implications
            Six one.
            REFERENCES
            References.
            """

            def fake_run_aeneas(audio_path, text_path, output_path, force=False):
                lines = [line for line in text_path.read_text(encoding="utf-8").splitlines() if line]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(
                        {
                            "fragments": [
                                {"id": f"f{index:06d}", "begin": f"{index - 1}.000", "end": f"{index}.000", "lines": [line]}
                                for index, line in enumerate(lines, start=1)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            with (
                patch.object(reader_pipeline, "extract_pdf_reader_text", return_value=reader_pipeline.EpubText("PDF Title", source)),
                patch.object(reader_pipeline, "ffprobe_duration", side_effect=lambda path: float(int(path.name[:3]))),
                patch.object(reader_pipeline, "run_aeneas", side_effect=fake_run_aeneas) as run_aeneas,
                patch.object(reader_pipeline, "concatenate_audio_parts", side_effect=lambda parts, output_path, force=False: output_path),
                patch.object(reader_pipeline, "build_reader_html", return_value="<html></html>"),
            ):
                reader_pipeline.generate_tainter_chapter_reader(
                    pdf_path,
                    audio_dir,
                    output_dir,
                    force=True,
                    chapter5_aligner="aeneas",
                )

            self.assertEqual(run_aeneas.call_count, 8)
            run_aeneas.assert_any_call(
                audio_dir / "007 - Example.mp3",
                output_dir / "text" / "chapter_005_part_001.txt",
                output_dir / "alignments" / "chapter_005_part_001.json",
                force=True,
            )

    def test_apply_reader_timestamp_overrides_updates_matching_manifest_paragraph(self):
        manifest = {
            "title": "Example",
            "duration": 20.0,
            "chapters": [
                {
                    "number": 5,
                    "start": 100.0,
                    "paragraphs": [
                        {
                            "id": "c005_f001227",
                            "text": "It was a predictable adjustment to an otherwise insolvable dilemma.",
                            "begin": 190.0,
                            "end": 200.0,
                            "localBegin": 90.0,
                            "localEnd": 100.0,
                        },
                        {
                            "id": "c005_f001228",
                            "text": "Over the short-term the collapse probably resulted in an improved standard of living for a peasant population suddenly relieved of the burden of supporting a hierarchy (cf. Sanders 1973).",
                            "begin": 200.0,
                            "end": 210.0,
                            "localBegin": 100.0,
                            "localEnd": 110.0,
                        },
                        {
                            "id": "c005_f001229",
                            "text": "In the long run, though, the agricultural population was itself decimated.",
                            "begin": 210.0,
                            "end": 214.0,
                            "localBegin": 110.0,
                            "localEnd": 114.0,
                        },
                    ],
                }
            ],
        }

        reader_pipeline.apply_reader_timestamp_overrides(manifest)

        previous = manifest["chapters"][0]["paragraphs"][0]
        paragraph = manifest["chapters"][0]["paragraphs"][1]
        following = manifest["chapters"][0]["paragraphs"][2]
        self.assertEqual(previous["localBegin"], 90.0)
        self.assertEqual(previous["localEnd"], 100.0)
        self.assertEqual(paragraph["localBegin"], 8119.0)
        self.assertEqual(paragraph["begin"], 8219.0)
        self.assertEqual(paragraph["localEnd"], 8129.0)
        self.assertEqual(paragraph["end"], 8229.0)
        self.assertEqual(following["localBegin"], 8129.0)
        self.assertEqual(following["begin"], 8229.0)
        self.assertEqual(following["localEnd"], 8133.0)
        self.assertEqual(following["end"], 8233.0)

    def test_apply_alignment_timestamp_overrides_shifts_target_and_following_fragments(self):
        alignment = {
            "fragments": [
                {
                    "id": "f001227",
                    "begin": "90.000",
                    "end": "100.000",
                    "lines": ["It was a predictable adjustment to an otherwise insolvable dilemma."],
                },
                {
                    "id": "f001228",
                    "begin": "100.000",
                    "end": "110.000",
                    "lines": [
                        "Over the short-term the collapse probably resulted in an improved standard of living for a peasant population suddenly relieved of the burden of supporting a hierarchy (cf. Sanders 1973)."
                    ],
                },
                {
                    "id": "f001229",
                    "begin": "110.000",
                    "end": "114.000",
                    "lines": ["In the long run, though, the agricultural population was itself decimated."],
                },
            ]
        }

        reader_pipeline.apply_alignment_timestamp_overrides(alignment, chapter_number=5)

        self.assertEqual(alignment["fragments"][0]["begin"], "90.000")
        self.assertEqual(alignment["fragments"][1]["begin"], "8119.000")
        self.assertEqual(alignment["fragments"][1]["end"], "8129.000")
        self.assertEqual(alignment["fragments"][2]["begin"], "8129.000")
        self.assertEqual(alignment["fragments"][2]["end"], "8133.000")

    def test_chapter_sentence_fragments_removes_tainter_chapter_5_figure_blocks(self):
        chapter = reader_pipeline.Chapter(
            number=5,
            title="Evaluation",
            body=(
                "Inline prose keeps a figure reference (see Fig.\n"
                "Fig.\n"
                "San Juan Basin and surrounding terrain (after Tainter and Gillio 1980: 111).\n"
                "As archaeologists are beginning to realize, this was not the end.\n"
                "Fig.\n"
                "The Chacoan regional system, A.D. 1050 - 1175 (after Powers et al.\n"
                "Courtesy of the U.S. National Park Service.\n"
                "Around 900 A.D. major changes began.\n"
            ),
        )

        fragments = reader_pipeline.chapter_sentence_fragments(chapter)

        self.assertIn("Inline prose keeps a figure reference.", fragments)
        self.assertIn("As archaeologists are beginning to realize, this was not the end.", fragments)
        self.assertIn("Around 900 A.D. major changes began.", fragments)
        self.assertNotIn("Fig.", fragments)
        self.assertFalse(any(fragment.startswith("San Juan Basin and surrounding terrain") for fragment in fragments))
        self.assertFalse(any(fragment.startswith("The Chacoan regional system") for fragment in fragments))
        self.assertFalse(any(fragment.startswith("Courtesy of the U.S. National Park Service") for fragment in fragments))

    def test_merge_split_citation_fragments_repairs_broken_tainter_citations(self):
        fragments = [
            "These issues were discussed by Frank 1940:",
            "7), Smith 1962:",
            "18).",
            "Next spoken sentence.",
        ]

        merged = reader_pipeline.merge_split_citation_fragments(fragments)

        self.assertEqual(
            merged,
            [
                "These issues were discussed by Frank 1940: 7), Smith 1962: 18).",
                "Next spoken sentence.",
            ],
        )

    def test_normalize_tainter_chapter_5_fragments_removes_isolated_see_fig_without_swallowing_next_sentence(self):
        fragments = [
            "The population grew rapidly (see Fig.",
            "This pressure coincided with higher administrative cost.",
            "Fig.",
            "Debasement of the denarius under Roman emperors.",
            "Normal narration resumes.",
        ]

        normalized = reader_pipeline.normalize_tainter_chapter_5_fragments(fragments)

        self.assertEqual(
            normalized,
            [
                "The population grew rapidly.",
                "This pressure coincided with higher administrative cost.",
                "Normal narration resumes.",
            ],
        )

    def test_normalize_tainter_chapter_5_fragments_repairs_dangling_figure_number_continuation(self):
        fragments = [
            "The Mayan political centers of this region (Fig.",
            "24) are numerous and varied.",
            "The increasing costliness is illustrated in Figs.",
            "25 and 26, which depict monument construction.",
            "Next sentence remains.",
        ]

        normalized = reader_pipeline.normalize_tainter_chapter_5_fragments(fragments)

        self.assertEqual(
            normalized,
            [
                "The Mayan political centers of this region are numerous and varied.",
                "The increasing costliness is illustrated.",
                "Next sentence remains.",
            ],
        )

    def test_normalize_tainter_chapter_5_fragments_removes_standalone_dangling_figure_marker(self):
        fragments = [
            "(see Fig.",
            "Augustus terminated the policy of expansion.",
        ]

        normalized = reader_pipeline.normalize_tainter_chapter_5_fragments(fragments)

        self.assertEqual(normalized, ["Augustus terminated the policy of expansion."])

    def test_merge_split_citation_fragments_merges_orphan_citation_fragments_backward(self):
        fragments = [
            "He debased the silver denarius (A. Jones 1974: 191;",
            "Heichelheim 1970: 213-14; Mattingly 1960: 121).",
            "Next sentence.",
        ]

        merged = reader_pipeline.merge_split_citation_fragments(fragments)

        self.assertEqual(
            merged,
            [
                "He debased the silver denarius (A. Jones 1974: 191; Heichelheim 1970: 213-14; Mattingly 1960: 121).",
                "Next sentence.",
            ],
        )

    def test_merge_split_citation_fragments_does_not_swallow_normal_sentence_after_dangling_citation(self):
        fragments = [
            "The program funded Italian orphans (M. Hammond 1946: 82; Duncan-Jones 1974:",
            "The emperor Trajan embarked on an ambitious program.",
        ]

        merged = reader_pipeline.merge_split_citation_fragments(fragments)

        self.assertEqual(
            merged,
            [
                "The program funded Italian orphans.",
                "The emperor Trajan embarked on an ambitious program.",
            ],
        )

    def test_audit_tainter_chapter_5_fragments_reports_suspicious_fragments_and_density(self):
        fragments = [
            "Chapter 5. Evaluation.",
            "Good spoken sentence.",
            "The centers (Fig.",
            "Normal ending.",
            "Author 1970: 12).",
        ]

        report = reader_pipeline.audit_tainter_chapter_5_fragments(fragments, [10.0, 20.0, 30.0])

        self.assertEqual(report["fragment_count"], 5)
        self.assertEqual(len(report["segments"]), 3)
        self.assertTrue(any(item["fragment"] == "The centers (Fig." for item in report["suspicious_fragments"]))
        self.assertTrue(any(item["fragment"] == "Author 1970: 12)." for item in report["suspicious_fragments"]))

    def test_chapter_5_segment_boundaries_start_part_3_at_known_anchor(self):
        fragments = [
            "Chapter 5. Evaluation.",
            "Before anchor one.",
            "Before anchor two.",
            "Before anchor three.",
            "Before anchor four.",
            "Over the short-term the collapse probably resulted in an improved standard of living for a peasant population.",
            "After anchor.",
        ]

        boundaries = reader_pipeline.chapter_5_segment_boundaries(fragments, [100.0, 200.0, 300.0])
        buckets = [fragments[start:end] for start, end in boundaries]

        self.assertEqual(len(buckets), 3)
        self.assertEqual(buckets[2][0], fragments[5])
        self.assertEqual([item for bucket in buckets for item in bucket], fragments)

    def test_merge_segment_alignments_offsets_fragments_and_applies_chapter_5_anchor(self):
        segments = [
            {
                "fragments": [
                    {"id": "f000001", "begin": "0.000", "end": "1.000", "lines": ["Before."]},
                ]
            },
            {
                "fragments": [
                    {"id": "f000001", "begin": "0.000", "end": "2.000", "lines": ["Middle."]},
                ]
            },
            {
                "fragments": [
                    {
                        "id": "f000001",
                        "begin": "0.000",
                        "end": "10.000",
                        "lines": [
                            "Over the short-term the collapse probably resulted in an improved standard of living for a peasant population."
                        ],
                    },
                    {"id": "f000002", "begin": "10.000", "end": "14.000", "lines": ["After anchor."]},
                ]
            },
        ]

        merged = reader_pipeline.merge_segment_alignments(segments, [10.0, 20.0, 30.0], chapter_number=5)

        self.assertEqual([fragment["id"] for fragment in merged["fragments"]], ["f000001", "f000002", "f000003", "f000004"])
        self.assertEqual(merged["fragments"][0]["begin"], "0.000")
        self.assertEqual(merged["fragments"][1]["begin"], "10.000")
        self.assertEqual(merged["fragments"][2]["begin"], "8119.000")
        self.assertEqual(merged["fragments"][3]["begin"], "8129.000")

    def test_load_whisper_transcript_accepts_openai_and_whisper_cpp_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            openai_path = root / "openai.json"
            openai_path.write_text(
                json.dumps({"segments": [{"start": 1.25, "end": 2.5, "text": "Hello world."}]}),
                encoding="utf-8",
            )
            cpp_path = root / "cpp.json"
            cpp_path.write_text(
                json.dumps(
                    {
                        "transcription": [
                            {
                                "timestamps": {"from": "00:00:03,000", "to": "00:00:04,500"},
                                "text": "Another line.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(reader_pipeline.load_whisper_transcript(openai_path), [{"start": 1.25, "end": 2.5, "text": "Hello world."}])
            self.assertEqual(reader_pipeline.load_whisper_transcript(cpp_path), [{"start": 3.0, "end": 4.5, "text": "Another line."}])

    def test_align_fragments_to_whisper_segments_splits_one_segment_proportionally(self):
        fragments = ["First sentence.", "Second sentence."]
        segments = [{"start": 10.0, "end": 20.0, "text": "First sentence. Second sentence."}]

        alignment = reader_pipeline.align_fragments_to_whisper_segments(fragments, segments)

        self.assertEqual([fragment["lines"][0] for fragment in alignment["fragments"]], fragments)
        self.assertEqual(alignment["fragments"][0]["begin"], "10.000")
        self.assertEqual(alignment["fragments"][1]["end"], "20.000")
        self.assertLess(float(alignment["fragments"][0]["end"]), float(alignment["fragments"][1]["end"]))
        self.assertEqual(alignment["audit"]["unmatched_fragments"], [])

    def test_align_fragments_to_whisper_segments_interpolates_unmatched_fragments_monotonically(self):
        fragments = ["Matched first.", "Unspoken artifact.", "Matched last."]
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Matched first."},
            {"start": 8.0, "end": 10.0, "text": "Matched last."},
        ]

        alignment = reader_pipeline.align_fragments_to_whisper_segments(fragments, segments)

        begins = [float(fragment["begin"]) for fragment in alignment["fragments"]]
        ends = [float(fragment["end"]) for fragment in alignment["fragments"]]
        self.assertEqual(begins, sorted(begins))
        self.assertEqual(alignment["fragments"][1]["begin"], "2.000")
        self.assertEqual(alignment["fragments"][1]["end"], "8.000")
        self.assertEqual(alignment["audit"]["unmatched_fragments"][0]["fragment"], "Unspoken artifact.")

    def test_run_whisper_cpp_invokes_whisper_cli_and_requires_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_path = root / "audio.mp3"
            model_path = root / "model.bin"
            output_path = root / "transcript.json"
            audio_path.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Whisper model not found"):
                reader_pipeline.run_whisper_cpp(audio_path, output_path, "whisper-cli", model_path)

            model_path.write_text("", encoding="utf-8")
            with patch.object(reader_pipeline.subprocess, "run") as run:
                reader_pipeline.run_whisper_cpp(audio_path, output_path, "whisper-cli", model_path, force=True)

            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[:2], ["whisper-cli", "-m"])
            self.assertIn(str(model_path), command)
            self.assertIn(str(audio_path), command)

    def test_chapter_5_anchor_times_are_every_interval_and_clip_near_end(self):
        anchors = reader_pipeline.chapter_5_anchor_times(1250.0, interval_seconds=600.0, window_seconds=30.0)

        self.assertEqual(
            anchors,
            [
                {"start": 0.0, "duration": 30.0},
                {"start": 600.0, "duration": 30.0},
                {"start": 1200.0, "duration": 30.0},
            ],
        )

    def test_cut_audio_window_invokes_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp3"
            target = root / "clip.mp3"
            source.write_text("", encoding="utf-8")

            with patch.object(reader_pipeline.subprocess, "run") as run:
                reader_pipeline.cut_audio_window(source, 600.0, 30.0, target, force=True)

            command = run.call_args.args[0]
            self.assertEqual(command[:6], ["ffmpeg", "-y", "-v", "error", "-ss", "600.000"])
            self.assertIn("30.000", command)
            self.assertEqual(command[-1], str(target))

    def test_run_openai_transcription_builds_verbose_timestamp_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "clip.mp3"
            output = root / "transcript.json"
            audio.write_bytes(b"audio")

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return b'{"segments":[]}'

            with patch.object(reader_pipeline.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen:
                reader_pipeline.run_openai_transcription(audio, output, "whisper-1", "test-key", force=True)

            request = urlopen.call_args.args[0]
            body = request.data.decode("utf-8", errors="ignore")
            self.assertEqual(request.full_url, "https://api.openai.com/v1/audio/transcriptions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertIn('name="model"', body)
            self.assertIn("whisper-1", body)
            self.assertIn('name="response_format"', body)
            self.assertIn("verbose_json", body)
            self.assertIn('name="timestamp_granularities[]"', body)
            self.assertIn("segment", body)
            self.assertIn("word", body)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"segments": []})

    def test_load_openai_transcript_normalizes_segments_and_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "transcript.json"
            path.write_text(
                json.dumps(
                    {
                        "segments": [{"start": 1.0, "end": 3.0, "text": "hello world"}],
                        "words": [{"start": 1.0, "end": 1.5, "word": "hello"}],
                    }
                ),
                encoding="utf-8",
            )

            transcript = reader_pipeline.load_openai_transcript(path)

        self.assertEqual(transcript["text"], "hello world")
        self.assertEqual(transcript["segments"], [{"start": 1.0, "end": 3.0, "text": "hello world"}])
        self.assertEqual(transcript["words"], [{"start": 1.0, "end": 1.5, "text": "hello"}])

    def test_match_anchor_transcript_to_fragments_returns_monotonic_anchor(self):
        fragments = ["Chapter 5.", "The Roman collapse was expensive.", "The Mayan collapse followed."]
        transcript = {"text": "Roman collapse was expensive", "segments": [{"start": 2.0, "end": 5.0, "text": "Roman collapse was expensive"}], "words": []}

        anchor = reader_pipeline.match_anchor_transcript_to_fragments(transcript, fragments, clip_start=600.0)

        self.assertEqual(anchor["fragment_index"], 1)
        self.assertEqual(anchor["chapter_time"], 602.0)
        self.assertGreater(anchor["score"], 0.8)

    def test_chapter_5_boundaries_from_anchors_includes_manual_anchor_and_endpoints(self):
        fragments = [
            "Chapter 5.",
            "Early.",
            "Middle.",
            "Over the short-term the collapse probably resulted in an improved standard of living.",
            "Late.",
        ]
        anchors = [
            {"fragment_index": 2, "chapter_time": 1200.0, "score": 0.9},
        ]

        boundaries = reader_pipeline.chapter_5_boundaries_from_anchors(fragments, anchors, duration=9000.0)

        self.assertEqual(boundaries[0]["fragment_start"], 0)
        self.assertEqual(boundaries[0]["time_start"], 0.0)
        self.assertTrue(any(boundary["fragment_start"] == 3 and boundary["time_start"] == 8119.0 for boundary in boundaries))
        self.assertEqual(boundaries[-1]["fragment_end"], len(fragments))
        self.assertEqual(boundaries[-1]["time_end"], 9000.0)

    def test_merge_interval_alignments_offsets_to_chapter_time(self):
        intervals = [
            {
                "time_start": 100.0,
                "alignment": {
                    "fragments": [
                        {"id": "f000001", "begin": "0.000", "end": "2.000", "lines": ["First."]},
                    ]
                },
            },
            {
                "time_start": 200.0,
                "alignment": {
                    "fragments": [
                        {"id": "f000001", "begin": "1.000", "end": "3.000", "lines": ["Second."]},
                    ]
                },
            },
        ]

        merged = reader_pipeline.merge_interval_alignments(intervals, chapter_number=None)

        self.assertEqual([fragment["id"] for fragment in merged["fragments"]], ["f000001", "f000002"])
        self.assertEqual(merged["fragments"][0]["begin"], "100.000")
        self.assertEqual(merged["fragments"][1]["begin"], "201.000")

    def test_extract_chapters_skips_contents_and_splits_real_chapters(self):
        source = """
        Contents
        ONE
        Dudley Demented . 1
        TWO
        A Peck of Owls . 20

        Harry Potter
        And the Order OF Phoenix

        CHAPTER ONE

        DUDLEY DEMENTED

        First paragraph of chapter one.

        CHAPTER ONE

        Repeated page header should stay inside the first chapter body.

        Second paragraph.

        CHAPTER TWO

        A PECK OF OWLS

        Another chapter starts here.
        """

        chapters = reader_pipeline.extract_chapters(source)

        self.assertEqual([chapter.number for chapter in chapters], [1, 2])
        self.assertEqual(chapters[0].title, "Dudley Demented")
        self.assertEqual(chapters[1].title, "A Peck of Owls")
        self.assertIn("First paragraph", chapters[0].body)
        self.assertNotIn("Contents", chapters[0].body)

    def test_normalize_paragraphs_repairs_wrapped_lines_and_drops_page_artifacts(self):
        body = """
        T      he hottest day of the summer so far was drawing to a close and
               a drowsy silence lay over the large, square houses of Privet
        Drive.
                                     \x91   1   \x91

            On the whole, Harry thought he was to be congratulated on his
        idea of hiding here.
        """

        paragraphs = reader_pipeline.normalize_paragraphs(body)

        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(
            paragraphs[0],
            "The hottest day of the summer so far was drawing to a close and a drowsy silence lay over the large, square houses of Privet Drive.",
        )
        self.assertEqual(
            paragraphs[1],
            "On the whole, Harry thought he was to be congratulated on his idea of hiding here.",
        )

    def test_normalize_paragraphs_uses_indents_and_drops_running_headers(self):
        body = textwrap.dedent("""
        First paragraph continues
        across this wrapped line.
            Second paragraph starts by indentation.
        DUDLEY DEMENTED
        More text in the second paragraph.
        """)

        paragraphs = reader_pipeline.normalize_paragraphs(body, running_headers={"DUDLEY DEMENTED"})

        self.assertEqual(
            paragraphs,
            [
                "First paragraph continues across this wrapped line.",
                "Second paragraph starts by indentation. More text in the second paragraph.",
            ],
        )

    def test_normalize_paragraphs_splits_adjacent_left_aligned_pdf_paragraphs(self):
        body = textwrap.dedent("""
        First paragraph starts here and wraps
        across this line before ending.
        Second paragraph starts immediately after it
        and wraps too.
        "Dialogue can start a new paragraph too."
        """)

        paragraphs = reader_pipeline.normalize_paragraphs(body)

        self.assertEqual(
            paragraphs,
            [
                "First paragraph starts here and wraps across this line before ending.",
                "Second paragraph starts immediately after it and wraps too.",
                '"Dialogue can start a new paragraph too."',
            ],
        )

    def test_normalize_paragraphs_drops_numbered_book_running_headers(self):
        body = """
        The effect was incredible: Dudley gasped and fell off his chair.
        8                         HARRY POTTER
        clapped her hands to her mouth.
        """

        paragraphs = reader_pipeline.normalize_paragraphs(body)

        self.assertEqual(
            paragraphs,
            ["The effect was incredible: Dudley gasped and fell off his chair. clapped her hands to her mouth."],
        )

    def test_normalize_paragraphs_drops_author_book_running_headers(self):
        body = """
        The bridge was fewer than ten years old, and the best experts were at a loss to explain
        J.K. Rowling HARRY POTTER AND THE HALF-BLOOD PRINCE
        why it had snapped cleanly in two.
        """

        paragraphs = reader_pipeline.normalize_paragraphs(body)

        self.assertEqual(
            paragraphs,
            [
                "The bridge was fewer than ten years old, and the best experts were at a loss to explain why it had snapped cleanly in two."
            ],
        )

    def test_normalize_paragraphs_preserves_compound_hyphen_line_breaks(self):
        body = """
        top-of-
        the-range broomstick

        sum-
        mer holidays
        """

        paragraphs = reader_pipeline.normalize_paragraphs(body)

        self.assertEqual(paragraphs, ["top-of-the-range broomstick", "summer holidays"])

    def test_extract_chapter_without_visible_title_keeps_body(self):
        source = """
        CHAPTER THREE

        But Hedwig didn't return next morning. Harry spent the day in his
        bedroom.
        """

        chapters = reader_pipeline.extract_chapters(source)

        self.assertEqual(chapters[0].number, 3)
        self.assertEqual(chapters[0].title, "The Advance Guard")
        self.assertTrue(chapters[0].body.startswith("But Hedwig"))

    def test_split_title_and_body_strips_mixed_case_printed_title(self):
        title, body = reader_pipeline.split_title_and_body("""
        The Ghoul in Pajamas

        First paragraph starts here.
        """)

        self.assertEqual(title, "The Ghoul in Pajamas")
        self.assertTrue(body.strip().startswith("First paragraph"))

    def test_extract_chapters_accepts_decorative_headings_and_expected_titles(self):
        source = """
        Front matter

                  — CHAPTER ONE —



              The Worst Birthday
        First paragraph starts here.

        THE WORST BIRTHDAY 9
        Wrapped line continues.

                  — CHAPTER TWO —

              Dobby's Warning
        Another chapter starts here.
        """

        chapters = reader_pipeline.extract_chapters(
            source,
            chapter_titles={1: "The Worst Birthday", 2: "Dobby's Warning"},
            chapter_count=2,
        )

        self.assertEqual([chapter.number for chapter in chapters], [1, 2])
        self.assertEqual(chapters[0].title, "The Worst Birthday")
        self.assertTrue(chapters[0].body.startswith("First paragraph"))
        self.assertNotIn("The Worst Birthday", chapters[0].body.splitlines()[0])

    def test_extract_chapters_accepts_numeric_headings_and_split_expected_titles(self):
        source = """
        Contents
        1 The Dark Lord Ascending         1
        2 In Memoriam                    13

        Chapter 1

        The Dark Lord
        Ascending

        First paragraph starts here.

                            Chapter 1

        Running page header should stay inside the chapter body.

        Chapter 2

        In Memoriam

        Another chapter starts here.
        """

        chapters = reader_pipeline.extract_chapters(
            source,
            chapter_titles={1: "The Dark Lord Ascending", 2: "In Memoriam"},
            chapter_count=2,
        )

        self.assertEqual([chapter.number for chapter in chapters], [1, 2])
        self.assertEqual(chapters[0].title, "The Dark Lord Ascending")
        self.assertTrue(chapters[0].body.startswith("First paragraph"))
        self.assertNotIn("The Dark Lord", chapters[0].body.splitlines()[0])

    def test_extract_chapters_accepts_word_headings_with_inline_titles(self):
        source = """
        CHAPTER ONE - THE RIDDLE HOUSE
        First paragraph starts here.

        CHAPTER TWO - THE SCAR
        Another chapter starts here.
        """

        chapters = reader_pipeline.extract_chapters(
            source,
            chapter_titles={1: "The Riddle House", 2: "The Scar"},
            chapter_count=2,
        )

        self.assertEqual([chapter.number for chapter in chapters], [1, 2])
        self.assertEqual(chapters[0].title, "The Riddle House")
        self.assertTrue(chapters[0].body.startswith("First paragraph"))

    def test_extract_chapters_keeps_hyphenated_word_numbers_separate_from_inline_title(self):
        source = """
        CHAPTER TWENTY-ONE - THE HOUSE-ELF LIBERATION FRONT
        First paragraph starts here.
        """

        chapters = reader_pipeline.extract_chapters(
            source,
            chapter_titles={21: "The House-Elf Liberation Front"},
            chapter_count=21,
        )

        self.assertEqual([chapter.number for chapter in chapters], [21])
        self.assertEqual(chapters[0].title, "The House-Elf Liberation Front")

    def test_extract_chapters_does_not_consume_dashed_inline_title_as_chapter_number(self):
        source = """
        CHAPTER THIRTEEN - MAD-EYE MOODY
        First paragraph starts here.
        """

        chapters = reader_pipeline.extract_chapters(
            source,
            chapter_titles={13: "Mad-Eye Moody"},
            chapter_count=13,
        )

        self.assertEqual([chapter.number for chapter in chapters], [13])
        self.assertEqual(chapters[0].title, "Mad-Eye Moody")

    def test_extract_chapters_trims_series_back_matter_from_final_chapter(self):
        source = """
        CHAPTER ONE

        One

        First book text.

        CHAPTER TWO

        Two

        Final book text.

        Titles available in the Example Series

        Read on for the first chapter of the next book in the series...

        CHAPTER ONE

        Preview text from the next book.
        """

        chapters = reader_pipeline.extract_chapters(
            source,
            chapter_titles={1: "One", 2: "Two"},
            chapter_count=2,
        )

        self.assertEqual(chapters[1].body.strip(), "Final book text.")

    def test_audio_chapter_spans_from_metadata_split_multi_chapter_parts(self):
        metadata = {
            "title": "Example Book",
            "spine": [{"duration": 100.0}, {"duration": 200.0}],
            "chapters": [
                {"title": "Chapter 1:  One", "spine": 0, "offset": 0},
                {"title": "Chapter 2:  Two", "spine": 0, "offset": 40},
                {"title": "Chapter 3:  Three", "spine": 1, "offset": 0},
                {"title": "Next Chapter:  Preview", "spine": 1, "offset": 180},
            ],
        }

        config = reader_pipeline.book_config_from_metadata(metadata)
        spans = reader_pipeline.audio_chapter_spans_from_metadata(metadata)

        self.assertEqual(config.title, "Example Book")
        self.assertEqual(config.chapter_titles, {1: "One", 2: "Two", 3: "Three"})
        self.assertEqual(len(spans), 3)
        self.assertEqual(spans[0].spine_index, 0)
        self.assertEqual(spans[0].start, 0.0)
        self.assertEqual(spans[0].end, 40.0)
        self.assertEqual(spans[1].start, 40.0)
        self.assertEqual(spans[1].end, 100.0)
        self.assertEqual(spans[2].spine_index, 1)
        self.assertEqual(spans[2].end, 180.0)

    def test_audio_parts_accepts_numbered_audiobook_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            second = root / "002 - Example.mp3"
            first = root / "001 - Example.mp3"
            tenth = root / "010 - Example.mp3"
            for path in [second, tenth, first]:
                path.write_text("", encoding="utf-8")

            self.assertEqual(reader_pipeline.audio_parts(root), [first, second, tenth])

    def test_align_all_uses_chapter_audio_when_extra_outro_part_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_dir = root / "audio"
            output_dir = root / "reader"
            text_dir = output_dir / "text"
            audio_dir.mkdir()
            text_dir.mkdir(parents=True)
            first = audio_dir / "001 - Example.mp3"
            second = audio_dir / "002 - Example.mp3"
            outro = audio_dir / "003 - Example.mp3"
            for path in [first, second, outro]:
                path.write_text("", encoding="utf-8")
            for index in [1, 2]:
                (text_dir / f"chapter_{index:03d}.txt").write_text(f"chapter {index}", encoding="utf-8")

            config = reader_pipeline.BookConfig(
                title="Example",
                chapter_titles={1: "One", 2: "Two"},
                chapter_count=2,
            )

            with (
                patch.object(reader_pipeline, "run_aeneas") as run_aeneas,
                patch.object(reader_pipeline, "validate_alignment_file") as validate_alignment_file,
                patch.object(reader_pipeline, "ffprobe_duration", return_value=10.0),
                patch.object(reader_pipeline, "concatenate_audio_parts") as concatenate_audio_parts,
            ):
                reader_pipeline.align_all(audio_dir, output_dir, config)

            concatenate_audio_parts.assert_not_called()
            run_aeneas.assert_has_calls(
                [
                    call(first, text_dir / "chapter_001.txt", output_dir / "alignments" / "chapter_001.json", force=False),
                    call(second, text_dir / "chapter_002.txt", output_dir / "alignments" / "chapter_002.json", force=False),
                ]
            )
            self.assertEqual(run_aeneas.call_count, 2)
            self.assertEqual(validate_alignment_file.call_count, 2)

    def test_build_reader_uses_extra_audio_part_as_outro(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_dir = root / "audio"
            output_dir = root / "reader"
            audio_dir.mkdir()
            (output_dir / "alignments").mkdir(parents=True)
            (output_dir / "book.txt").write_text("raw text", encoding="utf-8")
            first = audio_dir / "001 - Example.mp3"
            second = audio_dir / "002 - Example.mp3"
            outro = audio_dir / "003 - Example.mp3"
            for path in [first, second, outro]:
                path.write_text("", encoding="utf-8")

            chapters = [
                reader_pipeline.Chapter(number=1, title="One", body="First"),
                reader_pipeline.Chapter(number=2, title="Two", body="Second"),
            ]
            config = reader_pipeline.BookConfig(
                title="Example",
                chapter_titles={1: "One", 2: "Two"},
                chapter_count=2,
            )
            manifest = {"title": "Example", "duration": 30.0, "chapters": []}
            durations_by_path = {first: 10.0, second: 20.0, outro: 3.0}

            with (
                patch.object(reader_pipeline, "extract_chapters", return_value=chapters),
                patch.object(reader_pipeline, "validate_chapters"),
                patch.object(reader_pipeline, "ffprobe_duration", side_effect=lambda path: durations_by_path[path]),
                patch.object(reader_pipeline, "build_reader_manifest", return_value=manifest) as build_manifest,
                patch.object(reader_pipeline, "build_reader_html", return_value="<html></html>"),
            ):
                reader_pipeline.build_reader(output_dir, audio_dir, config)

            _, kwargs = build_manifest.call_args
            self.assertEqual(kwargs["audio_files"], [reader_pipeline.relative_to_output(first, output_dir), reader_pipeline.relative_to_output(second, output_dir)])
            self.assertEqual(kwargs["durations"], [10.0, 20.0])
            self.assertEqual(kwargs["outro_audio"], reader_pipeline.relative_to_output(outro, output_dir))
            self.assertEqual(kwargs["outro_duration"], 3.0)

    def test_build_reader_manifest_from_single_alignment_splits_chapters_by_fragment_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            alignment_path = root / "book.json"
            alignment_path.write_text(
                json.dumps(
                    {
                        "fragments": [
                            {"id": "f000001", "begin": "0.000", "end": "1.000", "lines": ["Chapter One. One."]},
                            {"id": "f000002", "begin": "1.000", "end": "3.000", "lines": ["First"]},
                            {"id": "f000003", "begin": "3.000", "end": "4.000", "lines": ["Chapter Two. Two."]},
                            {"id": "f000004", "begin": "4.000", "end": "5.000", "lines": ["Second"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            chapters = [
                reader_pipeline.Chapter(1, "One", "First"),
                reader_pipeline.Chapter(2, "Two", "Second"),
            ]

            manifest = reader_pipeline.build_reader_manifest_from_single_alignment(
                chapters=chapters,
                audio_file=Path("audio/book.mp3"),
                alignment_path=alignment_path,
                duration=6.0,
                title="Example Book",
            )

        self.assertEqual(manifest["title"], "Example Book")
        self.assertEqual(manifest["duration"], 6.0)
        self.assertEqual([chapter["audio"] for chapter in manifest["chapters"]], ["audio/book.mp3", "audio/book.mp3"])
        self.assertEqual(manifest["chapters"][0]["start"], 0.0)
        self.assertEqual(manifest["chapters"][0]["end"], 3.0)
        self.assertEqual(manifest["chapters"][1]["start"], 3.0)
        self.assertEqual(manifest["chapters"][1]["end"], 6.0)
        self.assertEqual(manifest["chapters"][1]["audioStart"], 3.0)
        self.assertEqual(manifest["chapters"][1]["paragraphs"][0]["localBegin"], 3.0)

    def test_build_reader_manifest_offsets_chapter_fragments_and_appends_outro(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            align_dir = root / "alignments"
            align_dir.mkdir()
            (align_dir / "chapter_001.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {"id": "f000001", "begin": "0.000", "end": "1.500", "lines": ["First"]},
                            {"id": "f000002", "begin": "1.500", "end": "3.000", "lines": ["Second"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (align_dir / "chapter_002.json").write_text(
                json.dumps(
                    {
                        "fragments": [
                            {"id": "f000001", "begin": "0.000", "end": "2.000", "lines": ["Third"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            chapters = [
                reader_pipeline.Chapter(1, "One", "First\n\nSecond"),
                reader_pipeline.Chapter(2, "Two", "Third"),
            ]
            audio_files = [Path("Part 001.mp3"), Path("Part 002.mp3")]

            manifest = reader_pipeline.build_reader_manifest(
                chapters=chapters,
                audio_files=audio_files,
                alignment_dir=align_dir,
                durations=[3.0, 2.0],
                title="Example Book",
                outro_audio=Path("Part 039.mp3"),
                outro_duration=100.0,
            )

        self.assertEqual(manifest["title"], "Example Book")
        self.assertEqual(len(manifest["chapters"]), 3)
        self.assertEqual(manifest["chapters"][1]["start"], 3.0)
        self.assertEqual(manifest["chapters"][1]["paragraphs"][0]["begin"], 3.0)
        self.assertEqual(manifest["chapters"][2]["kind"], "outro")
        self.assertEqual(manifest["duration"], 105.0)


if __name__ == "__main__":
    unittest.main()

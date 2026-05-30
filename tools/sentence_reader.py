from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from tools.reader_pipeline import build_reader_html, ffprobe_duration


DEFAULT_TITLE = "EA Diploma Interview Preparation Guide"


def clean_markdown_for_speech(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"\[\d+\]:\s+\S+.*", line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^\s*[-*]\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)
        line = re.sub(r"\(\[([^\]]+)\]\[\d+\]\)", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\[\d+\]", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"^[A-Z][A-Za-z ]+:\s*$", lambda match: match.group(0), line)
        lines.append(line)
    return "\n".join(lines)


def split_sentences(text: str) -> list[str]:
    fragments: list[str] = []
    for block in text.splitlines():
        block = re.sub(r"\s+", " ", block).strip()
        if not block:
            continue
        if looks_like_standalone_fragment(block):
            fragments.append(block)
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=(?:[\"']?[A-Z0-9]|I\b))", block)
        fragments.extend(part.strip() for part in parts if part.strip())
    return fragments


def looks_like_standalone_fragment(text: str) -> bool:
    if len(text) <= 80 and re.match(r"^(?:\d+\.\s+)?[A-Z]", text) and not text.endswith((".", "?", "!")):
        return True
    return False


def reset_output_dirs(output_dir: Path) -> None:
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


def convert_audio(source: Path, target: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "96k",
            str(target),
        ],
        check=True,
    )


def run_aeneas(audio_path: Path, text_path: Path, output_path: Path) -> None:
    config = "task_language=eng|is_text_type=plain|os_task_file_format=json"
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
    )


def build_manifest(title: str, audio_path: Path, alignment_path: Path, output_dir: Path) -> dict:
    duration = round(ffprobe_duration(audio_path), 3)
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    paragraphs = []
    for index, fragment in enumerate(alignment.get("fragments", []), start=1):
        begin = round(float(fragment["begin"]), 3)
        end = round(float(fragment["end"]), 3)
        paragraphs.append(
            {
                "id": f"c001_f{index:06d}",
                "text": " ".join(fragment.get("lines", [])).strip(),
                "begin": begin,
                "end": end,
                "localBegin": begin,
                "localEnd": end,
            }
        )
    return {
        "title": title,
        "duration": duration,
        "chapters": [
            {
                "kind": "chapter",
                "number": 1,
                "title": title,
                "audio": audio_path.relative_to(output_dir).as_posix(),
                "start": 0.0,
                "end": duration,
                "duration": duration,
                "paragraphs": paragraphs,
            }
        ],
    }


def generate_reader(text_path: Path, audio_source: Path, output_dir: Path, title: str) -> None:
    reset_output_dirs(output_dir)
    cleaned_text = clean_markdown_for_speech(text_path.read_text(encoding="utf-8"))
    sentences = split_sentences(cleaned_text)
    if not sentences:
        raise RuntimeError(f"No sentence fragments found in {text_path}")

    sentence_text_path = output_dir / "text" / "chapter_001.txt"
    full_text_path = output_dir / "text" / "book.txt"
    root_text_path = output_dir / "book.txt"
    sentence_body = "\n".join(sentences) + "\n"
    sentence_text_path.write_text(sentence_body, encoding="utf-8")
    full_text_path.write_text(sentence_body, encoding="utf-8")
    root_text_path.write_text(sentence_body, encoding="utf-8")

    audio_target = output_dir / "audio" / "chapter_001.mp3"
    convert_audio(audio_source, audio_target)

    alignment_path = output_dir / "alignments" / "chapter_001.json"
    run_aeneas(audio_target, sentence_text_path, alignment_path)

    manifest = build_manifest(title, audio_target, alignment_path, output_dir)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(build_reader_html(manifest), encoding="utf-8")
    print(f"sentences: {len(sentences)}")
    print(f"reader ready: {output_dir / 'index.html'}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a sentence-aligned reader from one text file and one audio file.")
    parser.add_argument("--text", type=Path, default=Path("audio.txt"))
    parser.add_argument("--audio", type=Path, default=Path("chatgpt_tts_1779987450838.aac"))
    parser.add_argument("--output-dir", type=Path, default=Path("aligned_reader"))
    parser.add_argument("--title", default=DEFAULT_TITLE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generate_reader(args.text, args.audio, args.output_dir, args.title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

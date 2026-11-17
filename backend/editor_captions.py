import math
import os
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import ImageFont

from .editor_layout import CAPTION_BAND, HEIGHT, TITLE_BAND, WEBCAM_CAPTION_BAND, WEBCAM_TITLE_BAND, WIDTH
from .highlights import AUDIO_RATE, whisper
from .media import MediaError, read_media

CONTEXT = 2.0
MIN_PAUSE = 0.08


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    probability: float
    uncertain: bool


def recognize_words(path, start, duration, check, language):
    data = read_media(path, start, duration, [
        "-map", "0:a:0", "-vn", "-af", "aresample=async=1:first_pts=0",
        "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le",
    ], check=check)
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
    if not len(samples) or np.max(np.abs(samples)) < 0.0005:
        return []
    try:
        segments, _ = whisper().transcribe(
            samples, word_timestamps=True, beam_size=5, vad_filter=True,
            language=None if language == "auto" else language,
            condition_on_previous_text=False, vad_parameters={"min_silence_duration_ms": 400},
        )
        result = []
        for segment in segments:
            check()
            doubtful = segment.avg_logprob < -0.85 or segment.no_speech_prob > 0.60
            for word in segment.words or []:
                term = safe_text(word.word)
                if term and 0 <= word.start < word.end <= len(samples) / AUDIO_RATE + 0.1:
                    result.append(Word(start + word.start, start + word.end, term,
                                       word.probability, doubtful or word.probability < 0.55))
        return sorted(result, key=lambda word: word.start)
    except (RuntimeError, ValueError) as exc:
        raise MediaError("Не удалось получить таймкоды слов для субтитров.") from exc


def resolve_boundary(boundary, words, left, right, *, intro):
    crossing = [word for word in words if word.start + 0.015 < boundary < word.end - 0.015]
    if not crossing:
        return boundary
    gaps = []
    for a, b in zip(words, words[1:]):
        gap_start, gap_end = max(left, a.end), min(right, b.start)
        if gap_end - gap_start >= MIN_PAUSE:
            closest = min(max(boundary, gap_start + 0.02), gap_end - 0.02)
            gaps.append(closest)
    nearby = [point for point in gaps if abs(point - boundary) <= CONTEXT - 0.1]
    # Preserve the whole intersected word when Whisper reports continuous speech.
    fallback = min(word.start for word in crossing) if intro else max(word.end for word in crossing)
    return min(nearby, key=lambda point: abs(point - boundary)) if nearby else min(max(fallback, left), right)


def prepare_speech(path, start, end, source_duration, check, language):
    left, right = max(0, start - CONTEXT), min(source_duration, end + CONTEXT)
    words = recognize_words(path, left, right - left, check, language)
    new_start = resolve_boundary(start, words, left, min(right, end - 1), intro=True)
    new_end = resolve_boundary(end, words, max(left, new_start + 1), right, intro=False)
    if new_end <= new_start + 1:
        return start, end, words
    return new_start, new_end, words


def safe_text(text):
    return " ".join(text.split()).translate(str.maketrans({"{": "｛", "}": "｝", "\\": "＼"}))


def ass_color(css):
    return "&H00" + css[5:7] + css[3:5] + css[1:3]


def ass_time(seconds):
    value = max(0, round(seconds * 100))
    return f"{value // 360000}:{value // 6000 % 60:02d}:{value // 100 % 60:02d}.{value % 100:02d}"


@lru_cache(maxsize=1)
def font_files():
    root = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts"
    pairs = [
        (root / "arial.ttf", root / "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for regular, bold in pairs:
        if regular.is_file() and bold.is_file():
            return regular, bold, ImageFont.truetype(str(regular), 48).getname()[0]
    raise MediaError("Для ASS нужен локальный шрифт Arial или DejaVu Sans с кириллицей.")


@lru_cache(maxsize=1)
def caption_fonts():
    root = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts"
    regular, bold = root / "segoeui.ttf", root / "segoeuib.ttf"
    if regular.is_file() and bold.is_file():
        return regular, bold, "Segoe UI"
    return font_files()


@lru_cache(maxsize=32)
def load_font(path, size):
    return ImageFont.truetype(str(path), size)


def lines_for(tokens, font, width):
    lines = []
    for token in tokens:
        if lines and font.getlength(lines[-1] + " " + token) <= width:
            lines[-1] += " " + token
        else:
            lines.append(token)
    return lines


def trim_to_width(text, font, width, *, ellipsis=False):
    suffix = "…" if ellipsis else ""
    while text and font.getlength(text.rstrip() + suffix) > width:
        text = text[:-1]
    return text.rstrip() + suffix


def title_lines(title, font_path, size, width):
    tokens = safe_text(title).split()
    while size >= 36:
        font = load_font(font_path, size)
        lines = lines_for(tokens, font, width)
        if len(lines) <= 2 and all(font.getlength(line) <= width for line in lines):
            return size, lines
        size -= 2
    size = 36
    font = load_font(font_path, size)
    lines = lines_for(tokens, font, width)
    limited = [trim_to_width(line, font, width, ellipsis=(i == 1 and len(lines) > 2 or font.getlength(line) > width))
               for i, line in enumerate(lines[:2])]
    return size, limited


def word_groups(words, font, width, count):
    group = []
    for word in words:
        if group and (
            len(group) >= count or word.start - group[-1].end >= 0.45
            or word.end - group[0].start > 4.2
            or re.search(r"[.!?…;]$", group[-1].text)
            or len(lines_for([p.text.upper() for p in group] + [word.text.upper()], font, width)) > 3
        ):
            yield group
            group = []
        group.append(word)
    if group:
        yield group


def write_overlays(workdir, words, source_start, options, title, lo, hi, *, webcam=False):
    if not title and not words:
        return []
    regular, bold, family = font_files()
    caption_regular, caption_bold, caption_family = caption_fonts()
    fonts_dir = workdir / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    for font in (regular, bold, caption_regular, caption_bold):
        target = fonts_dir / font.name
        if not target.exists():
            shutil.copyfile(font, target)

    title_left, title_top, title_right, title_bottom = WEBCAM_TITLE_BAND if webcam else TITLE_BAND
    cap_left, cap_top, cap_right, cap_bottom = WEBCAM_CAPTION_BAND if webcam else CAPTION_BAND
    title_width = title_right - title_left - 24
    cap_x = WIDTH // 2
    title_size, headings = title_lines(title, bold, min(options.title_size, 76), title_width)
    cap_size = options.caption_size
    cap_width = min(max(440, round(cap_size * 7.8)), 620, 2 * min(cap_x - cap_left, cap_right - cap_x) - 24)
    accent_size = round(cap_size * 1.1)
    cap_font = load_font(caption_bold, accent_size)
    title_y = min(max(round(HEIGHT * options.title_y / 100), title_top), title_bottom - math.ceil(title_size * 2.3))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,{caption_family},{cap_size},{ass_color(options.caption_color)},&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,2.2,1.2,5,96,192,50,1
Style: Accent,{caption_family},{accent_size},{ass_color(options.highlight_color)},&H00FFFFFF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,2.5,1.2,5,96,192,50,1
Style: Title,{family},{title_size},{ass_color(options.title_color)},&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,2.8,0,8,96,192,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events, cues = [], []

    def event(layer, begin, end, style, text, x, y, alignment):
        if end <= begin or ass_time(begin) == ass_time(end):
            return False
        at = rf"{{\an{alignment}\pos({x},{y})\q2}}"
        events.append(f"Dialogue: {layer},{ass_time(begin)},{ass_time(end)},{style},,0,0,0,," + at + r"\N".join(text))
        return True

    if headings:
        event(1, 0, min(3, options.title_seconds, hi), "Title", headings, (title_left + title_right) // 2, title_y, 8)

    source_hi = source_start + hi
    usable = [word for word in words if word.end > source_start + lo and word.start < source_hi]
    groups = list(word_groups(usable, cap_font, cap_width, min(12, options.words_per_line * 2)))
    for index, group in enumerate(groups):
        begin = max(lo, group[0].start - source_start)
        tail = min(hi, groups[index + 1][0].start - source_start) if index + 1 < len(groups) else hi
        end = min(tail, max(group[-1].end - source_start, begin + 0.40))
        doubtful = any(word.uncertain for word in group)
        lines = lines_for([word.text.upper() for word in group], cap_font, cap_width)
        accent = min(1, len(lines) - 1)
        row_heights = [accent_size if i == accent else cap_size for i in range(len(lines))]
        total_height = sum(row_heights)
        bottom = min(cap_bottom, max(cap_top + total_height, round(HEIGHT * options.caption_y / 100)))
        top = bottom - total_height
        shown = False
        for i, (line, height) in enumerate(zip(lines, row_heights)):
            line_width = load_font(caption_bold if i == accent else caption_regular, height).getlength(line)
            if line_width > cap_width:
                line = rf"{{\fs{math.floor(height * cap_width / line_width)}}}" + line
            shown |= event(0, begin, end, "Accent" if i == accent else "Captions",
                           [line], cap_x, top + height // 2, 5)
            top += height
        if shown:
            cues.append({"start": round(begin, 3), "end": round(end, 3), "text": " ".join(word.text for word in group),
                         "review_audio": doubtful, "min_word_probability": round(min(word.probability for word in group), 3)})
    if events:
        (workdir / "overlays.ass").write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return cues

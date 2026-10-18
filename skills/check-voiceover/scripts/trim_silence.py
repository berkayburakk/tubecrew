#!/usr/bin/env python3
"""trim_silence.py - shorten long pauses in a voiceover without touching the speech.

What it does
    Finds pauses with ffmpeg's silencedetect and caps every pause longer than a ceiling by cutting out its middle,
    so half the ceiling stays on each side of the cut. Speech is never stretched or sped up; only samples inside
    silence are removed. --measure only reports: silence share, number and lengths of the pauses, and the seconds
    a trim would save.

Why
    Generated voices often speak at a good pace but wait too long between sentences and paragraphs, which makes a
    video drag. Speeding the whole file up fixes the length but sounds artificial; removing the extra silence keeps
    the voice natural. Cutting in the middle of a pause puts both cut points in near-silence, so the join needs no
    crossfade, and the fading tail of the last word before the pause stays whole.

How to run
    python trim_silence.py voice/narration.wav --measure
    python trim_silence.py voice/narration.wav                       writes voice/narration-trimmed.wav
    python trim_silence.py voice/narration.wav voice/tight.wav --ceiling 0.5 --ratio 0.35

    Trim before the final transcription and alignment: every timing after the first cut moves. The output is a
    new file; an existing file is never overwritten. WAV in, WAV out keeps the samples bit for bit; other formats
    are re-encoded.

    A hard ceiling makes every long pause the same length. If the result never seems to breathe, raise the
    ceiling (0.5-0.7) and pass --ratio 0.35: pauses keep only that share of their length above --knee, so a long
    pause stays longer than a short one, and the ceiling still caps them.

Exit codes
    0  trimmed (or measured)     2  usage or environment error (ffmpeg missing, bad file, output exists)
    This is a tool, not a gate, so it never exits 1.

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = "trim_silence.py"
VERSION = "0.1.0"

SILENCE_DB = -40.0          # quieter than this is a pause: far under speech, above the noise floor of a clean file
MIN_PAUSE_S = 0.30          # shorter gaps are the breaths inside a sentence and are never touched
CEILING_S = 0.35            # longest pause kept; 0.35-0.50 s keeps sentence breaks audible without dead air
KNEE_S = 0.20               # with --ratio below 1, pauses up to this length keep their full length
TAIL_KEEP_S = 0.12          # each side of a cut keeps at least this much, so a word's fading tail is not clipped
FRAME_SAMPLES = 64          # cut granularity in samples (1.3-2.7 ms); smaller frames are slower to filter
MIN_CUT_S = 0.005           # a planned cut shorter than this is not worth making
SELECT_TERMS_MAX = 80       # cuts per aselect filter; ffmpeg's expression parser refuses sums of 100+ terms
INLINE_FILTER_MAX = 20000   # a longer filter goes through a file, because command lines have a length limit
LENGTH_TOLERANCE_S = 0.15   # the output may differ from the plan by encoder padding, not more
TIMEOUT_S = 1800            # one ffmpeg pass; a long narration decodes in well under a minute
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


class UsageError(Exception):
    """Raised for exit-code-2 problems."""


# ----------------------------------------------------------------------------------------------- pure planning

def parse_silences(stderr: str, duration: float) -> list[tuple[float, float]]:
    """silencedetect output -> [(start, end)]; a silence still open at the end of the file runs to `duration`."""
    pauses: list[tuple[float, float]] = []
    start = None
    for line in stderr.splitlines():
        if "silence_start:" in line:
            start = max(0.0, float(line.split("silence_start:")[1].split()[0]))
        elif "silence_end:" in line and start is not None:
            pauses.append((start, float(line.split("silence_end:")[1].split()[0])))
            start = None
    if start is not None and duration > start:
        pauses.append((start, duration))
    return pauses


def new_length(old: float, ceiling: float, knee: float = KNEE_S, ratio: float = 1.0) -> float:
    """Length a pause keeps. ratio 1 is a hard ceiling (min(old, ceiling)); a smaller ratio also shrinks the part
    above the knee, so long pauses stay longer than short ones before the ceiling caps them."""
    if old <= knee:
        return old
    return min(old, ceiling, knee + (old - knee) * ratio)


def plan_cuts(pauses: list[tuple[float, float]], ceiling: float, knee: float = KNEE_S,
              ratio: float = 1.0) -> list[tuple[float, float]]:
    """Pauses -> the stretches to remove, each taken from the middle of its pause (half the kept length stays on
    each side)."""
    cuts = []
    for start, end in pauses:
        keep = new_length(end - start, ceiling, knee, ratio)
        if (end - start) - keep < MIN_CUT_S:
            continue
        cuts.append((round(start + keep / 2.0, 6), round(end - keep / 2.0, 6)))
    return cuts


def frame_ranges(cuts: list[tuple[float, float]], sample_rate: int, offset: float = 0.0,
                 frame: int = FRAME_SAMPLES) -> list[tuple[int, int]]:
    """Cut times -> inclusive ranges of whole frames inside each cut (a frame on an edge of a cut is kept)."""
    out = []
    for a, b in cuts:
        first = math.ceil((a - offset) * sample_rate / frame - 1e-9)
        last = math.floor((b - offset) * sample_rate / frame + 1e-9) - 1
        if last >= first:
            out.append((first, last))
    return out


def filter_graph(ranges: list[tuple[int, int]], sample_rate: int, offset: float = 0.0,
                 frame: int = FRAME_SAMPLES) -> str:
    """aselect drops the listed frames; asetpts closes the gaps so the output plays continuously.

    Each frame is matched by its start time with half a frame of margin on both sides, so rounding never moves a
    boundary. ffmpeg's expression parser refuses a sum of 100 or more terms, so the ranges are split over several
    aselect filters; they test time (t), which an earlier aselect does not renumber the way it renumbers n."""
    step = frame / float(sample_rate)
    parts = [f"asetnsamples=n={frame}:p=0"]
    for i in range(0, len(ranges), SELECT_TERMS_MAX):
        drop = "+".join(f"between(t,{offset + (a - 0.5) * step:.7f},{offset + (b + 0.5) * step:.7f})"
                        for a, b in ranges[i:i + SELECT_TERMS_MAX])
        parts.append(f"aselect='not({drop})'")
    parts.append("asetpts=N/SR/TB")
    return ",".join(parts)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def pause_stats(pauses: list[tuple[float, float]], duration: float) -> dict:
    lengths = [end - start for start, end in pauses]
    total = sum(lengths)
    return {"count": len(lengths), "total": round(total, 2),
            "share": round(total / duration, 4) if duration else 0.0,
            "median": round(_percentile(lengths, 0.5), 2), "p90": round(_percentile(lengths, 0.9), 2),
            "max": round(max(lengths), 2) if lengths else 0.0}


# ----------------------------------------------------------------------------------------------- ffmpeg

def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def probe(path: Path) -> dict:
    """Duration, start time, sample rate, channels and codec of the first audio stream."""
    code, out, err = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                           "stream=codec_name,sample_rate,channels:format=duration,start_time", "-of", "json",
                           str(path)])
    if code != 0:
        raise UsageError(f"ffprobe cannot read {path.name}: {err.strip()[-200:]}")
    data = json.loads(out or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise UsageError(f"{path.name} has no audio stream")
    fmt = data.get("format") or {}
    try:
        return {"duration": float(fmt.get("duration") or 0.0), "start_time": float(fmt.get("start_time") or 0.0),
                "sample_rate": int(streams[0].get("sample_rate") or 0), "channels": streams[0].get("channels"),
                "codec": streams[0].get("codec_name") or ""}
    except ValueError as exc:
        raise UsageError(f"ffprobe gave unreadable values for {path.name}: {exc}") from exc


def detect_pauses(path: Path, silence_db: float, min_pause: float, duration: float) -> list[tuple[float, float]]:
    code, _out, err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                            f"silencedetect=noise={silence_db:g}dB:d={min_pause:g}", "-f", "null", "-"])
    if code != 0:
        last = err.strip().splitlines()[-1] if err.strip() else f"exit code {code}"
        raise UsageError(f"ffmpeg could not decode {path.name}: {last}")
    return parse_silences(err, duration)


def codec_args(out_path: Path, input_codec: str) -> list[str]:
    ext = out_path.suffix.lower()
    if ext == ".wav":
        return ["-c:a", input_codec if input_codec.startswith("pcm_") else "pcm_s16le"]
    if ext == ".flac":
        return ["-c:a", "flac"]
    return ["-b:a", "192k"]


def write_trimmed(src: Path, dst: Path, graph: str, input_codec: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(src), "-vn", "-map", "0:a:0"]
    script = None
    try:
        if len(graph) <= INLINE_FILTER_MAX:
            cmd += ["-af", graph]
        else:
            handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
            with handle:
                handle.write(graph)
            script = Path(handle.name)
            cmd += ["-filter_script:a", str(script)]
        cmd += codec_args(dst, input_codec) + [str(dst)]
        code, _out, err = _run(cmd)
        if code != 0 and script is not None and "filter_script" in err:
            cmd[cmd.index("-filter_script:a")] = "-/af"   # newer ffmpeg reads option values from a file this way
            code, _out, err = _run(cmd)
        if code != 0:
            dst.unlink(missing_ok=True)   # only ever a file this run created: main() refuses an existing output
            raise UsageError(f"ffmpeg could not write {dst.name}: {err.strip()[-300:]}")
    finally:
        if script is not None:
            script.unlink(missing_ok=True)


# ----------------------------------------------------------------------------------------------- main

def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def default_output(src: Path) -> Path:
    ext = src.suffix if src.suffix.lower() in AUDIO_EXTENSIONS else ".wav"
    return src.with_name(f"{src.stem}-trimmed{ext}")


def human_lines(r: dict) -> list[str]:
    p = r["pauses"]
    lines = [f"trim_silence: {r['input']} ({r['duration']:.1f} s)",
             f"Pauses of {r['min_pause']:g} s or more below {r['silence_db']:g} dB: {p['count']}, "
             f"{p['total']:.1f} s in total = {p['share'] * 100:.1f}% of the file"]
    if p["count"]:
        lines.append(f"  median {p['median']:.2f} s, 90th percentile {p['p90']:.2f} s, longest {p['max']:.2f} s")
    rule = f"capping at {r['ceiling']:g} s" + (f" with ratio {r['ratio']:g} above {r['knee']:g} s"
                                                if r["ratio"] < 1 else "")
    cut = f"{r['cut_pauses']} pause" + ("" if r["cut_pauses"] == 1 else "s")
    if r["measure_only"]:
        lines.append(f"{rule[0].upper() + rule[1:]} would cut {cut} and save "
                     f"{r['saved_seconds']:.2f} s ({r['duration']:.1f} s -> {r['new_duration']:.1f} s)")
        lines.append("(--measure: nothing written)")
    elif r["output"] is None:
        lines.append(f"Nothing to trim when {rule}: no file written.")
    else:
        lines.append(f"Wrote {r['output']}: {r['output_duration']:.1f} s (saved {r['saved_seconds']:.2f} s, "
                     f"{cut} cut, {rule})")
        lines.append("Next: transcribe and align the trimmed file; every timing after the first cut moved.")
    return lines


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Cap long pauses in a voiceover by cutting their middle; speech is never "
                               "stretched. Exit 0 done, 2 usage or environment error.")
    parser.add_argument("input", help="the narration audio (wav, mp3, m4a, flac, ogg, opus, aac)")
    parser.add_argument("output", nargs="?", help="new file to write (default <name>-trimmed.<ext>; never overwritten)")
    parser.add_argument("--ceiling", type=float, default=CEILING_S,
                        help=f"longest pause kept, seconds (default {CEILING_S:g})")
    parser.add_argument("--silence-db", type=float, default=SILENCE_DB,
                        help=f"quieter than this counts as a pause (default {SILENCE_DB:g} dB)")
    parser.add_argument("--min-pause", type=float, default=MIN_PAUSE_S,
                        help=f"shorter silences are never touched (default {MIN_PAUSE_S:g} s)")
    parser.add_argument("--ratio", type=float, default=1.0,
                        help="share of a pause's length above --knee that is kept; 1 = hard ceiling (default 1)")
    parser.add_argument("--knee", type=float, default=KNEE_S,
                        help=f"with --ratio below 1, pauses up to this length are kept whole (default {KNEE_S:g} s)")
    parser.add_argument("--measure", action="store_true", help="only report what a trim would do; write nothing")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    src = Path(args.input)
    try:
        if args.ceiling < 2 * TAIL_KEEP_S:
            raise UsageError(f"--ceiling {args.ceiling:g} would leave less than {TAIL_KEEP_S:g} s after a word and "
                             f"clip its tail; use {2 * TAIL_KEEP_S:g} or more")
        if not 0 < args.ratio <= 1:
            raise UsageError("--ratio must be above 0 and at most 1")
        if args.min_pause <= 0 or args.knee < 0:
            raise UsageError("--min-pause must be above 0 and --knee 0 or more")
        missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
        if missing:
            raise UsageError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (https://ffmpeg.org) first")
        if not src.is_file():
            raise UsageError(f"audio not found: {args.input}")
        dst = Path(args.output) if args.output else default_output(src)
        if not args.measure:
            if dst.suffix.lower() not in AUDIO_EXTENSIONS:
                raise UsageError(f"output must be an audio file ({', '.join(sorted(AUDIO_EXTENSIONS))})")
            if dst.exists():
                raise UsageError(f"{dst.name} already exists; this tool never overwrites, so pick another name")
        info = probe(src)
        if not info["sample_rate"] or not info["duration"]:
            raise UsageError(f"cannot tell the sample rate and length of {src.name}")
        pauses = detect_pauses(src, args.silence_db, args.min_pause, info["duration"])
        cuts = plan_cuts(pauses, args.ceiling, args.knee, args.ratio)
        ranges = frame_ranges(cuts, info["sample_rate"], info["start_time"])
        removed = sum(b - a + 1 for a, b in ranges) * FRAME_SAMPLES / info["sample_rate"]
        result = {
            "tool": TOOL, "version": VERSION, "input": src.name, "duration": round(info["duration"], 3),
            "sample_rate": info["sample_rate"], "silence_db": args.silence_db, "min_pause": args.min_pause,
            "ceiling": args.ceiling, "ratio": args.ratio, "knee": args.knee,
            "pauses": pause_stats(pauses, info["duration"]), "cut_pauses": len(ranges),
            "saved_seconds": round(removed, 3), "new_duration": round(info["duration"] - removed, 3),
            "measure_only": bool(args.measure), "output": None, "output_duration": None,
            "cuts": [[round(a, 3), round(b, 3)] for a, b in cuts],
        }
        if not args.measure and ranges:
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_trimmed(src, dst, filter_graph(ranges, info["sample_rate"], info["start_time"]), info["codec"])
            written = probe(dst)["duration"]
            if abs(written - result["new_duration"]) > LENGTH_TOLERANCE_S:
                dst.unlink(missing_ok=True)
                raise UsageError(f"the trimmed file came out {written:.2f} s long instead of the planned "
                                 f"{result['new_duration']:.2f} s, so it was removed; check the input with --measure")
            result.update(output=dst.name, output_duration=round(written, 3))
    except UsageError as exc:
        print(f"trim_silence: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("trim_silence: error: ffmpeg timed out", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

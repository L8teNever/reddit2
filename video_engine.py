"""
SARA Video Engine — Standalone Logic
Zuständig für: TTS, Wort-Timing (Whisper), ASS-Untertitel, Hintergrund-Loops und Rendering.
"""

import os
import re
import json
import uuid
import random
import shutil
import string
import subprocess
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Pfad-Konfiguration (Anpassen falls nötig)
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).parent
DATA_DIR        = BASE_DIR / "data"
BACKGROUNDS_DIR = DATA_DIR / "backgrounds"
OUTPUTS_DIR     = DATA_DIR / "outputs"
TTS_DIR         = DATA_DIR / "tts"
COVERS_DIR      = DATA_DIR / "covers"

VIDEO_WIDTH              = 1080
VIDEO_HEIGHT             = 1920
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "avi"}

# ---------------------------------------------------------------------------
# Subprozess-Management
# ---------------------------------------------------------------------------
_ACTIVE_SUBPROCESSES = []

def _run_sub(args, **kwargs) -> subprocess.CompletedProcess:
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    check = kwargs.pop("check", False)
    p = subprocess.Popen(args, **kwargs)
    _ACTIVE_SUBPROCESSES.append(p)
    try:
        out, err = p.communicate()
        if check and p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, args, out, err)
        return subprocess.CompletedProcess(args, p.returncode, out, err)
    finally:
        if p in _ACTIVE_SUBPROCESSES:
            _ACTIVE_SUBPROCESSES.remove(p)

# ---------------------------------------------------------------------------
# Werkzeug-Erkennung (FFmpeg & Fonts)
# ---------------------------------------------------------------------------
def _find_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if ff: return ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except: return "ffmpeg"

def _find_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for c in candidates:
        if Path(c).exists(): return c
    return ""

FFMPEG_EXE    = _find_ffmpeg()
DRAWTEXT_FONT = _find_font()

# ---------------------------------------------------------------------------
# TTS — Kokoro-82M (Lokal & Offline)
# ---------------------------------------------------------------------------
def _ensure_kokoro_model():
    import urllib.request
    model_dir = DATA_DIR / "kokoro_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = model_dir / "kokoro-v1.0.int8.onnx"
    voices_path = model_dir / "voices-v1.0.bin"
    if not onnx_path.exists():
        urllib.request.urlretrieve("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx", onnx_path)
    if not voices_path.exists():
        urllib.request.urlretrieve("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin", voices_path)
    return onnx_path, voices_path

def tts_to_file(text: str, output_path: Path) -> Path:
    from kokoro_onnx import Kokoro
    import soundfile as sf
    onnx_path, voices_path = _ensure_kokoro_model()
    kokoro = Kokoro(str(onnx_path), str(voices_path))
    samples, sample_rate = kokoro.create(text, voice="af_heart", speed=1.0, lang="en-us")
    sf.write(str(output_path), samples, sample_rate)
    return output_path

# ---------------------------------------------------------------------------
# Timing & Untertitel (Whisper Sync)
# ---------------------------------------------------------------------------
_WHISPER_MODEL = None

def _get_word_boundaries(text: str, audio_path: Path) -> list:
    global _WHISPER_MODEL
    from faster_whisper import WhisperModel
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")

    segments, _ = _WHISPER_MODEL.transcribe(str(audio_path), word_timestamps=True, initial_prompt=text)
    whisper_words = [w for seg in segments for w in seg.words if w.word.strip()]

    original_words = text.split()
    aligned = []
    w_idx = 0
    for orig_w in original_words:
        orig_clean = "".join(c for c in orig_w.lower() if c.isalnum())
        if not orig_clean:
            t = aligned[-1][2] if aligned else 0.0
            aligned.append((orig_w, t, t + 0.1))
            continue
        found = False
        for i in range(w_idx, min(w_idx + 4, len(whisper_words))):
            wc = "".join(c for c in whisper_words[i].word.lower() if c.isalnum())
            if wc and (wc in orig_clean or orig_clean in wc):
                aligned.append((orig_w, whisper_words[i].start, whisper_words[i].end))
                w_idx = i + 1
                found = True
                break
        if not found:
            t = aligned[-1][2] if aligned else 0.0
            aligned.append((orig_w, t, t + 0.3))
    return aligned

def _build_ass_from_events(word_events: list) -> str:
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: TikTok,Arial Black,150,&H0000FFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,12,0,5,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""
    def _fmt(sec: float):
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        return f"{int(h)}:{int(m):02d}:{int(s):02d}.{int((s-int(s))*100):02d}"
    lines = [f"Dialogue: 0,{_fmt(s)},{_fmt(e)},TikTok,,0,0,0,,{w.strip().upper()}" for w, s, e in word_events if w.strip()]
    return header + "\n".join(lines)

def build_word_timed_ass(text: str, output_path: Path, audio_path: Path):
    events = _get_word_boundaries(text, audio_path)
    output_path.write_text(_build_ass_from_events(events), encoding="utf-8")

# ---------------------------------------------------------------------------
# Video-Verarbeitung (Hintergrund & Cover)
# ---------------------------------------------------------------------------
def _get_duration(path: Path) -> float:
    res = _run_sub([FFMPEG_EXE, "-i", str(path)], capture_output=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", res.stderr.decode(errors="replace"))
    h, m, s = map(float, match.groups())
    return h * 3600 + m * 60 + s

def create_cover_image(title: str, code: str, part: int, out: Path, bg_frame=None):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import textwrap
    W, H = VIDEO_WIDTH, VIDEO_HEIGHT
    if bg_frame and Path(bg_frame).exists():
        img = Image.open(str(bg_frame)).convert("RGB").resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(3))
    else:
        img = Image.new("RGB", (W, H), (20, 10, 40))

    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle([(0,0),(W,H)], fill=(0,0,0,100))

    font = DRAWTEXT_FONT or None
    try:
        f_tit = ImageFont.truetype(font, 90)
        f_sub = ImageFont.truetype(font, 40)
    except:
        f_tit = f_sub = ImageFont.load_default()

    draw.text((W//2, 300), f"PART {part}", font=f_sub, fill="white", anchor="mm")
    lines = textwrap.wrap(title, width=15)
    y = H // 2 - (len(lines) * 50)
    for line in lines:
        draw.text((W//2, y), line.upper(), font=f_tit, fill="yellow", anchor="mm", stroke_width=4, stroke_fill="black")
        y += 110
    draw.text((W//2, H-200), code, font=f_sub, fill="gray", anchor="mm")
    img.convert("RGB").save(str(out), "JPEG", quality=90)

def _assemble_background_loop(bg_videos: list, target_dur: float, output: Path):
    random.shuffle(bg_videos)
    curr_dur, clips = 0.0, []
    while curr_dur < target_dur:
        for v in bg_videos:
            d = _get_duration(v)
            clips.append(v)
            curr_dur += d
            if curr_dur >= target_dur: break

    tmp_clips = []
    for i, v in enumerate(clips):
        tmp = output.parent / f"tmp_{i}.mp4"
        _run_sub([FFMPEG_EXE, "-y", "-i", str(v), "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}", "-c:v", "libx264", "-an", "-preset", "ultrafast", str(tmp)], check=True)
        tmp_clips.append(tmp)

    list_file = output.parent / "list.txt"
    list_file.write_text("\n".join([f"file '{str(c.resolve()).replace(chr(92),'/')}'" for c in tmp_clips]))
    _run_sub([FFMPEG_EXE, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-t", str(target_dur), "-c:v", "libx264", "-an", str(output)], check=True)
    for c in tmp_clips: c.unlink()
    list_file.unlink()

# ---------------------------------------------------------------------------
# Hauptfunktion: VIDEO GENERIEREN
# ---------------------------------------------------------------------------
def generate_video(story_title: str, story_code: str, part_num: int, text: str) -> Path:
    """
    Bündelt alles: TTS → Untertitel → BG-Loop → Rendering.
    Gibt den Pfad zur fertigen MP4-Datei zurück.
    """
    for d in [OUTPUTS_DIR, TTS_DIR, COVERS_DIR, BACKGROUNDS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    out_dir = OUTPUTS_DIR / story_code
    out_dir.mkdir(parents=True, exist_ok=True)

    final_mp4 = out_dir / f"part_{part_num}.mp4"
    audio_wav = TTS_DIR / f"{story_code}_{part_num}.wav"
    ass_file  = TTS_DIR / f"{story_code}_{part_num}.ass"
    bg_video  = TTS_DIR / f"{story_code}_{part_num}_bg.mp4"
    cover_jpg = COVERS_DIR / f"{story_code}_{part_num}.jpg"

    # 1. TTS
    tts_to_file(text, audio_wav)
    duration = _get_duration(audio_wav)

    # 2. Untertitel
    build_word_timed_ass(text, ass_file, audio_wav)

    # 3. Hintergrund
    bgs = [v for v in BACKGROUNDS_DIR.glob("*") if v.suffix.lower()[1:] in ALLOWED_VIDEO_EXTENSIONS]
    if not bgs:
        raise FileNotFoundError(
            f"Keine Hintergrundvideos in {BACKGROUNDS_DIR}. "
            "Lege mindestens eine .mp4-Datei dort ab."
        )
    _assemble_background_loop(bgs, duration, bg_video)

    # 4. Cover
    create_cover_image(story_title, story_code, part_num, cover_jpg)

    # 5. Final Render
    ass_path = str(ass_file).replace("\\", "/").replace(":", "\\:")
    _run_sub([
        FFMPEG_EXE, "-y", "-i", str(bg_video), "-i", str(audio_wav),
        "-vf", f"ass='{ass_path}'",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(final_mp4)
    ], check=True)

    for f in [audio_wav, ass_file, bg_video]:
        f.unlink(missing_ok=True)

    return final_mp4


if __name__ == "__main__":
    print("Starte Test-Generierung...")
    generate_video(
        story_title="The Secret Room",
        story_code="TEST01",
        part_num=1,
        text="I always thought my house was normal, until I found a key behind the wallpaper. It opened a door that shouldn't exist.",
    )
    print("Fertig!")

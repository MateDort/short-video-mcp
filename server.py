#!/usr/bin/env python3
"""
Short Video MCP Server
Generates TikTok-style short videos narrated by Peter Griffin and Stewie Griffin.

https://github.com/matedort/short-video-mcp
"""
import os
import sys
import json
import logging
import platform
import random
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import anthropic
from elevenlabs import ElevenLabs

# ── Path constants ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

ASSETS_DIR = os.path.join(BASE_DIR, "assets")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CHARACTERS_DIR = os.path.join(ASSETS_DIR, "characters")
BACKGROUND_DIR = os.path.join(ASSETS_DIR, "background")


def _find_binary(name: str) -> str:
    """Find a binary on the system, checking common paths."""
    found = shutil.which(name)
    if found:
        return found
    # Common install locations
    candidates = [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return name  # fallback to bare name, let subprocess raise if missing


FFMPEG_BIN = _find_binary("ffmpeg")
FFPROBE_BIN = _find_binary("ffprobe")

# Font: try macOS, then Linux, then fallback
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",    # Debian/Ubuntu
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",                # Arch
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",  # Fedora
]
FONT_PATH = next((f for f in _FONT_CANDIDATES if os.path.exists(f)), None)

# ── Logging — NEVER write to stdout (corrupts MCP JSON-RPC framing) ───────────
LOG_FILE = os.path.join(BASE_DIR, "server.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("short-video-mcp")

# ── API clients ────────────────────────────────────────────────────────────────
_anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
_elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")

if not _anthropic_key:
    logger.error("ANTHROPIC_API_KEY is not set in .env")
if not _elevenlabs_key:
    logger.error("ELEVENLABS_API_KEY is not set in .env")

anthropic_client = anthropic.Anthropic(api_key=_anthropic_key)
elevenlabs_client = ElevenLabs(api_key=_elevenlabs_key)

VOICE_MAP = {
    "PETER": os.getenv("PETER_VOICE_ID", ""),
    "STEWIE": os.getenv("STEWIE_VOICE_ID", ""),
}

# ── Script generation prompt ──────────────────────────────────────────────────
SCRIPT_SYSTEM_PROMPT = """You write HILARIOUS dialogues between Peter Griffin and Stewie Griffin for TikTok-style videos that also happen to cover ALL the important information.

Given a TOPIC and CONTENT, create a dialogue where Peter and Stewie discuss the topic IN CHARACTER. The comedy comes first — but every key fact sneaks in through their banter. Their personalities:
- PETER: Lovable idiot. Confidently wrong. Compares everything to beer, chicken fights, TV shows, or Lois. Uses absurd analogies that somehow almost make sense. Gets excited about random tangents. Sometimes stumbles into being right by accident. Talks like he is explaining something at The Drunken Clam.
- STEWIE: Evil genius baby. Dripping with sarcasm and contempt for Peters stupidity. Uses unnecessarily big words. Delivers the real facts but always with an insult attached. Gets genuinely frustrated when Peter is dumb. Occasionally impressed when Peter accidentally says something smart.

OUTPUT FORMAT: Return ONLY valid JSON matching this schema — no markdown, no code fences, just the JSON:
{
  "dialogue": [
    {
      "caption": "single sentence, max 25 words",
      "speaker": "PETER" or "STEWIE",
      "emotion": "neutral" | "angry" | "excited" | "confused" | "teaching"
    }
  ]
}

COMEDY TECHNIQUES TO USE:
- Peter misunderstands a concept in a hilarious way, Stewie roasts him then explains correctly
- Peter uses a wild analogy (its like when me and the guys at the Clam...) that somehow circles back to the point
- Stewie gets increasingly exasperated at Peters stupidity
- Peter gets something surprisingly right and Stewie is shocked
- Running gags or callbacks within the dialogue
- Peter interrupts with something completely random then gets pulled back on topic
- End with a killer punchline that ties the humor and the content together

CONTENT RULES:
- COMPLETENESS IS CRITICAL: Every key point, fact, name, date, number, and takeaway from the CONTENT must appear in the dialogue. Do NOT skip details. If the content has 10 facts, all 10 must show up.
- Scale the dialogue length to fit: short content = 8-12 lines, medium = 12-20 lines, dense/long content = 20-35 lines
- Each caption: single sentence, at most 25 words
- The FUNNY delivery is how the facts get communicated — Peter gets it wrong, Stewie corrects with the real info, or Peter explains it in the dumbest correct way possible
- Default emotion to neutral unless a different one clearly fits
- CRITICAL: Every caption must be SPOKEN DIALOGUE ONLY — words the character says out loud. NEVER write stage directions, narration, or descriptions like "Peter thinks..." or "Stewie explains..." — those are NOT dialogue
- Keep the tone hilarious throughout — Peters analogies should be absurd and Stewies roasts savage
- Avoid special characters that would break video captions (no quotes, apostrophes are ok)"""

# ── FastMCP server ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """This server generates TikTok-style short videos with Peter Griffin and Stewie Griffin.

TOOL:
- generate_short_video(topic, content) → Creates a ~30-60 second portrait video
  - topic: The question or subject (e.g. "Why is the sky blue?")
  - content: The educational content/answer to turn into a video

The video features:
- Random gameplay background (Subway Surfers, Minecraft, etc.)
- Peter and Stewie character overlays with emotion-based expressions
- ElevenLabs voice narration for both characters
- Timed captions synchronized with audio

Returns the file path to the generated MP4 video."""

mcp = FastMCP("short-video", instructions=SYSTEM_PROMPT)


# ── Helper functions ───────────────────────────────────────────────────────────

def generate_script(topic: str, content: str) -> list[dict]:
    """Generate a Peter/Stewie dialogue script via Anthropic API."""
    logger.info(f"Generating script for topic: {topic}")

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SCRIPT_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"TOPIC: {topic}\n\nCONTENT:\n{content}",
        }],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    data = json.loads(cleaned)
    dialogue = data.get("dialogue", data)

    # Validate structure
    for line in dialogue:
        if "caption" not in line or "speaker" not in line:
            raise ValueError(f"Invalid dialogue line: {line}")
        line.setdefault("emotion", "neutral")
        line["speaker"] = line["speaker"].upper()

    logger.info(f"Generated {len(dialogue)} dialogue lines")
    return dialogue


def get_audio_duration(filepath: str) -> float:
    """Get duration of an audio file in seconds via ffprobe."""
    result = subprocess.run(
        [
            FFPROBE_BIN, "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            filepath,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def generate_audio(dialogue: list[dict], temp_dir: str) -> tuple[str, list[dict]]:
    """Generate TTS audio for each dialogue line, concatenate, and return timings."""
    segment_files = []

    for i, line in enumerate(dialogue):
        voice_id = VOICE_MAP.get(line["speaker"])
        if not voice_id:
            raise ValueError(f"No voice ID configured for speaker: {line['speaker']}")

        logger.info(f"  TTS segment {i+1}/{len(dialogue)}: {line['speaker']} - {line['caption'][:40]}...")

        segment_path = os.path.join(temp_dir, f"segment_{i:03d}.mp3")
        for attempt in range(3):
            try:
                audio_generator = elevenlabs_client.text_to_speech.convert(
                    text=line["caption"],
                    voice_id=voice_id,
                    model_id="eleven_multilingual_v2",
                    output_format="mp3_44100_128",
                )
                with open(segment_path, "wb") as f:
                    for chunk in audio_generator:
                        f.write(chunk)
                break
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"  TTS attempt {attempt+1} failed, retrying: {e}")
                    import time; time.sleep(1)
                else:
                    raise

        segment_files.append(segment_path)

    # Concatenate all segments
    combined_path = os.path.join(temp_dir, "combined.mp3")
    concat_list_path = os.path.join(temp_dir, "filelist.txt")

    with open(concat_list_path, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    subprocess.run(
        [
            FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path, "-c", "copy", combined_path,
        ],
        capture_output=True, check=True,
    )

    # Build timing metadata
    timings = []
    current_time = 0.0
    for i, seg in enumerate(segment_files):
        duration = get_audio_duration(seg)
        timings.append({
            "start": round(current_time, 3),
            "end": round(current_time + duration, 3),
            "caption": dialogue[i]["caption"],
            "speaker": dialogue[i]["speaker"],
            "emotion": dialogue[i].get("emotion", "neutral"),
        })
        current_time += duration

    total_duration = round(current_time, 3)
    logger.info(f"Audio generated: {len(segment_files)} segments, {total_duration:.1f}s total")

    return combined_path, timings


def get_character_image(speaker: str, emotion: str) -> str | None:
    """Get path to character image, falling back to neutral if emotion variant missing."""
    name = speaker.lower()

    # Try emotion-specific first
    if emotion != "neutral":
        path = os.path.join(CHARACTERS_DIR, f"{name}_{emotion}.png")
        if os.path.exists(path):
            return path

    # Fall back to neutral
    path = os.path.join(CHARACTERS_DIR, f"{name}.png")
    if os.path.exists(path):
        return path

    return None


def pick_background_video() -> str:
    """Pick a random background video from the background directory."""
    videos = [f for f in os.listdir(BACKGROUND_DIR) if f.endswith(".mp4")]
    if not videos:
        raise FileNotFoundError(f"No .mp4 files found in {BACKGROUND_DIR}")
    chosen = random.choice(videos)
    logger.info(f"Selected background video: {chosen}")
    return os.path.join(BACKGROUND_DIR, chosen)


def _render_caption_image(text: str, speaker: str, out_path: str, width: int = 1000) -> None:
    """Render a caption as a transparent PNG with a dark background box."""
    from PIL import Image, ImageDraw, ImageFont

    font_color = (255, 255, 255) if speaker == "PETER" else (255, 255, 0)
    try:
        if FONT_PATH:
            font = ImageFont.truetype(FONT_PATH, 48)
        else:
            font = ImageFont.load_default()
    except (IOError, OSError):
        font = ImageFont.load_default()

    # Create temp image to measure text
    tmp = Image.new("RGBA", (width, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)

    # Word-wrap text
    words = text.split()
    lines = []
    current = ""
    for w in words:
        test = f"{current} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > width - 40:
            if current:
                lines.append(current)
            current = w
        else:
            current = test
    if current:
        lines.append(current)

    wrapped = "\n".join(lines)
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad = 16
    img_w = text_w + pad * 2
    img_h = text_h + pad * 2

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(0, 0), (img_w, img_h)], radius=12, fill=(0, 0, 0, 153))
    draw.text((pad, pad), wrapped, font=font, fill=font_color)
    img.save(out_path)


def assemble_video(audio_path: str, timings: list[dict], output_path: str, bg_video: str) -> None:
    """Assemble the final video with FFmpeg."""
    total_duration = timings[-1]["end"]

    # Render caption images
    caption_dir = output_path.replace(".mp4", "_captions")
    os.makedirs(caption_dir, exist_ok=True)
    caption_paths = []
    for i, t in enumerate(timings):
        cap_path = os.path.join(caption_dir, f"cap_{i}.png")
        _render_caption_image(t["caption"], t["speaker"], cap_path)
        caption_paths.append(cap_path)

    # Collect unique character+emotion combos and their image paths
    character_inputs = {}
    input_args = [
        "-stream_loop", "-1", "-i", bg_video,
        "-i", audio_path,
    ]
    input_idx = 2

    for timing in timings:
        key = (timing["speaker"], timing["emotion"])
        if key not in character_inputs:
            img = get_character_image(timing["speaker"], timing["emotion"])
            if img:
                input_args.extend(["-i", img])
                character_inputs[key] = (input_idx, img)
                input_idx += 1

    # Build filter chain
    filters = []

    # Scale background to portrait 1080x1920
    filters.append("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg]")

    # Scale each character image
    scaled_labels = {}
    for (speaker, emotion), (idx, _) in character_inputs.items():
        label = f"{speaker.lower()}_{emotion}"
        filters.append(f"[{idx}:v]scale=-1:800[{label}_scaled]")
        scaled_labels[(speaker, emotion)] = f"{label}_scaled"

    # Build overlay chain
    speaker_emotion_times = defaultdict(list)
    for t in timings:
        key = (t["speaker"], t["emotion"])
        speaker_emotion_times[key].append((t["start"], t["end"]))

    current_layer = "bg"
    overlay_count = 0

    for (speaker, emotion), times in speaker_emotion_times.items():
        if (speaker, emotion) not in scaled_labels:
            continue

        scaled = scaled_labels[(speaker, emotion)]
        enable_parts = [f"between(t,{s},{e})" for s, e in times]
        enable_expr = "+".join(enable_parts)

        if speaker == "PETER":
            x_pos = "W-w-10"
        else:
            x_pos = "10"
        y_pos = "H-h-10"

        out_label = f"ov{overlay_count}"
        filters.append(
            f"[{current_layer}][{scaled}]overlay=x={x_pos}:y={y_pos}"
            f":enable='{enable_expr}'[{out_label}]"
        )
        current_layer = out_label
        overlay_count += 1

    # Add caption image inputs and overlays
    caption_input_start = input_idx
    for cap_path in caption_paths:
        input_args.extend(["-i", cap_path])
        input_idx += 1

    for i, t in enumerate(timings):
        cap_idx = caption_input_start + i
        cap_label = f"cap{i}_scaled"
        filters.append(f"[{cap_idx}:v]format=rgba[{cap_label}]")
        out_label = f"txt{i}"
        filters.append(
            f"[{current_layer}][{cap_label}]overlay=x=(W-w)/2:y=(H/2-h/2)"
            f":enable='between(t,{t['start']},{t['end']})'"
            f"[{out_label}]"
        )
        current_layer = out_label

    filter_complex = ";".join(filters)

    cmd = [
        FFMPEG_BIN, "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", f"[{current_layer}]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-t", str(total_duration),
        "-shortest",
        output_path,
    ]

    logger.info(f"Running FFmpeg video assembly ({total_duration:.1f}s video)")
    logger.info(f"Filter chain has {len(filters)} filters")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg stderr: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed (exit {result.returncode}): {result.stderr[-500:]}")

    # Clean up caption images
    if os.path.exists(caption_dir):
        shutil.rmtree(caption_dir)

    logger.info(f"Video assembled: {output_path}")


# ── MCP tool ───────────────────────────────────────────────────────────────────

@mcp.tool(
    description="Generate a TikTok-style short video with Peter Griffin and Stewie Griffin "
    "narrating the given content. Returns the file path to the generated MP4."
)
def generate_short_video(content: str, topic: str) -> str:
    """
    Generate a short video narrated by Peter and Stewie Griffin.

    Args:
        content: The educational content/answer to narrate.
        topic: The question or subject being explained.
    """
    logger.info(f"=== generate_short_video called: topic='{topic}' ===")

    # Validate API keys
    if not _anthropic_key:
        return "ERROR: ANTHROPIC_API_KEY is not set. Add it to the .env file."
    if not _elevenlabs_key:
        return "ERROR: ELEVENLABS_API_KEY is not set. Add it to the .env file."

    # Validate assets exist
    if not os.path.exists(BACKGROUND_DIR) or not any(f.endswith(".mp4") for f in os.listdir(BACKGROUND_DIR)):
        return (
            f"ERROR: No background videos found in {BACKGROUND_DIR}. "
            "Please place at least one .mp4 video file there."
        )

    has_peter = os.path.exists(os.path.join(CHARACTERS_DIR, "peter.png"))
    has_stewie = os.path.exists(os.path.join(CHARACTERS_DIR, "stewie.png"))
    if not has_peter and not has_stewie:
        return (
            f"ERROR: No character PNGs found in {CHARACTERS_DIR}. "
            "Need at least peter.png and stewie.png."
        )

    for speaker, vid in VOICE_MAP.items():
        if not vid:
            return f"ERROR: {speaker}_VOICE_ID not set in .env"

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        # Step 1: Generate dialogue script
        dialogue = generate_script(topic, content)

        with tempfile.TemporaryDirectory(prefix="shortvid_") as temp_dir:
            # Step 2: Generate audio
            audio_path, timings = generate_audio(dialogue, temp_dir)

            # Step 3: Assemble video
            slug = re.sub(r"[^a-z0-9]+", "_", topic.lower().strip())[:40].strip("_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{timestamp}_{slug}.mp4"
            output_path = os.path.join(OUTPUT_DIR, output_filename)

            bg_video = pick_background_video()
            assemble_video(audio_path, timings, output_path, bg_video)

        total_duration = timings[-1]["end"]
        logger.info(f"=== Video complete: {output_path} ({total_duration:.1f}s) ===")

        # Auto-open the video (macOS)
        if platform.system() == "Darwin":
            subprocess.Popen(["open", output_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif platform.system() == "Linux":
            subprocess.Popen(["xdg-open", output_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        file_url = "file://" + quote(output_path)
        return (
            f"Video generated successfully!\n"
            f"  Path: {output_path}\n"
            f"  Open: {file_url}\n"
            f"  Duration: {total_duration:.1f}s\n"
            f"  Dialogue lines: {len(dialogue)}\n"
            f"  Speakers: {', '.join(set(t['speaker'] for t in timings))}"
        )

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse script JSON: {e}")
        return f"ERROR: Failed to generate dialogue script — JSON parse error: {e}"
    except subprocess.CalledProcessError as e:
        logger.error(f"Subprocess failed: {e}")
        return f"ERROR: Audio/video processing failed: {e}"
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Short Video MCP server starting (stdio transport)")
    mcp.run(transport="stdio")

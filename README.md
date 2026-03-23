# Short Video MCP Server

Turn any topic into a TikTok-style short video narrated by **Peter Griffin** and **Stewie Griffin**.

Ask Claude a question, then call `generate_short_video` — it creates a script, generates voice audio, and assembles a full portrait video with gameplay background, character overlays, and timed captions.

![Example Output](docs/example_screenshot.png)

## How It Works

```
You ask a question → Claude answers → generate_short_video is called
                                            │
                                            ├── 1. Script: Claude writes a Peter/Stewie dialogue
                                            ├── 2. Audio: ElevenLabs generates voice for each line
                                            └── 3. Video: FFmpeg assembles the final MP4
                                                    ├── Random gameplay background
                                                    ├── Character PNGs with emotion overlays
                                                    └── Timed captions synced to audio
```

**Output:** A 30-90 second portrait MP4 that auto-opens when done.

## Quick Setup

### Prerequisites

| Requirement | How to get it |
|---|---|
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) |
| **FFmpeg** | `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux) |
| **Anthropic API key** | [console.anthropic.com](https://console.anthropic.com/) |
| **ElevenLabs API key** | [elevenlabs.io](https://elevenlabs.io/) (free tier works) |

### 1. Clone and install

```bash
git clone https://github.com/matedort/short-video-mcp.git
cd short-video-mcp
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env with your API keys and ElevenLabs voice IDs
```

Your `.env` should look like:
```
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
ELEVENLABS_API_KEY=sk_your-elevenlabs-key-here
PETER_VOICE_ID=your-peter-voice-id
STEWIE_VOICE_ID=your-stewie-voice-id
```

**Finding voice IDs:** Go to [elevenlabs.io/voice-library](https://elevenlabs.io/voice-library), find voices you like, add them to your account, then copy the voice ID from the voice settings. Any two voices work — pick a deep one for Peter and a higher-pitched one for Stewie.

### 3. Add assets

Place your files in:
```
assets/
├── background/          ← .mp4 gameplay videos (at least one)
│   ├── subwaysurfers.mp4
│   ├── minecraft.mp4
│   └── ...
└── characters/          ← Character PNGs
    ├── peter.png        ← Required
    ├── stewie.png       ← Required
    ├── peter_excited.png    ← Optional emotion variants
    ├── peter_confused.png
    ├── peter_teaching.png
    ├── stewie_angry.png
    └── stewie_excited.png
```

Background videos should be **portrait orientation** (or they'll be cropped to fit). Character PNGs should have **transparent backgrounds**.

### 4. Connect to Claude

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "short-video": {
      "command": "python3",
      "args": ["/full/path/to/short-video-mcp/server.py"],
      "env": {}
    }
  }
}
```

**Claude Code** — add to `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "short-video": {
      "command": "python3",
      "args": ["/full/path/to/short-video-mcp/server.py"],
      "env": {}
    }
  }
}
```

Then restart Claude.

## Usage

In Claude, just ask a question and tell it to make a video:

> "Explain why the sky is blue and make a short video about it"

Or provide detailed content:

> "Here's my notes on quantum computing [paste content]. Generate a short video from this."

It works great with PDFs too — upload a PDF, ask Claude to summarize it, then generate a video.

## Master Prompt (One-Click Setup)

Copy and paste this prompt into Claude Code or Claude Desktop to set everything up automatically:

```
I want to set up the short-video MCP server from GitHub.

Before you start, I need to confirm:
1. Do you have an Anthropic API key? (get one at console.anthropic.com)
2. Do you have an ElevenLabs API key? (get one at elevenlabs.io — free tier works)

Once confirmed, please:
1. Clone https://github.com/matedort/short-video-mcp.git to my home directory
2. Run: pip install -r requirements.txt
3. Create the .env file from .env.example and ask me for my API keys and voice IDs
4. Ask me to provide background gameplay videos (.mp4) and character PNGs, or help me find/download some
5. Add the short-video MCP server to my Claude config:
   - Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json
   - Claude Code: ~/.claude.json
   Use the full path to server.py with python3 as the command
6. Tell me to restart Claude, then test it with: "Explain why pizza is the best food and generate a short video about it"
```

## Project Structure

```
short-video-mcp/
├── server.py            ← MCP server (the only code file)
├── requirements.txt     ← Python dependencies
├── .env.example         ← Template for API keys
├── .env                 ← Your actual API keys (git-ignored)
├── assets/
│   ├── background/      ← Gameplay videos (.mp4)
│   └── characters/      ← Character PNGs with transparent backgrounds
├── output/              ← Generated videos land here (git-ignored)
└── docs/
    └── example_screenshot.png
```

## How the Video is Built

1. **Script Generation** — Claude (via Anthropic API) writes a dialogue between Peter and Stewie that covers all the key information from your content, staying in character with humor
2. **Voice Synthesis** — Each dialogue line is sent to ElevenLabs TTS with the corresponding voice ID, then all segments are concatenated
3. **Video Assembly** — FFmpeg composites everything:
   - Background gameplay video (randomly selected, looped)
   - Character PNGs positioned bottom-left (Stewie) and bottom-right (Peter)
   - Characters appear/disappear based on who's speaking, with emotion-specific images
   - Caption text rendered as overlays, centered and synced to audio timing

## Configuration

| Environment Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key for script generation |
| `ELEVENLABS_API_KEY` | Your ElevenLabs API key for voice synthesis |
| `PETER_VOICE_ID` | ElevenLabs voice ID for Peter Griffin |
| `STEWIE_VOICE_ID` | ElevenLabs voice ID for Stewie Griffin |

## Troubleshooting

| Issue | Fix |
|---|---|
| `ffmpeg not found` | Install FFmpeg: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux) |
| `ANTHROPIC_API_KEY not set` | Create `.env` from `.env.example` and add your key |
| Voice sounds wrong | Try different ElevenLabs voice IDs — browse the [voice library](https://elevenlabs.io/voice-library) |
| Video takes too long | Use shorter content, or the server uses `ultrafast` FFmpeg preset by default |
| Claude says "credits" or "quota" | That's Claude hallucinating — there's no credit system. The server runs locally. Just retry. |
| Server not showing in Claude | Make sure the path in your config is absolute and points to `server.py`. Restart Claude fully (Cmd+Q). |

## Credits

Inspired by [HackEmory-backend](https://github.com/Jayyk09/HackEmory-backend) by [@Jayyk09](https://github.com/Jayyk09).

## License

MIT

import base64
import os

import httpx

from .. import config, pricing
from ..openai_client import client
from ..refs import literal_chars, template_refs
from .base import Cost, Limb, LimbError

ELEVEN = "https://api.elevenlabs.io/v1"
REF_CHARS = 150


def artifact_path(ctx, ext: str):
    d = config.ARTIFACTS_DIR / ctx.run_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ctx.step_id}.{ext}"
    return p, f"{config.PUBLIC_BASE_URL}/artifacts/{ctx.run_id}/{p.name}"


async def _eleven_post(path: str, body: dict) -> bytes:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(ELEVEN + path, json=body, headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]})
    if r.status_code >= 400:
        raise LimbError(f"ElevenLabs {path} -> {r.status_code}: {r.text[:500]}")
    return r.content


class TtsLimb(Limb):
    name = "tts"
    description = "Text to speech (ElevenLabs). Returns {file, url} of an mp3. Billed per character."
    args_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "voice_id": {"type": "string", "description": "Optional"},
            "model_id": {"type": "string", "description": "Optional ElevenLabs model"},
        },
        "required": ["text"],
    }
    requires = ("ELEVENLABS_API_KEY",)
    seconds = 3.0
    outputs = {"file", "url"}

    def estimate(self, args):
        text = args.get("text", "")
        chars = min(literal_chars(text) + REF_CHARS * len(template_refs(text)), config.CAPS["max_tts_chars"])
        return Cost(tts_chars=chars, usd=pricing.tts(args.get("model_id") or config.TTS_MODEL, chars), seconds=self.seconds)

    def precheck(self, args):
        return Cost(tts_chars=len(str(args.get("text", ""))))

    async def run(self, args, ctx):
        text = str(args["text"])
        model = args.get("model_id") or config.TTS_MODEL
        ctx.charge(Cost(tts_chars=len(text), usd=pricing.tts(model, len(text))))
        audio = await _eleven_post(f"/text-to-speech/{args.get('voice_id') or config.VOICE_ID}", {"text": text, "model_id": model})
        path, url = artifact_path(ctx, "mp3")
        path.write_bytes(audio)
        return {"file": str(path), "url": url}


class SfxLimb(Limb):
    name = "sfx"
    description = "Generate a sound effect from a text description (ElevenLabs). Returns {file, url} of an mp3."
    args_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "duration_seconds": {"type": "number", "description": "0.5-22, optional"},
        },
        "required": ["text"],
    }
    requires = ("ELEVENLABS_API_KEY",)
    seconds = 4.0
    outputs = {"file", "url"}

    async def run(self, args, ctx):
        body = {"text": str(args["text"])}
        if args.get("duration_seconds"):
            body["duration_seconds"] = float(args["duration_seconds"])
        audio = await _eleven_post("/sound-generation", body)
        path, url = artifact_path(ctx, "mp3")
        path.write_bytes(audio)
        return {"file": str(path), "url": url}


class ImageLimb(Limb):
    name = "image"
    description = "Generate one image from a prompt (OpenAI). Expensive: requires policy.max_images. Returns {file, url}."
    args_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"]},
            "quality": {"type": "string", "enum": ["low", "medium", "high"], "description": "Default low"},
        },
        "required": ["prompt"],
    }
    requires = ("OPENAI_API_KEY",)
    seconds = 20.0
    outputs = {"file", "url"}

    def estimate(self, args):
        return Cost(images=1, seconds=self.seconds)

    def validate_args(self, args, spec):
        return [] if spec.policy.max_images >= 1 else ["image needs policy.max_images >= 1"]

    async def run(self, args, ctx):
        ctx.charge(Cost(images=1, usd=pricing.image(config.IMAGE_MODEL, args.get("quality") or "low")))
        resp = await client().images.generate(
            model=config.IMAGE_MODEL, prompt=str(args["prompt"]), size=args.get("size") or "1024x1024",
            quality=args.get("quality") or "low", n=1,
        )
        path, url = artifact_path(ctx, "png")
        path.write_bytes(base64.b64decode(resp.data[0].b64_json))
        return {"file": str(path), "url": url}


class WebSearchLimb(Limb):
    name = "web_search"
    description = "Web search (Tavily). Returns {results: [{title, url, content}]}. Cheap and fast, no LLM tokens."
    args_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "1-10"}},
        "required": ["query"],
    }
    requires = ("TAVILY_API_KEY",)
    seconds = 2.0
    outputs = {"results"}

    def estimate(self, args):
        return Cost(usd=pricing.WEB_SEARCH, seconds=self.seconds)

    async def run(self, args, ctx):
        ctx.charge(Cost(usd=pricing.WEB_SEARCH))
        body = {"query": str(args["query"]), "max_results": max(1, min(int(args.get("max_results") or 5), 10))}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.tavily.com/search",
                json=body,
                headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
            )
        if r.status_code >= 400:
            raise LimbError(f"Tavily -> {r.status_code}: {r.text[:500]}")
        return {
            "results": [
                {"title": x.get("title"), "url": x.get("url"), "content": x.get("content")}
                for x in r.json().get("results", [])
            ]
        }

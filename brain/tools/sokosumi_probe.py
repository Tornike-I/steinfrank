"""Помощник для хакатона: найти в Sokosumi агента, который сможет быть «мозгом» Frankenstein, и проверить ElevenLabs.

Ключи читаются из backend/.env (в консоль не печатаются).

  backend/.venv/Scripts/python tools/sokosumi_probe.py                 список агентов + кредиты, лучшие кандидаты сверху
  backend/.venv/Scripts/python tools/sokosumi_probe.py --test AGENT_ID  отправить одно крошечное задание и показать задержку и форму ответа
  backend/.venv/Scripts/python tools/sokosumi_probe.py --voices         список голосов ElevenLabs (для ELEVENLABS_VOICE_ID)
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import config                                   # noqa: E402  (читает backend/.env)
from app.sokosumi import SokosumiClient, _fill_inputs, _schema_fields, job_credits  # noqa: E402

# слова, по которым агент похож на «универсальный LLM / кодер»
GOOD = ("llm", "chat", "assistant", "gpt", "claude", "gemini", "code", "coding", "developer", "programming", "reason",
        "general", "prompt", "question", "answer", "write", "python", "json")
BAD = ("image", "video", "audio", "music", "twitter", "tweet", "seo", "price", "trading", "crypto", "nft", "translate",
       "voice", "podcast", "instagram", "linkedin")


def score(agent: dict, fields: list[dict] | None) -> int:
    text = f"{agent.get('name', '')} {agent.get('description', '')} {agent.get('tags', '')}".lower()
    s = sum(3 for w in GOOD if w in text) - sum(4 for w in BAD if w in text)
    if fields is not None:
        textual = [f for f in fields if str(f.get("type", "")).lower() in ("string", "text", "textarea", "")]
        required = [f for f in fields if f not in textual]
        s += 5 if len(textual) >= 1 else -20           # нужно свободное текстовое поле
        s -= 2 * len(required)                          # лишние обязательные поля мешают
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", metavar="AGENT_ID")
    ap.add_argument("--voices", action="store_true")
    args = ap.parse_args()

    if args.voices:
        import httpx
        r = httpx.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": config.ELEVENLABS_API_KEY}, timeout=30)
        print(r.status_code)
        for v in (r.json().get("voices") or []):
            print(f"{v['voice_id']}  {v['name']:<20} {v.get('category', '')}  {v.get('labels', {})}")
        return

    if not config.SOKOSUMI_API_KEY:
        sys.exit("SOKOSUMI_API_KEY is empty - put it into backend/.env")
    api = SokosumiClient()

    if args.test:
        schema = api.input_schema(args.test)
        fields = _schema_fields(schema)
        print("input fields:", [(f.get("id"), f.get("type")) for f in fields])
        data, _ = _fill_inputs(fields, "Reply with exactly this JSON and nothing else: {\"ok\": true, \"answer\": 42}")
        print("sending a tiny job (costs a few credits)...")
        t0 = time.time()
        text, job = api.run(args.test, "Reply with exactly this JSON and nothing else: {\"ok\": true, \"answer\": 42}",
                            on_poll=lambda st: print(f"  status={st}  t={time.time() - t0:.0f}s"))
        print(f"\nlatency {time.time() - t0:.1f}s, credits {job_credits(job)}")
        print("extracted text:", text[:500])
        print("job keys:", sorted(job))
        return

    print("credits:", api.credits())
    agents = api.list_agents(100)
    print(f"{len(agents)} agents\n")
    rows = []
    for a in agents:
        try:
            fields = _schema_fields(api.input_schema(a["id"]))
        except Exception:                                # noqa: BLE001
            fields = None
        rows.append((score(a, fields), a, fields))
    for sc, a, fields in sorted(rows, key=lambda r: -r[0])[:15]:
        names = [f.get("id") for f in fields] if fields is not None else "?"
        print(f"[{sc:>3}] {a['id']}  {a.get('name', '')[:40]:<40} credits={a.get('credits', a.get('price', '?'))}  fields={names}")
        if a.get("description"):
            print("       ", str(a["description"])[:140].replace("\n", " "))
    print("\nNext: python tools/sokosumi_probe.py --test <AGENT_ID>   then put it into SOKOSUMI_AGENT_ID in backend/.env")


if __name__ == "__main__":
    main()

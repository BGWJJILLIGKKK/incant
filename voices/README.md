# Voice library

`voices.json` lists the voices the IndexTTS server provides, keyed by id:

```json
{
  "a_janelle_risa": { "voice": "a_janelle_risa", "source": "voice-zero", "license": "public domain (Voice-Zero)", "default_emotion": "calm" }
}
```

Any field of `incant.schemas.Voice` may be added to an entry. Casting uses the tags `gender` (male/female),
`age` (teen/adult/older/neutral) and `tone` (high/neutral/deep); voices without a `gender` are never cast
automatically. `description` is free text (timbre, pace, accent); `license`, `source` and `consent` are the
voice's provenance and go into each audiobook's `credits.txt`.

A `voices/<id>.json` file holding one `Voice` overrides the `voices.json` entry with the same id.

Other backends have built-in voices; `kokoro.json`, `openai.json` and `elevenlabs.json` here add voices or
override their fields (tags, Kokoro blends via `mix`) in the same `{id: {fields}}` form. `<id>.wav` is the
voice's preview sample on the review page. Details: `docs/interfaces.md`, "Voice library".

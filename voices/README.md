# Voice library

`voices.json` lists the voices the IndexTTS server provides, keyed by id:

```json
{
  "a_janelle_risa": { "voice": "a_janelle_risa", "source": "voice-zero", "default_emotion": "calm" }
}
```

Any field of `incant.schemas.Voice` may be added to an entry and is used for casting:
`gender` (male/female/nonbinary/unknown), `age` (child/teen/young_adult/adult/elderly),
`description` (free text: timbre, pace, accent), and provenance — `license`, `consent`
— which matters if anything is ever distributed.

A `voices/<id>.json` file holding one `Voice` overrides the index entry with the same id.

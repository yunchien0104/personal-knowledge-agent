# Instagram Reel URL-only pipeline

## Runtime flow

1. Web validates and canonicalizes `https://www.instagram.com/reel/{shortcode}/`.
2. `create_instagram_reel_asset` creates the user-owned Asset.
3. `enqueue_instagram_reel_cloud_ingestion` creates one durable
   `ingest_instagram_reel` media Job with `acquisitionMode=cloud`.
4. The Reel Worker asks a licensed acquisition provider for a temporary media
   URL. It does not log or store the provider credential.
5. The Worker downloads at most 150 MiB into an isolated temporary directory,
   rejects redirects/unlisted media hosts, and rejects videos over 15 minutes.
6. The bundled FFmpeg runtime extracts 30-second mono audio chunks and at most
   eight representative JPEG frames.
7. `gpt-transcribe` produces transcript chunks. The configured Vision model
   describes only visible facts in each frame.
8. The Worker creates Unified Instagram Reel Contract v2. Transcript locators
   use `content_origin=source_transcript`; generated visual observations use
   `content_origin=ai_visual_observation`, `model_name`, and `frame_ids`.
9. The existing immutable Storage, fenced publication, Evidence, Retrieval,
   Citation, and Source Note pipelines continue unchanged. Original video and
   audio chunks are deleted when the temporary directory closes.

Caption and transcript are combined when both exist; otherwise the available
one is used. If neither exists, the Job fails without publishing searchable
content. Visual observations augment source text but do not replace this rule.

## Acquisition provider contract

The Worker sends an authenticated HTTPS `POST` request:

```json
{"url":"https://www.instagram.com/reel/CODE/"}
```

The provider must return:

```json
{
  "video_url": "https://approved-media-host.example/temporary.mp4",
  "caption": "optional caption",
  "author": {
    "external_id": "optional stable id",
    "handle": "optional handle",
    "display_name": "optional display name"
  },
  "published_at": "2026-10-29T00:00:00Z"
}
```

`video_url` is required. Other fields are optional, but `published_at` must be
ISO-8601 and author fields must match Contract limits when present. The provider
is responsible for lawful, authorized access to public Reel media.

## Required server-only environment

- `REEL_ACQUISITION_API_URL`: exact licensed-provider HTTPS endpoint.
- `REEL_ACQUISITION_API_KEY`: provider token.
- `REEL_MEDIA_ALLOWED_HOSTS`: comma-separated exact media host allowlist.
- `OPENAI_API_KEY`: transcription and Vision credential.
- `REEL_TRANSCRIPTION_MODEL`: defaults to `gpt-transcribe`.
- `REEL_VISION_MODEL`: defaults to `gpt-4.1-mini`.

These variables belong only on the Reel Worker. Do not expose them through
`NEXT_PUBLIC_*`, Vercel client variables, logs, fixtures, or Git.

## Deployment order

1. Add the server-only variables to the Reel Worker without deploying Web.
2. Deploy the additive Supabase migration and verify only the new RPC/trigger.
3. Deploy the Reel Worker and run one synthetic provider-boundary smoke test.
4. Deploy Web, then submit one authorized public Reel from an isolated user.
5. Verify Job completion, private artifacts, Retrieval, Citation, and automatic
   Source Note before enabling the entry point broadly.

The legacy Contract-upload API remains available as rollback compatibility; the
current UI no longer asks users for Contract JSON or keyframes.

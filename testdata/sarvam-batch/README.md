# Sarvam batch speech-to-text vectors

The HTTP exchange of one Sarvam batch job, as the after-call pass drives it
(`python/dafter_providers/src/dafter_providers/sarvam/batch.py`), replayed by
a local stub server in `python/dafter_providers/tests/test_sarvam_batch.py`,
which asserts every request body and header against these files. No test
calls Sarvam.

Shapes come from Sarvam's API reference (docs.sarvam.ai, "Batch - Initiate
Job", "Upload Files", "Start Job", "Get Status", "Download Results") and the
`sarvamai` 0.1.34 SDK that implements it; values are illustrative:

- `POST https://api.sarvam.ai/speech-to-text/job/v1`, header
  `api-subscription-key`, body `create-job.request.json` (`job_parameters`:
  `language_code`, `model` `saaras:v3` or `saaras:v4`, `mode`,
  `with_timestamps`, `with_diarization`); answers `create-job.response.json`
  with `job_id` and `job_state`.
- `POST .../job/v1/upload-files` with the job id and file names; answers a
  presigned `file_url` per file (Azure blob storage), which takes the audio as
  `PUT` with `x-ms-blob-type: BlockBlob` and its content type, as the SDK's
  `upload_files` does.
- `POST .../job/v1/{job_id}/start`, no body.
- `GET .../job/v1/{job_id}/status` until `job_state` is `Completed` or
  `Failed`; `job_details` maps each input `file_name` to an output
  `file_name` and a per-file `state` (`Success` or an error state).
- `POST .../job/v1/download-files` with the output names; answers a presigned
  `file_url` per output, fetched with a plain `GET`.
- Each output (`output-0.json`, `output-1.json`) is the speech-to-text
  response: `transcript`, `language_code`, and with `with_timestamps`,
  `timestamps` of chunk-level (phrase, not word) `words`,
  `start_time_seconds` and `end_time_seconds`. `words` is the field name in
  Sarvam's REST reference and SDK type (`TimestampsModel`); the batch guide
  calls it `chunks`, so the adapter reads either.

`{storage}` stands for the stub server's address. Limits the pass respects:
at most 20 files per job (split into several jobs above that), up to 2 hours
per file (a longer file fails in its job and the pass reports it), formats
detected from the file (the recordings are Ogg Opus from a track egress, which
Sarvam lists as `ogg`/`opus`). `mode` `verbatim` is the verbatim rendering
(fillers and spoken numbers as said); `transcribe` is the clean one.

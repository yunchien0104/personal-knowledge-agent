# Research Evaluation Report

## Automated suite

The repository contains manually judged retrieval cases in
`worker/tests/research/eval_data/retrieval_golden_v1.json`, covering Chinese,
English, mixed language, paraphrases, and per-character PDF line breaks. The
metric implementation reports Recall@5, Recall@10, Precision@5, MRR, nDCG@10,
and Relevant Evidence Coverage. Existing R2 tests cover citation validity,
claim support verdicts, contradiction, repair, re-verification, gate behavior,
and publication blocking.

Thresholds are recorded in the dataset: Recall@5 >= 0.80, Recall@10 >= 0.90,
Precision@5 >= 0.40, MRR >= 0.70, nDCG@10 >= 0.75, and Evidence Coverage >=
0.90. These are release gates, not claimed results.

## Performance baseline protocol

Run against isolated Staging at corpus sizes 1k, 10k, 100k, and the largest
available non-production fixture set. Capture 100 warm and 20 cold queries per
language group, then report p50/p95/p99 end-to-end and lexical/vector SQL
latencies. Record `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` for lexical and HNSW
paths, index scan names, rows removed by filters, buffer reads/hits, and HNSW
recall against exact cosine search. Load tests must exercise 1, 5, 20, and 50
concurrent users with fixed query/candidate/token caps.

No database or load environment was available in this local run, so latency,
query-plan, ANN recall, and concurrency capacity results are **BLOCKED**, not
passed. Local unit-test timings are not a SaaS capacity claim.

## Release gate

Do not mark Production Ready until all golden thresholds, two-user isolation,
deleted/superseded source, crash/retry/idempotency, timeout, concurrent request,
query plan, load, and browser-to-publication Staging tests pass with archived
artifacts.

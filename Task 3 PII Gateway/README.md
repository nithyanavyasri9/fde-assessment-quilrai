# Task 3 — LLM Gateway Streaming PII Redaction

## Setup
@"
# Task 3 — LLM Gateway Streaming PII Redaction

## Setup
pip install -r requirements.txt

## Run
Terminal 1 - mock LLM server:
python mock_llm_server.py

Terminal 2 - PII redaction gateway:
python gateway.py

## Test (no servers needed)
python test_gateway.py

## Live stream test (servers must be running)
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

## PII Patterns Redacted
| Pattern | Tag |
|---------|-----|
| Email addresses | [REDACTED-EMAIL] |
| SSNs | [REDACTED-SSN] |
| Credit card numbers | [REDACTED-CC] |
| Phone numbers | [REDACTED-PHONE] |

## Key Design - Rolling Overlap Buffer
Naive per-chunk regex misses PII that spans two chunks.
Example: chunk 1 = john.sm / chunk 2 = ith@example.com

Solution: hold back last 50 chars of each chunk as overlap.
PII is always redacted in a single call. Memory usage is O(1).

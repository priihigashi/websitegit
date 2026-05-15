# SKILL: Handle Apify Data Absence

## Purpose
This skill addresses the issue of running in fallback mode due to the absence of Apify data.

## Trigger
When the system detects no Apify data during a run.

## Actions
- Implement a retry mechanism for Apify data fetching with exponential backoff.
- Log detailed error messages indicating the cause of no data.
- Notify the human operator if retry attempts fail after a certain threshold.

## Success Condition
- The system should successfully fetch Apify data after retrying, or notify the human operator if all attempts fail.
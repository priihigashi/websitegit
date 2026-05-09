# SKILL: Fix Apify Fallback Mode

## Purpose
Prevent fallback mode due to issues with Apify data retrieval.

## Trigger
When the system enters fallback mode with the message "Ran in fallback mode (no Apify data)."

## Actions
1. Log the error details for review, including any Apify API response or error messages.
2. Implement retry logic with exponential backoff for Apify API calls.
3. If the issue persists after retries, send an alert to the system administrator with error details and context.
4. Optionally, implement a secondary data source or caching mechanism to reduce reliance on Apify during outages.

## Notes
- Ensure that API keys and configurations for Apify are correctly set and checked before retries.
- This skill aims to improve data retrieval reliability from Apify and reduce unnecessary fallback mode entries.
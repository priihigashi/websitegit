# SKILL: Fix Google Sheets Range Error

## Description
This skill addresses the recurring 'Unable to parse range' error when accessing Google Sheets via the API. It ensures that any range requests to Sheets are properly formatted and validated before the request is made.

## Steps
1. Validate the range string before making the API request.
2. Check for typos or incorrect sheet names in the range string.
3. Implement error handling to catch and log incorrect range errors.
4. Provide feedback to the user or system to correct the range string.

## Implementation
- Use a regular expression to validate the range format.
- Log errors with detailed information about the incorrect range.
- Automatically correct common mistakes if possible, such as trimming whitespace or correcting known sheet names.
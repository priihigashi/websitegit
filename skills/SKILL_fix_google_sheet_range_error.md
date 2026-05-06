# SKILL: Fix Google Sheet Range Error

## Purpose
This skill ensures that the range specified in Google Sheets API requests is valid and correctly formatted to prevent HttpError 400 due to range parsing issues.

## Steps
1. **Validate Range Format**: Before making an API request, parse and validate the range string to ensure it follows the expected format.
2. **Fallback Range**: Implement a fallback mechanism to use a default valid range if the specified range is invalid.
3. **Logging**: Log any discrepancies or errors in range formatting to facilitate debugging.
4. **Testing**: Ensure thorough testing with various range formats to confirm the skill's effectiveness.

## Usage
- Implement this skill as a pre-processing step before any Google Sheets API call involving range specifications.
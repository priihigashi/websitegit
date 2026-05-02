# SKILL: Fix Google Sheets Range Parsing

## Purpose
To prevent HttpError 400 due to incorrect range parsing in Google Sheets API requests.

## Steps
1. **Validate Range Format:** Implement a function to validate the format of the range before making API requests.
   - Ensure the range format follows the 'SheetName!A1:F10' pattern.
   - Check for any special characters or spaces that might need URL encoding.

2. **Error Handling:** Add error handling to catch HttpError 400 and log specific details about the incorrect range.

3. **Test Cases:** Develop test cases to simulate various range inputs and ensure the function correctly identifies valid and invalid ranges.
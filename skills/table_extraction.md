---
name: Table Data Extraction (Visual Model Only)
description: Locate and extract table data from documents and output it in standard Markdown table format
enabled: true
---

## Use Case
Activate this skill when the user requests to extract, organize, or display table data from a document.

## Execution Steps
1. **Locate the table**: Use keyword_search with terms like "Table" to find table references, or use tree_search with the table number/name specified by the user to identify the containing node
2. **Read content**: Use read_node to retrieve the full text of the node containing the table; if the table is incomplete in the text, use navigate_outline to check adjacent nodes for missing data
3. **Extract the table** (visual mode only): Use the view_pages tool to perform structured extraction on the target node
4. **Visual assistance** (visual mode only): If the text extraction result is incomplete or poorly formatted, use view_pages to view the original table image and supplement missing row/column data with visual analysis
5. **Format output**: Organize the extracted data into a standard Markdown table

## Output Format
Output using the following structure:

**Table Name**: Table X — Title (if available)

**Table Location**: Node XXXX, Page X

| Col 1 | Col 2 | Col 3 | ... |
|-------|-------|-------|-----|
| data  | data  | data  | ... |

**Additional Notes**:
- If the table contains merged cells, describe them in text
- If the table has footnotes or remarks, append them below the table
- If data is incomplete and cannot be fully reconstructed, clearly indicate the missing parts

## Notes
- Preserve data precision; do not round or omit original values
- If a table spans multiple pages or nodes, merge all fragments before outputting the complete table
- Table headers must be accurate; do not invent column names

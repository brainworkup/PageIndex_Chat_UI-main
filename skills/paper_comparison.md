---
name: Paper Comparison Analysis
description: Systematically compare the similarities and differences between different methods, models, or sections in a document
enabled: true
---

## Use Case
Activate this skill when the user requests a comparative analysis of different parts of a document (e.g., different methods, algorithms, or experimental results).

## Execution Steps
1. **Retrieve separately**: For each subject being compared, independently use tree_search and read_node to fetch the relevant content
2. **Extract key dimensions**: Organize each subject's information along the following dimensions:
   - Core idea / method
   - Technical architecture
   - Strengths and limitations
   - Experimental setup and results
   - Applicable scenarios
3. **Structured comparison**: Present the comparison using a table or side-by-side format
4. **Summarize conclusions**: Provide an overall evaluation and recommendations

## Output Format
Present the comparison using a Markdown table, for example:

| Dimension | Method A | Method B |
|-----------|----------|----------|
| Core Idea | ... | ... |
| Strengths | ... | ... |
| Limitations | ... | ... |

Finish with an overall analysis and recommendations.

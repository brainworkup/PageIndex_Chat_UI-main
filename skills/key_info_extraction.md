---
name: Key Information Extraction
description: Extract core contributions, methods, experimental results, and other key information from papers
enabled: true
---

## Use Case
Activate this skill when the user wants a quick overview of a paper's core content or a summary of its key findings.

## Execution Steps
1. Use tree_search to obtain the overall document structure
2. Retrieve the following key sections in order:
   - Abstract
   - Contribution statements in the Introduction
   - Core design of the method/model
   - Experimental setup (datasets, baselines, evaluation metrics)
   - Main experimental results and ablation studies
   - Conclusion and future work

## Output Format
Output using the following structure:

**📄 Paper Summary**
- **Research Problem**: ...
- **Core Contributions**: (list 2–4 points)
- **Method Overview**: ...
- **Key Experimental Results**: ...
- **Conclusion**: ...
- **Limitations & Future Directions**: ...

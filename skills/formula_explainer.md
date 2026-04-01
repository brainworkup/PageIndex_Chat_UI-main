---
name: Formula Explainer
description: Explain mathematical formulas in documents in detail, including symbol definitions and derivation logic
enabled: false
---

## Use Case
Activate this skill when the user asks about the meaning, derivation process, or symbol definitions of a formula.

## Execution Steps
1. Locate the node containing the formula and retrieve the formula along with its context
2. Find the definitions of all symbols within the context (they may appear in earlier sections)
3. If a visual model is available, use view_pages to inspect the original formula image to ensure accuracy

## Output Format
Explain using the following structure:

1. **Formula**: Give the complete form of the formula as a LaTeX expression compatible with Markdown, centered and in a bold box.
2. **Symbol Definitions**: List each symbol and its meaning one by one; write all symbols using Markdown-compatible LaTeX notation.
3. **Intuitive Explanation**: Explain in plain language what the formula is doing.
4. **Derivation Highlights**: If requested by the user, provide the key derivation steps.
# FlytBase Documentation System — Agent Configuration

This file is read by the drafting agent on every run. It defines the voice, formatting conventions,
and structural rules that all generated documentation must follow.

---

## Voice Guide

### Docs Pages (docs.flytbase.com)
- **Tone**: Professional, instructional, direct. Use imperative voice for steps ("Click on", "Select", "Navigate to").
- **Reader address**: Implicit — describe capabilities objectively. Avoid excessive "you" unless giving direct instructions in step-by-step procedures.
- **Sentence length**: Short to medium. Prioritize scannability over narrative.
- **Technical depth**: Accessible but not dumbed down. Assume the reader is a drone operations professional, not a developer.
- **Feature framing**: Lead with what the feature does and why it matters, then how to use it.

### Release Notes (releases.flytbase.com)
- **Tone**: Conversational and direct. More narrative than docs — treat release notes as storytelling, not reference.
- **Structure**: Start with the problem or context, explain why it matters, then show the solution.
- **Framing**: Use real-world analogies where helpful. Subheadings should ask questions or make claims, not just label sections.
- **Limitations**: State limitations transparently. Don't oversell.

### Common Rules (both)
- Never use "we" to refer to FlytBase (the company). Use "FlytBase" or passive voice.
- Avoid marketing language ("revolutionary", "cutting-edge", "best-in-class").
- Use present tense for feature descriptions ("The scheduler allows..." not "The scheduler will allow...").
- Numbers: spell out one through nine, use digits for 10+. Always use digits with units (e.g., "5 meters", "3 drones").
- Acronyms: spell out on first use with abbreviation in parentheses, then use abbreviation. Example: "Collision Avoidance Sensing (CAS)".

---

## Formatting Conventions

### Heading Hierarchy
- **H1**: Page title only (one per page).
- **H2**: Major sections (Overview, How It Works, Configuration, etc.).
- **H3**: Sub-sections within H2.
- **H4**: Rarely used — only for deeply nested content.
- Use title case for all headings.

### Step-by-Step Instructions
- Use numbered lists (1, 2, 3...) for sequential procedures.
- Bold the action verb or UI element at the start: "**Click** on Add New Device".
- Nest sub-actions as bullet points under the numbered step, indented.
- Keep steps atomic — one action per step.

### Callouts and Hints
Use GitBook-style hint blocks:

```
{% hint style="info" %}
Informational note here.
{% endhint %}

{% hint style="warning" %}
Warning about potential issues.
{% endhint %}

{% hint style="danger" %}
Critical warning — data loss or safety risk.
{% endhint %}

{% hint style="success" %}
Confirmation or positive outcome note.
{% endhint %}
```

### UI Element References
- Bold UI element names: **Add New Device**, **Mission Scheduler**, **Save**.
- Use `code formatting` for values, URLs, API fields, file paths.
- Use ">" to indicate navigation paths: **Settings > Flight Configuration > Failsafes**.

### Images and Media
- Place images immediately after the text that references them.
- Use descriptive alt text that explains what the image shows.
- GIFs go inline to demonstrate interactive workflows.
- YouTube embeds go at the end of the relevant section, not at the top.
- Asset path format: `assets/{feature-slug}/{descriptive-name}.{ext}`

### Frontmatter Template (docs pages)
```yaml
---
description: >-
  One to two sentence summary of what this page covers.
---
```

### Frontmatter Template (release notes)
```yaml
---
description: >-
  Brief summary of the feature or update being released.
---
```

---

## IA Structure Reference

The full Information Architecture is defined in `config/ia_structure.yaml`.
The drafting agent must use the IA node IDs when suggesting page placement.

Top-level sections of the current IA:
1. Introduction to FlytBase
2. Getting Started with Your FlytBase Account
3. Navigating FlytBase
4. Maps and Overlays
5. Device Management
6. Pre-Flight Modules
7. Terrain and Altitude Visualization
8. In-Flight Modules
9. AVSS Parachute Integration
10. Post-Flight Modules
11. Flinks and Flows
12. Discover More

---

## Asset Path Scheme

All media assets committed to the docs repo follow this structure:

```
assets/
  {feature-slug}/
    {descriptive-name}.png
    {descriptive-name}.gif
    {descriptive-name}.mp4
```

Example: `assets/verkos-detection/baseline-comparison.png`

The feature slug must match the page slug in the IA structure where possible.

---

## Cross-Reference Rules

- When a new page relates to existing pages, add a cross-reference link in both directions.
- Use relative links within the docs repo, not absolute URLs.
- Format: `[Page Title](../relative/path.md)`
- Add cross-references in a "Related Pages" section at the bottom, or inline where contextually relevant.

---

## Memory System

The drafting agent must read `memory.md` and relevant memory files before generating content.
Memory files contain learned rules from reviewer feedback — corrections to voice, placement,
terminology, and feature-specific conventions that override defaults in this file.

Memory files live in `memory/` and are indexed by `memory.md` in the project root.

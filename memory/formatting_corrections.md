# Formatting Corrections

Rules learned from reviewer edits to formatting, structure, or layout.
Appended to by the memory updater when formatting is corrected in PRs.

---

- [2026-07-15] [formatting] The Constraints and Requirements section header and its table header must match. Always use "Components" and "Details" as the table column headers in the constraints table.
- [2026-07-15] [formatting] Whenever hardware compatibility is mentioned, always include a reference sentence and provide the URL to the hardware compatibility page.

- [2026-07-23] [formatting] Ensure table headings match the content they describe, such as using 'Components and Details' for a table with that content.

- [2026-08-19] [formatting] Release pages should be designed to be shown during onboarding calls, providing enough information to replace live demos.

- [2026-08-27] [formatting] MARKDOWN-ONLY RULE — hard constraint, not a style preference. The docs platform (FlytDocs) parses plain Markdown/CommonMark plus a small fixed set of GitBook {% %} block directives. ANY other raw HTML renders as literal visible text on the published page, or is silently dropped. Never emit raw inline HTML tags in body text: <img>, <a>, <strong>, <em>, <div>, <span>, <p>, <br>, <table>, or any other bare tag. Writing <strong>Warning</strong> puts the literal characters on the live page.
- [2026-08-27] [formatting] Images: use ![alt text](path) ONLY. Do NOT use <figure>/<figcaption> — this reverses the previous convention. An inline <img> mid-sentence is silently DROPPED entirely, not shown as an icon. Inline UI icons cannot be rendered at all on FlytDocs; describe them in words instead ("click the settings icon"). The descriptive caption goes in the alt text.
- [2026-08-27] [formatting] Links: [text](url) only. A bare <a href="...">text</a> renders as literal tag text, not a clickable link. Never add manual heading anchors like <a id="section"></a>.
- [2026-08-27] [formatting] Tables: standard Markdown pipe tables only, never a raw HTML <table>. GitBook's card-view table degrades to a plain table on FlytDocs. If a card grid of links is the intent, write a plain Markdown bullet list of links instead.
- [2026-08-27] [formatting] Frontmatter description must always have a value — either "description: One sentence." or the folded ">-" form with the text indented beneath. Never leave description empty.
- [2026-08-27] [formatting] These GitBook directives ARE supported and should be kept as-is: {% hint style="info|warning|danger|success" %}, {% content-ref %}, {% embed %}, {% tabs %}, and triple-backtick code fences. Note that FlytDocs flattens tabs into sequential sections, so don't rely on tabs to carry page structure.
- [2026-08-27] [formatting] Lists: only ONE level of nested indentation is preserved. Do not rely on deeper nesting to convey structure.
- [2026-08-27] [formatting] When unsure whether something will render, choose the plainest Markdown available and describe intent in words rather than reaching for a raw HTML tag.

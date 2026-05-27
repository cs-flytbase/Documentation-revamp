# FlytBase Documentation Pipeline — Team SOP

This document explains how to use the automated documentation pipeline from start to finish.
Follow every step carefully. If something looks wrong at any point, leave a comment on the PR and the pipeline will learn from it.

---

## What This Pipeline Does

When you push a feature bundle, the pipeline automatically:
1. Reads your PM document and assets
2. Generates a release note and a documentation page
3. Identifies existing pages that need to be updated due to this feature
4. Creates Pull Requests on both `flytbase-docs` and `flytbase-releases`
5. Once a PR is merged, GitBook updates automatically — no manual publishing needed

---

## Step 1 — Clone the Repo (One Time Only)

Do this once on your machine. You don't need to repeat this every time.

Open your terminal and run:

```bash
git clone https://github.com/cs-flytbase/Documentation-revamp.git
cd Documentation-revamp
```

If you've already cloned it before, just pull the latest changes:

```bash
cd Documentation-revamp
git pull origin main
```

---

## Step 2 — Create Your Input Bundle

Inside the repo, there is a folder called `input_bundles/`. This is where you put your feature content.

### Folder Structure

Create a new folder inside `input_bundles/` named after your feature using **lowercase letters and hyphens only** (no spaces, no underscores, no capital letters).

```
input_bundles/
└── your-feature-name/
    ├── pm_doc.md
    ├── youtube_link.txt
    ├── transcript.md          ← optional but recommended (Clueso transcript)
    └── assets/
        ├── feature-name-screenshot-1.png
        ├── feature-name-screenshot-2.png
        └── feature-name-workflow.gif
```

### Naming Rules — Read Carefully

**Folder name (your feature):**
- Use lowercase letters and hyphens only
- Should match the feature name closely
- Examples: `site-pointers`, `baseline-images`, `fleet-view-direct-launch`
- ❌ Wrong: `Site Pointers`, `site_pointers`, `SitePointers`
- ✅ Correct: `site-pointers`

**PM document:**
- Must be named exactly: `pm_doc.md`
- Must be a Markdown file (`.md`)
- ❌ Wrong: `PM Doc.docx`, `feature_doc.md`, `site-pointers-pm.md`
- ✅ Correct: `pm_doc.md`

**YouTube link file:**
- Must be named exactly: `youtube_link.txt`
- Contains only the full YouTube URL, nothing else
- ❌ Wrong: `video.txt`, `youtube.txt`, `link.txt`
- ✅ Correct: `youtube_link.txt`

Contents of `youtube_link.txt`:
```
https://www.youtube.com/watch?v=YOUR_VIDEO_ID
```

**YouTube link file:**
- Must be named exactly: `youtube_link.txt`
- Contains only the full YouTube URL, nothing else
- ❌ Wrong: `video.txt`, `youtube.txt`, `link.txt`
- ✅ Correct: `youtube_link.txt`

Contents of `youtube_link.txt`:
```
https://www.youtube.com/watch?v=YOUR_VIDEO_ID
```

**Clueso transcript (optional but recommended):**
- Must be named exactly: `transcript.md` or `transcript.txt`
- This is the transcript exported from Clueso after recording your feature demo video
- The pipeline uses it to extract detailed step-by-step instructions, exact UI element names, and edge cases that may not be in the PM doc
- The more detailed the transcript, the richer the generated documentation
- ❌ Wrong: `clueso.txt`, `video-transcript.md`, `demo-transcript.txt`
- ✅ Correct: `transcript.md` or `transcript.txt`

How to get the transcript from Clueso:
1. Open your Clueso project
2. Go to the transcript/captions section
3. Export as `.txt` or copy the full text
4. Save it as `transcript.md` inside your feature folder

**Assets folder:**
- Must be named exactly: `assets`
- ❌ Wrong: `Assets`, `images`, `screenshots`
- ✅ Correct: `assets`

**Asset file names:**
- Use lowercase letters and hyphens
- Start with the feature name so files stay organized
- Be descriptive about what the screenshot shows
- Examples:
  - `site-pointers-empty-state.png`
  - `site-pointers-add-pointer-modal.png`
  - `site-pointers-workflow.gif`
- Supported formats: `.png`, `.jpg`, `.jpeg`, `.gif`, `.mp4`

---

## Step 3 — Write the PM Document

The `pm_doc.md` file is the most important input. The better the PM doc, the better the generated content.

### What to Include

The PM doc should cover all of these:

- **What the feature is** — a clear description in plain English
- **What problem it solves** — real-world examples, not abstract statements
- **How it works** — the underlying mechanism (e.g. GPS radius, AI model, etc.)
- **Step-by-step usage** — how does an operator actually use it
- **Constraints and limitations** — what doesn't work, what the limits are
- **Hardware compatibility** — which docks/drones support this
- **Where screenshots go** — mark each screenshot with a tag like `[SCREENSHOT: site-pointers-workflow.png]` at the point where that image should appear in the doc

### Screenshot Markers

Inside `pm_doc.md`, mark where each screenshot should be placed:

```
## Step 1: Navigate to Site Pointers

Go to Verkos AI > Site Pointers in the left sidebar.

[SCREENSHOT: site-pointers-empty-state.png]

## Step 2: Add a Pointer
...
```

This tells the pipeline exactly where to embed each image.

---

## Step 4 — Push Your Bundle

Once your folder is ready, push it to the repo.

```bash
cd Documentation-revamp

# Make sure you're on main and up to date
git checkout main
git pull origin main

# Stage your new bundle
git add input_bundles/your-feature-name/

# Commit it
git commit -m "add input bundle: your-feature-name"

# Push
git push origin main
```

Replace `your-feature-name` with your actual folder name.

---

## Step 5 — Pipeline Runs Automatically

As soon as you push, GitHub Actions detects the new bundle and starts the pipeline automatically. You don't need to do anything.

To watch the progress:
1. Go to `https://github.com/cs-flytbase/Documentation-revamp/actions`
2. You'll see a run called **Documentation Pipeline** at the top
3. Click on it to watch the logs in real time
4. The run takes approximately **4-6 minutes**

### If You Want to Trigger It Manually

If the automatic trigger didn't fire, or you want to re-run:
1. Go to `https://github.com/cs-flytbase/Documentation-revamp/actions`
2. Click **Documentation Pipeline** in the left sidebar
3. Click **Run workflow** (top right)
4. In the **bundle_name** field, type your feature folder name exactly (e.g. `site-pointers`)
5. Click **Run workflow**

---

## Step 6 — Review the Pull Requests

Once the pipeline finishes, two PRs are automatically created:

| PR | Repo | Contains |
|---|---|---|
| Release Note PR | `FlytBaseAILabs/flytbase-releases` | New release note + patches to existing release pages |
| Doc Page PR | `FlytBaseAILabs/flytbase-docs` | New doc page + patches to existing doc pages |

You will find them at:
- `https://github.com/FlytBaseAILabs/flytbase-releases/pulls`
- `https://github.com/FlytBaseAILabs/flytbase-docs/pulls`

### What to Check in the PR

Each PR description tells you:
- **New file** — the newly generated page
- **Existing pages updated** — which older pages were patched and why
- **Review checklist** — what to verify before merging

Go through the checklist:
- [ ] Content covers everything in the PM doc
- [ ] All images render correctly (click **Preview** tab)
- [ ] Tone and terminology match existing pages
- [ ] Patched sections read naturally in their existing pages

---

## Step 7 — Merge or Give Feedback

### If Everything Looks Good
Click **Merge pull request** → GitBook updates automatically within a few minutes.

### If Something Needs to Be Fixed

**Minor fix** (wrong word, small formatting issue):
- Edit the file directly on GitHub using the pencil icon
- Commit the change to the same branch
- Then merge

**Structural issue** (wrong tone, missing section, wrong placement):
- Leave a comment on the PR explaining what's wrong
- The pipeline automatically picks up your comment, learns from it, and applies the correction next time
- Example comments:
  - `The release note leads with the feature name instead of the problem. It should start with the operator pain point.`
  - `The constraints section is missing the GPS radius limitation mentioned in the PM doc.`
  - `Doc page overview is too short, needs to explain how the AI learning mechanism works.`

You do not need any special command or prefix — just write a normal comment explaining the issue.

---

## Quick Reference — File Naming Cheatsheet

| File / Folder | Required Name | Required? | Example |
|---|---|---|---|
| Feature folder | `your-feature-name` (lowercase, hyphens) | ✅ Yes | `site-pointers` |
| PM document | `pm_doc.md` | ✅ Yes | `pm_doc.md` |
| YouTube link | `youtube_link.txt` | ✅ Yes | `youtube_link.txt` |
| Clueso transcript | `transcript.md` or `transcript.txt` | ⭐ Recommended | `transcript.md` |
| Assets folder | `assets` | ✅ Yes | `assets` |
| Asset files | `feature-name-description.ext` | ✅ Yes | `site-pointers-workflow.gif` |

---

## Quick Reference — Full Folder Structure

```
input_bundles/
└── site-pointers/                    ← feature folder (your feature name)
    ├── pm_doc.md                     ← PM document (always this name)
    ├── youtube_link.txt              ← YouTube URL (always this name)
    ├── transcript.md                 ← Clueso transcript (recommended)
    └── assets/                       ← assets folder (always this name)
        ├── site-pointers-empty-state.png
        ├── site-pointers-add-pointer-modal.png
        ├── site-pointers-populated-list.png
        └── site-pointers-workflow.gif
```

---

## FAQs

**Q: Is the Clueso transcript mandatory?**
No, it is optional. But it significantly improves the quality of the generated documentation — especially the step-by-step sections and the How It Works explanation. If you have a Clueso recording for the feature, always export the transcript and include it.

**Q: What if I don't have a YouTube link yet?**
Create the `youtube_link.txt` file but leave it empty. The pipeline will skip the embed and generate content without it.

**Q: What if I have more than 4 screenshots?**
Include all of them in the assets folder. The pipeline will place all of them. More screenshots = more detailed documentation.

**Q: Can I update a bundle after pushing it?**
Yes. Add your changes, commit, and push again. Then manually trigger the workflow from the Actions tab with your bundle name.

**Q: The pipeline ran but the PR looks wrong. What do I do?**
Leave a comment on the PR describing what's wrong. Close the PR. The pipeline will learn from the feedback. Push the bundle again (or manually trigger) to generate a new PR with the correction applied.

**Q: How long until GitBook updates after merging?**
Usually within 1-2 minutes. GitBook syncs automatically when a PR is merged to the branch.

**Q: Do I need to update SUMMARY.md or any navigation files?**
Not right now — this will be automated in a future update. For now, after merging, manually add the new page to `SUMMARY.md` in the relevant repo if it doesn't appear in the GitBook navigation.

# Master Prompt for Claude: Comprehensive Audit & Overhaul of Semantic SEO Tool

> **تعليمات الاستخدام:**
> 1. ارفع ملف `semantic-seo-core.rar` الموجود في مجلد المشروع إلى شات Claude.
> 2. انسخ البرومبت الموجود بالأسفل بالكامل والصقه في الرسالة.

---

```markdown
# Role & Mission
You are a Principal Python Architect, Senior Full-Stack Engineer, and World-Class Semantic SEO & NLP Specialist (former Google Search Quality & Knowledge Graph Architect).

You have been provided with the complete, clean source code of the **Semantic SEO Tool** (`semantic-seo-core.rar`).
Your mission is to perform a rigorous, **file-by-file comprehensive architectural audit and complete code overhaul** of the entire codebase to achieve **maximum SEO precision, bulletproof content extraction, flawless entity analysis, and zero runtime errors**.

---

## 🚨 Critical Areas That Require Immediate Treatment:

### 1. Web Scraping & Content Extraction Overhaul (`services/extraction.py`)
Currently, the scraping and text extraction engine suffers from severe issues:
1. **Total Failures on Protected/Modern Sites:** Some URLs fail completely or return empty shells (Cloudflare/WAF 403, bot mitigation, Single Page Applications / client-rendered React/Vue/Next.js).
2. **Scrambled & Out-of-Order Reading Sequence:** On many sites, text is extracted in an illogical order (e.g., sidebars, navigation widgets, or footer links are extracted before or intermingled with article paragraphs). The reading flow of the main article is broken.
3. **Partial Extraction (Missing Sections):** Some pages are only partially extracted (e.g., lazy-loaded text, accordions, tabs, Salla/Zid/Shopify product blocks, or paywall/read-more snippets are skipped; over-aggressive noise filters drop legitimate article sections like reviews, tables, or FAQs).

**Your Required Fixes:**
- Re-architect `services/extraction.py` to use a true **document reading-order tree traversal** that strictly respects DOM sequence within the primary content container.
- Implement an intelligent multi-tier extraction hierarchy:
  - **Tier 1:** High-fidelity static fetch with rotating modern browser headers, robust charset detection (fixing Arabic mojibake), and precision boilerplate removal (`trafilatura` in XML/structural mode + Readability DOM scoring).
  - **Tier 2:** Deep hydration extraction (Next.js `__NEXT_DATA__`, Nuxt `__NUXT__`, JSON-LD schema, Salla/Zid product state).
  - **Tier 3:** Graceful fallback for client-rendered / WAF-protected pages (headless browser or Reader fallback).
- Ensure headings (`[H1]`, `[H2]`, `[H3]`), lists (`•`), and structured tables (`[جدول]`) maintain their exact hierarchical position relative to their body paragraphs.
- Zero data loss for Arabic content.

---

### 2. Deep & Logical Entity Analysis (`services/semantic.py`, `services/quality.py`, `services/claude.py`)
The entity extraction must be mathematically sound, logically structured, and tailored for advanced Semantic SEO:
1. **Disambiguation & Salience:** Extract entities with genuine contextual salience (not mere keyword frequency). Calculate importance based on placement (Title, H1, H2, First Paragraph) and topical necessity.
2. **Semantic Clustering:** Group entities into meaningful clusters:
   - **Core Entities (الكيانات المحورية):** The foundational concepts required to rank.
   - **Contextual/Supporting Entities (الكيانات الداعمة):** Subtopics that establish topical depth.
   - **LSI & Synonyms (المرادفات الدلالية):** Natural linguistic variations and related search queries.
3. **Arabic Morphological Precision:** Handle Arabic prefixation (ال، و، ف، ب، ك، ل) and orthographic variations (أ، إ، آ -> ا | ة -> ه | ى -> ي) accurately during matching without destroying root meanings or brand names.
4. **Gap Analysis Quality:** Avoid flagging single-competitor trivial mentions as critical gaps. Prioritize gaps based on independent competitor consensus and search intent relevance.

---

### 3. World-Class Content Brief & Strategy Generation (`services/content_brief.py`, `prompts/`)
The generated Content Brief must represent the gold standard of SEO content architecture:
- Deep Search Intent mapping (Informational, Commercial, Navigational, Transactional).
- Comprehensive E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) directives.
- Exhaustive H2/H3 outline assigning specific entities to each section.
- Clear word-count targets, FAQ schema sections, and internal linking strategies.

---

### 4. Zero-Error Code Integrity & Multi-Engine / Claude Routing
- Review `app.py`, `config.py`, `routes/analyze.py`, and `services/claude.py`.
- Ensure **Claude 3.7 Sonnet (`claude-3-7-sonnet-20250219`)** is leveraged with hybrid reasoning, prompt caching, deterministic temperature (`0.1`), and 8,192 token output limit.
- Ensure embedded backend API key support via `.env` works seamlessly with zero leaks to the client.
- Eliminate every bug, unhandled exception, broken import, or edge-case crash across the entire project.

---

## 📋 Required Output:
Please structure your response as follows:
1. **Architectural Diagnosis & Root Causes:** A precise technical diagnosis of why scraping and extraction previously failed or produced out-of-order text.
2. **Step-by-Step Overhaul Plan:** The exact files modified and why.
3. **Complete, Drop-In Code Replacements:** Provide the complete, production-ready source code for every file you modify (do not use placeholders or omit lines). Start with `services/extraction.py`, followed by `services/semantic.py`, `services/claude.py`, and any others needed.
4. **Verification & Testing Guide:** Exact commands to verify the fixes and ensure zero errors across all unit tests.
```

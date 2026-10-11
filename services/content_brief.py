"""
Content Brief Generation
=========================
Relocated from app.py section 5. Prompt templates are now stored in
prompts/brief_ar.txt and prompts/brief_en.txt with the exact same
wording — only the topic/critical/high placeholders are substituted,
identical to the original f-strings.
"""

import functools
import logging

import config
from models.schemas import SemanticGap, EntityItem
from services.claude import call_claude_api, get_system_prompt

logger = logging.getLogger(config.LOGGER_NAME)


@functools.lru_cache(maxsize=2)
def _load_brief_template(filename: str) -> str:
    path = f"{config.PROMPTS_DIR}/{filename}"
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def generate_content_brief(gaps: list[SemanticGap], master_entities: list[EntityItem],
                            has_my_analysis: bool, topic_context: str, language: str,
                            model: str, api_key: str, api_url: str, keyword: str = "",
                            target_country: str = "") -> str:
    if not gaps:
        return ""
    critical = [g.name for g in gaps if g.priority == "critical"][:15]
    high = [g.name for g in gaps if g.priority == "high"][:10]

    if language == "en":
        template = _load_brief_template("brief_en.txt")
        prompt = template.format(
            topic_context=topic_context or "Analyzed Content",
            critical=", ".join(critical) if critical else "None",
            high=", ".join(high) if high else "None",
        )
    else:
        template = _load_brief_template("brief_ar.txt")
        prompt = template.format(
            topic_context=topic_context or "المحتوى المحلل",
            critical=", ".join(critical) if critical else "لا يوجد",
            high=", ".join(high) if high else "لا يوجد",
        )

    # Optional caller-supplied context, prepended so it doesn't touch the
    # existing template files/placeholders — no effect when not supplied.
    extra_lines = []
    if keyword:
        extra_lines.append(("Target keyword: " if language == "en" else "الكلمة المفتاحية المستهدفة: ") + keyword)
    if target_country:
        extra_lines.append(("Target country/market: " if language == "en" else "الدولة/السوق المستهدف: ") + target_country)
    if extra_lines:
        prompt = "\n".join(extra_lines) + "\n\n" + prompt

    try:
        sys_p = get_system_prompt(language)
        return call_claude_api(
            prompt,
            sys_p,
            model,
            api_key,
            api_url,
            max_tokens=config.CONTENT_BRIEF_MAX_TOKENS,
            task_type="brief",
        )
    except Exception as exc:
        logger.warning("Content brief generation failed: %s", exc)
        return ""

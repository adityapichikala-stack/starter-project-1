"""Step 1b (LLM): a second, independent read that cross-checks each extracted value against the email.

It returns verdicts and quotes only. It cannot change values or the status; code decides what to do
with its verdicts (see app/steps/extraction_loop.py).
"""
import json

from app.email_parser import ParsedEmail
from app.llm_client import LLMClient
from app.models import ExtractedOrder, Verification
from app.steps.extract import FIELD_RULES, UNTRUSTED, build_user_message

SYSTEM_PROMPT = f"""You are a strict reviewer. Another system extracted order details from a customer \
email. Check every extracted value against the email.

{UNTRUSTED}

The extraction must follow these rules:
{FIELD_RULES}

For each field give a verdict:
- correct: true only if the value follows the rules and the email supports it. A null value is correct \
only if the email really does not give that value. A value that is present but not the right one \
(an area instead of the city, a person instead of the organisation, the wrong date) is not correct.
- evidence: the shortest exact quote from the email that supports the value (copy it character for \
character). null when the value is null.
- problem: if not correct, say briefly what is wrong and what the email actually says.
Ignore differences in capitalisation, spacing and punctuation: code already normalises those, so \
they are never a problem. Only flag a value that is wrong in substance.
For items, judge product_name and quantity together, one verdict per extracted item in the same order.
In missing_items, list any product the email orders that the extraction left out.
Check deadlines carefully against the email date, including relative dates and weekdays."""


def verify_extraction(llm: LLMClient, parsed: ParsedEmail, extracted: ExtractedOrder) -> Verification:
    user = (
        build_user_message(parsed)
        + "\n\nExtraction to check:\n"
        + json.dumps(extracted.model_dump(), ensure_ascii=False, indent=2)
    )
    return llm.parse(
        "verify",
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        Verification,
    )

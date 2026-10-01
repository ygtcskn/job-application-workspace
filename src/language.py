"""Choose the application language from the job ad: German ads get a German CV and cover letter."""
import re

LANGUAGES = {"en": "English", "de": "German"}
# Frequent function words that do not also occur in the other language (no "in", "an", "was", "will").
GERMAN = frozenset("und der die das den dem des ist sind wir sie ihr ihre ihren mit für von zu zur zum auf im "
                   "eine einer einen ein nicht werden wird bei als auch oder sich du dich dein deine unser unsere "
                   "über aus".split())
ENGLISH = frozenset("the and is are we you your with for of to on our at or this that have has be as from by it".split())


def detect_language(ad_text: str) -> str:
    """'de' when German function words outnumber English ones in the ad body, otherwise 'en'."""
    # LinkedIn imports put English header fields (Company:, Location:) above this line.
    body = re.split(r"^\s*Job description\s*$", ad_text, maxsplit=1, flags=re.M | re.I)[-1]
    words = re.findall(r"\w+", body.lower())
    german = sum(word in GERMAN for word in words)
    english = sum(word in ENGLISH for word in words)
    return "de" if german > english else "en"

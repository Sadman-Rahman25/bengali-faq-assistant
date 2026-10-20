"""ui_strings.py -- every interface string, in English and in Bengali.

ONE TABLE. app.py and quiz_app.py hold no interface text of their own: a
tab name, a button, a card state, an empty state, the footer note and every
error lead-in come from here through t(). A string typed anywhere else is a
string the language switch cannot reach, and it shows up as one English
sentence on an otherwise Bengali page.

WHAT IS NOT IN HERE, deliberately:
  - ANSWERS. They come from the model, in the language of the question,
    with roles, products and clause numbers in Bengali either way. That rule
    lives in generate.py and the switch does not touch it.
  - DATA. Manual titles, clause numbers, product names, chunk ids, model
    names and file paths are values read from the manifest, the registry or
    the configuration. They are shown as they are in both languages.
  - UPSTREAM ERROR TEXT. A provider error or a token-budget refusal arrives
    from generate.py as an English sentence. The lead-in around it is
    translated; the sentence itself is shown as received, because rewording
    a diagnostic is how it stops matching the log it came from.

PRODUCT NAMES ARE NOT TYPED HERE (Convention 2). Where a string names a
programme, it carries {dabi} / {progoti} and the caller fills them from
productmatch.PROGRAMME_NAMES -- see programme_names().

BENGALI. Spelling follows the Prothom Alo style the brand guide names
(BRAND.md 10.2 -- not on this machine when this was written; see journal
4m). Numbers in Bengali text are Bengali numerals: t() converts every int
or float it is given, and num() converts a number that has already been
formatted as a string. Identifiers that merely contain digits -- a model
name, a chunk id -- are passed as strings and are never converted.
"""

LANGS = ("en", "bn")
DEFAULT_LANG = "en"

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

STRINGS = {
    # ---------------------------------------------------------- page frame
    "page_title": {
        "en": "BRAC Field Assistant",
        "bn": "ব্র্যাক মাঠকর্মী সহায়ক"},
    "logo_alt": {"en": "BRAC", "bn": "ব্র্যাক"},
    "title": {
        "en": "Field Assistant",
        "bn": "মাঠকর্মী সহায়ক"},
    "subtitle": {
        "en": "Dabi and Progoti operations manuals",
        "bn": "{dabi} ও {progoti} অপারেশন ম্যানুয়াল"},

    # ------------------------------------------------------------ switches
    "lang_label": {"en": "Language", "bn": "ভাষা"},
    "lang_en": {"en": "English", "bn": "English"},
    "lang_bn": {"en": "বাংলা", "bn": "বাংলা"},
    "theme_label": {"en": "Display", "bn": "প্রদর্শন"},
    "theme_light": {"en": "Light", "bn": "লাইট"},
    "theme_dark": {"en": "Dark", "bn": "ডার্ক"},

    # ---------------------------------------------------------- stat cards
    "stat_chunks": {"en": "indexed chunks", "bn": "সূচিভুক্ত অংশ"},
    "stat_manuals": {"en": "manuals", "bn": "ম্যানুয়াল"},
    "stat_violations": {
        "en": "forbidden-figure violations",
        "bn": "নিষিদ্ধ সংখ্যা ব্যবহারের ঘটনা"},
    "stat_citation": {"en": "citation accuracy", "bn": "সূত্র উল্লেখের নির্ভুলতা"},
    "stat_citation_note": {
        "en": "on achievable questions ({hit}/{scored})",
        "bn": "উত্তরযোগ্য প্রশ্নে ({hit}/{scored})"},

    # ---------------------------------------------------------------- tabs
    "tab_ask": {"en": "Ask anything", "bn": "প্রশ্ন করুন"},
    "tab_faq": {"en": "50 FAQ", "bn": "৫০টি সাধারণ প্রশ্ন"},
    "tab_quiz": {"en": "Test yourself", "bn": "নিজেকে যাচাই করুন"},

    # -------------------------------------------------------- ask controls
    "mode_label": {
        "en": "How would you like to ask?",
        "bn": "কীভাবে প্রশ্ন করবেন?"},
    "mode_browse": {
        "en": "Browse gold questions",
        "bn": "যাচাই-সেটের প্রশ্ন দেখুন"},
    "mode_own": {"en": "Ask your own", "bn": "নিজের প্রশ্ন লিখুন"},
    "gold_select": {
        "en": "{n} gold questions – the evaluation set",
        "bn": "যাচাই-সেটের {n}টি প্রশ্ন – মূল্যায়নের জন্য"},
    "gold_placeholder": {
        "en": "Choose a question to put it in the box",
        "bn": "একটি প্রশ্ন বাছুন, সেটি নিচের ঘরে বসবে"},
    "gold_mark_no": {
        "en": "[no answer in the manual]",
        "bn": "[ম্যানুয়ালে উত্তর নেই]"},
    "gold_mark_partial": {
        "en": "[partly answerable]",
        "bn": "[আংশিক উত্তর আছে]"},
    "input_label": {
        "en": "Ask a question – Bengali or Banglish",
        "bn": "প্রশ্ন লিখুন – বাংলায়, বা ইংরেজি হরফে বাংলায়"},
    # Banglish in both languages, and not Bengali: see render_ask().
    "input_placeholder": {
        "en": "progoti loner service charge koto?",
        "bn": "progoti loner service charge koto?"},
    "submit": {"en": "Search the manuals", "bn": "ম্যানুয়ালে খুঁজুন"},
    "spinner": {"en": "Searching the manuals…", "bn": "ম্যানুয়ালে খোঁজা হচ্ছে…"},

    # ------------------------------------------------------- token budget
    "budget_head": {
        "en": "Token budget nearly spent.",
        "bn": "আজকের টোকেন-সীমা প্রায় শেষ।"},
    "budget_body": {
        "en": ("{used} of {limit} tokens used today on {model}. The limit "
               "is per model per day; a query that will not fit is refused "
               "before it is sent, not part-way through."),
        "bn": ("আজ {model} মডেলে {limit}টি টোকেনের মধ্যে {used}টি ব্যবহার "
               "হয়েছে। এই সীমা প্রতিটি মডেলের জন্য দৈনিক। যে প্রশ্ন সীমার "
               "মধ্যে ধরবে না, তা পাঠানোর আগেই ফিরিয়ে দেওয়া হয়, মাঝপথে নয়।")},

    # --------------------------------------------------------- answer card
    "answer_label": {"en": "Answer", "bn": "উত্তর"},
    "empty": {
        "en": ("Ask about a product, a clause or an approval limit. Answers "
               "are drawn from the Dabi and Progoti operations manuals only."),
        "bn": ("কোনো পণ্য, ধারা বা অনুমোদনের সীমা নিয়ে জানতে চান। উত্তর "
               "শুধু {dabi} ও {progoti} অপারেশন ম্যানুয়াল থেকে নেওয়া হয়।")},

    # THE FOURTH CARD STATE: the machine, not the index. See ask().
    "infra_head": {
        "en": ("No answer. The machine ran out of memory before the "
               "question could be searched."),
        "bn": ("উত্তর নেই। প্রশ্নটি খোঁজার আগেই কম্পিউটারের মেমরি "
               "ফুরিয়ে গেছে।")},
    "infra_flag_head": {
        "en": "Nothing is wrong with the manuals or the index.",
        "bn": "ম্যানুয়াল বা সূচিতে কোনো সমস্যা নেই।"},
    "infra_flag_body": {
        "en": ("Loading the search model needs more memory than this machine "
               "can commit right now. Two things help, and on this machine "
               "the second is usually the one that works:<br>"
               "&nbsp;&nbsp;{one}. Close other applications and reload the "
               "page.<br>"
               "&nbsp;&nbsp;{two}. Raise the Windows page file, then restart."
               "<br>The limit here is the <b>commit ceiling</b>, not free "
               "RAM – at the last failure only {resident} GB was actually "
               "resident against a {ceiling} GB commit limit, so closing "
               "applications on its own may not free enough. The question was "
               "not sent to any model, and nothing was retrieved."),
        "bn": ("খোঁজার মডেলটি চালু করতে যতটা মেমরি লাগে, এই কম্পিউটার এখন "
               "ততটা দিতে পারছে না। দুটি উপায়ে কাজ হয়; এই কম্পিউটারে সাধারণত "
               "দ্বিতীয়টিতেই হয়:<br>"
               "&nbsp;&nbsp;{one}. অন্য অ্যাপ্লিকেশন বন্ধ করে পাতাটি আবার "
               "খুলুন।<br>"
               "&nbsp;&nbsp;{two}. উইন্ডোজের পেজ ফাইল বাড়িয়ে কম্পিউটার আবার "
               "চালু করুন।<br>এখানে সীমাটি খালি র‍্যামের নয়, <b>কমিট "
               "সীমার</b> – গতবার ব্যর্থ হওয়ার সময় {ceiling} জিবি কমিট "
               "সীমার বিপরীতে মাত্র {resident} জিবি আসলে ব্যবহৃত ছিল। তাই শুধু "
               "অ্যাপ্লিকেশন বন্ধ করলে যথেষ্ট জায়গা না-ও হতে পারে। প্রশ্নটি "
               "কোনো মডেলে পাঠানো হয়নি, কিছু খোঁজাও হয়নি।")},
    "infra_note_head": {
        "en": "Recorded as an infrastructure failure.",
        "bn": "পরিকাঠামোর ত্রুটি হিসেবে লেখা হয়েছে।"},
    "infra_note_body": {
        "en": ("The meta row for {id} carries {flag}, so this cannot be read "
               "later as a refusal – a refusal is a decision the system made, "
               "and this is not one."),
        "bn": ("{id}-এর মেটা সারিতে {flag} লেখা আছে, তাই পরে এটিকে উত্তর "
               "দিতে অস্বীকার বলে পড়া যাবে না। অস্বীকার হলো সিস্টেমের নেওয়া "
               "সিদ্ধান্ত; এটি তা নয়।")},

    "error_head": {
        "en": "No answer. The provider was not called.",
        "bn": "উত্তর নেই। মডেল সেবায় প্রশ্ন পাঠানো হয়নি।"},
    "error_reason": {"en": "Reason.", "bn": "কারণ।"},
    # Shown with the reason exactly as generate.py gave it; see the docstring.
    "error_untranslated": {
        "en": "",
        "bn": "(নিচের বার্তাটি সিস্টেম থেকে যেমন এসেছে তেমনই, ইংরেজিতে।)"},

    # The floor and the post-filter. Formerly FLOOR_NOTE_BN / FILTER_NOTE_BN,
    # Bengali only; the Bengali is unchanged.
    "floor_note": {
        "en": ("Nothing in the manuals matches this question. Try again with "
               "the product name or the clause number."),
        "bn": ("ম্যানুয়ালে এই প্রশ্নের সাথে মেলে এমন কিছু পাওয়া যায়নি। "
               "পণ্যের নাম বা ধারা নম্বর লিখে আবার চেষ্টা করুন।")},
    "filter_note": {
        "en": ("A reliable answer to this question cannot be given, so no "
               "figure is shown."),
        "bn": ("এই প্রশ্নের নির্ভরযোগ্য উত্তর দেওয়া যাচ্ছে না, "
               "তাই কোনো সংখ্যা দেখানো হয়নি।")},
    # Shown when the hosted embedder cannot be reached or the day's quota is
    # spent. It says what the reader can do: "try again later" is
    # actionable; a 429 is not. Formerly EMBEDDER_DOWN_BN.
    "embedder_down": {
        "en": ("The service cannot be used at the moment. Please try again "
               "in a little while."),
        "bn": ("এই মুহূর্তে সেবাটি ব্যবহার করা যাচ্ছে না। "
               "কিছুক্ষণ পর আবার চেষ্টা করুন।")},
    "abstain_head": {
        "en": "The model read the clauses and found no answer in them.",
        "bn": "মডেল ধারাগুলো পড়েছে, কিন্তু সেখানে উত্তর পায়নি।"},
    "abstain_body": {
        "en": ("This is a refusal, not a blank. The clauses below were "
               "retrieved and read; they do not carry the figure asked for. "
               "Open them and judge for yourself."),
        "bn": ("এটি ইচ্ছাকৃতভাবে উত্তর না দেওয়া, ফাঁকা উত্তর নয়। নিচের "
               "ধারাগুলো খুঁজে এনে পড়া হয়েছে; যে সংখ্যা চাওয়া হয়েছে, তা "
               "সেগুলোতে নেই। খুলে দেখে নিজে বিচার করুন।")},

    "filter_skip_head": {
        "en": "Post-filter not armed.",
        "bn": "পরবর্তী যাচাই চালু ছিল না।"},
    "filter_skip_off": {"en": "The post-filter is off", "bn": "পরবর্তী যাচাই বন্ধ"},
    "filter_skip_nogold": {
        "en": "The question is not in the gold file",
        "bn": "প্রশ্নটি যাচাই-সেটে নেই"},
    "filter_skip_body": {
        "en": ("{reason}. It holds per-question constraints and has none for "
               "this question, so it could not have fired. This is not a "
               "clean pass."),
        "bn": ("{reason}। এই যাচাই প্রতিটি প্রশ্নের জন্য আলাদা শর্ত দিয়ে চলে, "
               "আর এই প্রশ্নের জন্য কোনো শর্ত নেই, তাই এটি কাজ করার সুযোগই "
               "পায়নি। একে নির্ভুল ফল বলে ধরবেন না।")},

    "status_head": {"en": "Product status.", "bn": "পণ্যের অবস্থা।"},
    # The registry's own status values (data/registry/README.md), labelled.
    "status_active": {"en": "active", "bn": "চালু"},
    "status_suspended": {"en": "suspended", "bn": "স্থগিত"},
    "status_pilot": {"en": "pilot", "bn": "পাইলট"},
    "status_unknown": {
        "en": "unknown – not in the registry",
        "bn": "অজানা – নিবন্ধনে নেই"},
    "status_note": {
        "en": ("From the field team's product registry: {populated} of "
               "{total} products have a status. Status is read at question "
               "time, so a suspended product still shows its terms – the "
               "terms bind existing clients either way."),
        "bn": ("মাঠ দলের পণ্য নিবন্ধন থেকে: {total}টি পণ্যের মধ্যে "
               "{populated}টির অবস্থা লেখা আছে। অবস্থা প্রশ্ন করার সময় পড়া "
               "হয়, তাই স্থগিত পণ্যের শর্তও দেখা যায় – বর্তমান গ্রাহকদের "
               "জন্য সেই শর্ত যেভাবেই হোক প্রযোজ্য।")},

    "meta_line": {
        "en": ("Answered by {model} · provider {provider} · k={k} · top fused "
               "{top} · {prompt} prompt + {output} output tokens · meta row "
               "{id} in {path}"),
        "bn": ("উত্তর দিয়েছে {model} · সেবা {provider} · k={k} · সর্বোচ্চ "
               "স্কোর {top} · {prompt} প্রম্পট + {output} আউটপুট টোকেন · "
               "{path}-এ মেটা সারি {id}")},
    "meta_no_model": {
        "en": "no model – the floor fired",
        "bn": "কোনো মডেল নয় – ন্যূনতম স্কোরে পৌঁছায়নি"},

    # ------------------------------------------------------ retrieved list
    "chunks_title": {"en": "Retrieved chunks", "bn": "খুঁজে পাওয়া অংশ"},
    "orphan_head": {
        "en": "Cited but not retrieved.",
        "bn": "উল্লেখ আছে, কিন্তু খুঁজে পাওয়া যায়নি।"},
    "orphan_body": {
        "en": ("named in the answer, but no retrieved chunk carries that "
               "clause. Either the model invented it, or it belongs to a "
               "manual this search did not reach. Treat the answer as "
               "unsupported on that point."),
        "bn": ("উত্তরে নাম আছে, কিন্তু খুঁজে পাওয়া কোনো অংশে এই ধারা নেই। "
               "হয় মডেল এটি বানিয়েছে, নয়তো এটি এমন ম্যানুয়ালের, যেখানে এই "
               "খোঁজ পৌঁছায়নি। এই বিষয়ে উত্তরটিকে সমর্থনহীন ধরুন।")},
    "chunks_none": {"en": "Nothing retrieved yet.", "bn": "এখনো কিছু খোঁজা হয়নি।"},
    "chunks_infra": {
        "en": "Retrieval never ran – the search model could not be loaded.",
        "bn": "খোঁজা হয়নি – খোঁজার মডেলটি চালু করা যায়নি।"},
    "chunks_embedder": {
        "en": "Retrieval never ran – the search service could not be reached.",
        "bn": "খোঁজা হয়নি – খোঁজার সেবায় পৌঁছানো যায়নি।"},
    "no_clause": {"en": "no clause number", "bn": "ধারা নম্বর নেই"},
    "cited": {"en": "CITED", "bn": "উল্লেখিত"},
    "fused": {"en": "fused {score}", "bn": "স্কোর {score}"},

    # ------------------------------------------------------------- the FAQ
    # PHOTO ALT TEXT, keyed "<PHOTOS key>_photo_alt" (app.photo_html). What
    # is IN the photo, not what it is meant to say: a screen-reader user
    # should learn what a sighted reader sees.
    "enterprise_photo_alt": {
        "en": ("Two people lean across a long table in a textile workshop, "
               "smoothing a length of yellow fabric together. One of them is "
               "smiling."),
        "bn": ("একটি কাপড়ের কারখানায় লম্বা টেবিলের ওপর ঝুঁকে দুজন মানুষ "
               "একসঙ্গে একটি হলুদ কাপড় সমান করছেন। তাঁদের একজন হাসছেন।")},
    "river_photo_alt": {
        "en": ("A woman, seen from behind, rows a wooden boat along a narrow "
               "river between muddy banks."),
        "bn": ("পেছন থেকে দেখা একজন নারী কাদামাটির পাড়ের মাঝে একটি সরু নদীতে "
               "কাঠের নৌকা বাইছেন।")},
    # PLACEHOLDER, for a photo not yet supplied. Rewrite it to describe the
    # actual picture when brac-members.webp arrives; until then it is never
    # shown, because the slot draws nothing without the file.
    "members_photo_alt": {
        "en": "Members sitting together at a group meeting.",
        "bn": "একটি দলীয় সভায় সদস্যরা একসঙ্গে বসে আছেন।"},
    "faq_state": {"en": "Not started", "bn": "শুরু হয়নি"},
    "faq_head": {
        "en": "Fifty questions field officers actually ask, with reviewed answers.",
        "bn": "মাঠকর্মীরা আসলে যে পঞ্চাশটি প্রশ্ন করেন, যাচাই করা উত্তরসহ।"},
    "faq_body": {
        "en": ("The {n} questions in the evaluation set were written by "
               "reading chunks, so they test retrieval mechanics rather than "
               "what an officer walks in with. This surface waits on the field "
               "questions being collected and their answers checked against "
               "the printed manual by someone who has it."),
        "bn": ("মূল্যায়ন সেটের {n}টি প্রশ্ন ম্যানুয়ালের অংশ পড়ে লেখা, তাই "
               "সেগুলো খোঁজার কৌশল যাচাই করে, মাঠকর্মী আসলে কী জানতে চান তা "
               "নয়। মাঠ থেকে প্রশ্ন সংগ্রহ এবং ছাপা ম্যানুয়াল হাতে আছে এমন "
               "কারও দিয়ে উত্তর যাচাইয়ের পর এই অংশ চালু হবে।")},

    # ------------------------------------------------------------ the quiz
    "quiz_no_bank": {"en": "No bank", "bn": "প্রশ্নভান্ডার নেই"},
    "quiz_no_bank_body": {
        "en": "Nothing at {path}. Run {cmd} first.",
        "bn": "{path}-এ কিছু নেই। আগে {cmd} চালান।"},
    # The bank is there but no question is currently reviewed by a person:
    # waiting, not missing, so nothing here says to rebuild anything.
    "quiz_awaiting_review": {"en": "Waiting for review",
                             "bn": "পর্যালোচনার অপেক্ষায়"},
    "quiz_awaiting_review_body": {
        "en": ("The bank holds {total} questions, but none has been reviewed "
               "by a person in its current form. A question appears here once "
               "someone has read it and marked it correct. Nothing needs "
               "rebuilding."),
        "bn": ("ভান্ডারে {total}টি প্রশ্ন আছে, কিন্তু বর্তমান রূপে কোনোটিই "
               "এখনো কেউ পর্যালোচনা করেননি। কেউ পড়ে সঠিক বলে চিহ্নিত করার "
               "পর প্রশ্নটি এখানে দেখা যাবে। নতুন করে কিছু তৈরি করার দরকার "
               "নেই।")},
    "quiz_intro": {
        "en": ("{n} questions, drawn at random from {total} in the bank, with "
               "no more than {cap} sharing a correct answer. Answers come from "
               "the manuals; nothing is generated while you take this."),
        "bn": ("ভান্ডারের {total}টি প্রশ্ন থেকে এলোমেলোভাবে {n}টি নেওয়া "
               "হয়েছে; একই সঠিক উত্তর {cap}টির বেশি প্রশ্নে নেই। উত্তর "
               "ম্যানুয়াল থেকে নেওয়া; আপনি উত্তর দেওয়ার সময় কিছুই তৈরি করা "
               "হয় না।")},
    "quiz_short_head": {
        "en": "{n} questions, not {want}.",
        "bn": "{want}টি নয়, {n}টি প্রশ্ন।"},
    "quiz_short_body": {
        "en": ("At most {cap} questions may share a correct answer, and the "
               "{total} in the bank support only {capacity} under that limit. "
               "The bank needs more distinct answers, not a bigger draw."),
        "bn": ("একই সঠিক উত্তর সর্বোচ্চ {cap}টি প্রশ্নে থাকতে পারে, আর এই "
               "সীমায় ভান্ডারের {total}টি প্রশ্ন থেকে মাত্র {capacity}টি "
               "নেওয়া যায়। ভান্ডারে আরও ভিন্ন উত্তর দরকার, বড় ড্র নয়।")},
    "quiz_question": {"en": "Question {i}", "bn": "প্রশ্ন {i}"},
    "quiz_submit": {"en": "Submit answers", "bn": "উত্তর জমা দিন"},
    "quiz_score": {"en": "Your score", "bn": "আপনার স্কোর"},
    "quiz_score_of": {"en": "{score} of {n}", "bn": "{n}টির মধ্যে {score}টি"},
    "quiz_right": {"en": "Correct.", "bn": "সঠিক।"},
    "quiz_unanswered": {"en": "Not answered.", "bn": "উত্তর দেওয়া হয়নি।"},
    "quiz_wrong": {"en": "Not correct.", "bn": "সঠিক নয়।"},
    "quiz_you_chose": {"en": "You chose", "bn": "আপনার উত্তর"},
    "quiz_correct_answer": {"en": "Correct answer", "bn": "সঠিক উত্তর"},
    "quiz_page_title": {
        "en": "BRAC Field Assistant – Quiz",
        "bn": "ব্র্যাক মাঠকর্মী সহায়ক – কুইজ"},
    "quiz_subtitle": {"en": "Practice quiz", "bn": "অনুশীলনের কুইজ"},

    # ------------------------------------------------------------- footer
    # The second sentence pair used to say product status was unconfirmed
    # and that the notice would stay "until the status sheet comes back".
    # The sheet is data/registry/products.csv, and the status panel reads it,
    # so that sentence became false the day the panel switched.
    "footer_note": {
        "en": ("Answers are drawn from the operations manuals and may be "
               "incomplete. Verify against the printed manual before acting. "
               "Product status comes from the field team's registry, not "
               "from the manuals."),
        "bn": ("উত্তরগুলো অপারেশন ম্যানুয়াল থেকে নেওয়া এবং অসম্পূর্ণ হতে "
               "পারে। কাজ করার আগে ছাপা ম্যানুয়ালের সঙ্গে মিলিয়ে নিন। পণ্যের "
               "অবস্থা ম্যানুয়াল থেকে নয়, মাঠ দলের পণ্য নিবন্ধন থেকে নেওয়া।")},
    # THE BAR CARRIES THE PROGRAMME NAME, IN BENGALI, IN BOTH MODES. It is
    # the brand descriptor the footer specifies, not a sentence to translate.
    "footer_bar": {
        "en": "মাইক্রোফাইন্যান্স কর্মসূচি",
        "bn": "মাইক্রোফাইন্যান্স কর্মসূচি"},
}


def num(s):
    """Bengali numerals for an already formatted number: '63.6%' -> '৬৩.৬%'.
    Only for numbers. Never pass an identifier through this."""
    return str(s).translate(BN_DIGITS)


def t(key, lang, **kw):
    """The string `key` in `lang`, formatted. -> str

    Ints and floats in `kw` are rendered with Bengali numerals in bn.
    Strings are inserted untouched -- a model name or a chunk id keeps its
    digits -- so a number already formatted as a string must go through
    num() first if it should change.

    A MISSING KEY RAISES. A silent fallback to English, or to the key, is
    exactly how an untranslated string reaches a Bengali page unnoticed.
    """
    s = STRINGS[key][lang]
    if not kw:
        return s
    if lang == "bn":
        kw = {k: num(v) if isinstance(v, (int, float)) else v
              for k, v in kw.items()}
    return s.format(**kw)


def missing():
    """[(key, lang)] for every entry without a non-empty value in a language.

    Empty is allowed only where it is the meaning -- error_untranslated is
    empty in English because there is nothing to say. Listed here so the
    exception is visible rather than inferred."""
    allowed_empty = {("error_untranslated", "en")}
    return [(k, lang) for k, v in STRINGS.items() for lang in LANGS
            if (k, lang) not in allowed_empty and not v.get(lang)]

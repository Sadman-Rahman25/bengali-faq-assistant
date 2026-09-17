#!/usr/bin/env bash
# run_livestock_grow.sh -- the PDF ingestion path, with gates.
#
# Generic in its arguments: it takes any PDF and any manual_id, so the same
# script serves the next manual too.
#
#   ./run_livestock_grow.sh data/raw/livestock_grow.pdf livestock_grow_2026
#
# Each stage exits non-zero when its verdict is not clean, and `set -e` stops
# the run there. That is intentional: the whole lesson of the docx phase was
# that a stage passing on data it could not see poisons everything after it.
set -euo pipefail

PDF="${1:?usage: run_livestock_grow.sh <pdf> <manual_id>}"
MID="${2:?usage: run_livestock_grow.sh <pdf> <manual_id>}"
OUT="out/${MID}"
PROBES="${PROBES:-probes_livestock_grow.csv}"
PY="${PY:-python}"
mkdir -p "$OUT"

echo "=== 0. source check ==================================================="
echo "Has anyone asked the programme team for the .docx? Dabi and Progoti"
echo "were both authored in Word. One email versus several days of OCR"
echo "benchmarking, and the docx gives back real Unicode AND real heading"
echo "styles -- the two things this path has to reconstruct. If it exists,"
echo "stop here and use ex.py. Everything below is the fallback."
echo

echo "=== 1. triage ========================================================="
"$PY" pdf_triage.py "$PDF" --dump "$OUT/raw.txt"

echo
echo "=== 2. text-layer correctness ========================================="
# Does not gate the run: a CORRUPT verdict routes to the shootout, it does
# not end the pipeline.
VERDICT_BAD=0
"$PY" pdf_extract_check.py "$PDF" --probes "$PROBES" \
    --html "$OUT/side_by_side.html" --pages 1-12 || VERDICT_BAD=1

if [[ "$VERDICT_BAD" == "1" ]]; then
  echo
  echo "=== 3. extractor shootout ==========================================="
  "$PY" pdf_shootout.py "$PDF" --probes "$PROBES" --save-best "$OUT/best.txt"
  echo
  echo "If every route scored the same and all were bad, the text layer is"
  echo "broken -- a wrong ToUnicode CMap, not a library problem. Run:"
  echo "  $PY ocr_bench.py $PDF"
  echo "Then re-run stage 2 on the OCR output. OCR failure modes differ from"
  echo "CMap failure modes but are equally invisible to a range check."
  exit 1
fi

echo
echo "=== 4. heading spine =================================================="
"$PY" pdf_structure.py "$PDF" --out "$OUT/structure.json"

echo
echo "=== 5. extraction ====================================================="
"$PY" ex_pdf.py "$PDF" \
    --structure "$OUT/structure.json" \
    --manual-id "$MID" --source-type pdf_text --confidence probed \
    --out "$OUT/chunks.jsonl"

echo
echo "=== 6. figure audit ==================================================="
"$PY" digit_audit.py "$OUT/chunks.jsonl" --pdf "$PDF" \
    --html "$OUT/figure_worklist.html"

echo
echo "=== 7. rejoin the existing pipeline ==================================="
cat <<EOF
ex_pdf.py emits ex.py's schema, so these run unmodified. From the repo root:

  $PY retag.py    $OUT/chunks.jsonl     # expect 0 changed
  $PY resplit.py  $OUT/chunks.jsonl     # expect 0 oversized
  $PY dupscan.py  --all-manuals         # overlap is now 3-way
  $PY dedupe.py   --threshold 1.000     # hold it at exactly 1.000
  $PY roles.py                          # counts $MID; reports any third title
  $PY conventions_check.py              # must exit 0

NOTE: reconcile.py is referenced by the ingestion record but does not exist
in this repo. Until it does, do the product reconciliation by hand -- and do
it FIRST, because of this:

  productmatch.NAMED records লাইভস্টক গ্রো as an ALIAS of
  গবাদিপ্রাণি সুরক্ষা বিমা, key livestock_insurance, an INSURANCE product
  (Dabi §5.4.3). If this manual defines Livestock Grow as a loan, every
  chunk in it inherits the wrong product identity, retrieval answers
  Livestock Grow questions out of Dabi §5.4, and no figure check would ever
  notice -- the numbers would all be internally consistent and attached to
  the wrong product. Gold question G-101 is the one that settles it.

Then, before embedding:
  - open $OUT/figure_worklist.html and verify every flagged figure
  - open $OUT/side_by_side.html and read 12 pages against the render
  - calibrate $PROBES: min_count is 1 for every term, which catches a word
    that vanished but not one that is half damaged. Raise each to about half
    its observed count and fill verified_on_page.
  - flip extraction_confidence from "probed" to "verified" on what you read
EOF

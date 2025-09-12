def get_irs_1040_2024_extraction_prompt() -> str:
    """
    System prompt for extracting data from IRS Form 1040 (2024) with strict schema adherence.
    """
    return (
        "You are extracting data from IRS Form 1040 (2024). "
        "Return only a single JSON object that exactly conforms to the provided JSON Schema—no extra keys, comments, or text.\n"
        "Where to look & how to read\n"
        "• Match fields by official line numbers and nearby label phrases (e.g., “Line 1a Wages, salaries, tips”). "
        "Ignore PDF page numbers, coordinates, or layout variations.\n"
        "• Process all pages of the document; include values only if they appear on the 1040 itself (not on referenced schedules unless the numeric value is printed on the 1040).\n"
        "Value rules\n"
        "• If a field is blank/absent/unreadable or shows a dash (—) or “N/A”, output null (not 0/false/empty string).\n"
        "• For money amounts: strip $ and commas; if parentheses are printed, return the negative value (e.g., “(1,234)” → -1234).\n"
        "• Do not compute totals or differences; copy printed totals only. If the form says “See Schedule …” with no number on the 1040, return null.\n"
        "• For checkboxes use tri-state semantics:\n"
        "– true if clearly checked;\n"
        "– false if clearly present and empty;\n"
        "– null if missing, cropped, or ambiguous.\n"
        "• Allow masked identifiers where printed (e.g., XXX-XX-1234). Do not unmask, reformat, or infer.\n"
        "• Do not normalize addresses/phones/emails—copy exactly as printed.\n"
        "Output\n"
        "• Produce exactly one JSON object matching the schema. If uncertain between multiple possible values, choose null."
    )


"""
Lead Data Cleaning & Sorting Tool  (Raw Lead File -> Final Campaign File)
--------------------------------------------------------------------------

Implements the full workflow:
1. Standardize raw data (detect columns, split Full Name, rename, keep relevant fields)
2. Remove blank email records
3. Remove Indian contacts (by location/country + email domain)
4. Remove special characters & Unicode junk, trim spaces
5. Remove duplicates within file (by Email)
6. Remove records already in Master File(s) (by Email)
7. Remove bounced emails (by Master Bounce file, Email)
8. Arrange final column sequence
9. Final quality check + summary report
"""

import io
import re
import unicodedata
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Lead Cleaner", layout="wide")
st.title("Lead Data Cleaning & Sorting Tool")
st.write("Upload your raw lead file (plus optional Master File and Master Bounce File) to produce a clean, campaign-ready file.")

with st.expander("ℹ️ How this works ", expanded=False):
    st.markdown(
        """
1. Upload your raw lead file one file at a time. The tool auto-detects which columns correspond to First Name, Last Name, Company, Email, Job Title, Industry, and Location. If your file has a single Full Name column instead of separate First/Last, it will be auto-split. Only the relevant fields are kept for the final output.
2. Upload Master File and Master Bounce File (optional). These are used to remove any contacts that have already been contacted or have bounced in the past, so you don't re-contact them.
3. System will show preview of your raw data and detected column mapping. You can adjust the mapping if needed.
4. Click "Run cleaning pipeline" to process the data. Progress bar will show the status of each step. The steps include:
5. Sample of cleaned data will be shown for review, along with a processing report summarizing what happened at each step.
6. Select your preferred download format (CSV or XLSX) and download the final cleaned campaign file.
7. After processing you'll also get: a metrics summary (duplicates removed, Indian contacts removed, special characters found), a location-based split (US/UK/Canada/Australia/Other), and downloadable audit files showing exactly which rows were removed and why.
        """
    )
 
FIELD_HELP = {
    "Full Name": "If your file has one combined name column instead of separate First/Last, map it here — it will be auto-split.",
    "First Name": "Contact's first name.",
    "Last Name": "Contact's last name.",
    "Company": "The company or organization the contact works at.",
    "Email": "Required for every step — used to remove duplicates, existing contacts, and bounces.",
    "Job Title": "Contact's job title, designation, or role.",
    "Industry": "Optional. The company's industry or sector.",
    "Location": "Optional. City, state, or country — also used to detect and remove Indian contacts.",
}
 
FINAL_COLUMNS = ["First Name", "Last Name", "Company", "Email", "Job Title", "Industry", "Location"]
 
# Header synonyms used for auto-detecting columns in messy raw files
COLUMN_SYNONYMS = {
    "Full Name": ["full name", "fullname", "name", "contact name"],
    "First Name": ["first name", "firstname", "fname", "given name"],
    "Last Name": ["last name", "lastname", "lname", "surname", "family name"],
    "Company": ["company", "company name", "organization", "organisation", "employer"],
    "Email": ["email", "email address", "e-mail", "emailid", "email id"],
    "Job Title": ["job title", "title", "designation", "position", "role"],
    "Industry": ["industry", "sector", "vertical"],
    "Location": ["location", "city", "country", "region", "address", "state"],
}
 
INDIAN_STATE_CITY_HINTS = [
    "india", "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai",
    "kolkata", "pune", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur",
    "nagpur", "indore", "gurgaon", "gurugram", "noida", "chandigarh", "kerala",
    "punjab", "maharashtra", "karnataka", "tamil nadu", "gujarat", "rajasthan",
    "uttar pradesh", "west bengal", "telangana", "andhra pradesh",
]
INDIAN_HINT_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(h) for h in INDIAN_STATE_CITY_HINTS) + r")\b"
)
 
 
SPECIAL_CHARS_PATTERN = re.compile(
    r"[ÃÂÄÅƒÙ¢€™žœ¦§µ¶®·¸»¼½¾¿ŸþÿΓÇ~*!#$%^?]"
)
# Combined pattern for fast vectorized counting: any non-ASCII char, or any of the
# specific ASCII symbols we strip. Equivalent in effect to count_special_chars() below,
# but usable directly with pandas .str.count() for speed on large files.
SPECIAL_CHARS_COUNT_PATTERN = re.compile(r"[^\x00-\x7F]|[~*!#$%^?]")
 
# Country buckets for splitting the final file by region.
# Best-effort keyword match against the Location field / email TLD.
COUNTRY_HINTS = {
    "US": [
        "usa", "united states", "u.s.a", "u.s.", " us ", "america",
        "Alabama", "Alaska", "Arizona", "Arkansas", "California",
        "Colorado", "Connecticut", "Delaware", "Florida", "Georgia",
        "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", 
        "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", 
        "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "miami", 
        "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", 
        "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", 
        "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", 
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", 
        "Wisconsin", "Wyoming", "los angeles", "new york city", "chicago", "houston", "phoenix", 
        "san francisco", "philadelphia", "san antonio", "san diego", "dallas", "san jose",
    ],
    "UK": [
        "united kingdom", "uk", "england", "scotland", "wales", "northern ireland", 
        "london", "manchester", "birmingham", "glasgow", "edinburgh", "liverpool", 
        "leeds", "bristol", "sheffield", "newcastle", "nottingham", "leicester", 
        "southampton", "belfast", "cardiff", "coventry", "hull", "bradford", 
        "stoke-on-trent", "wolverhampton", "plymouth", "derby", "swansea", 
        "aberdeen", "dundee", "portsmouth", "york", "norwich", "oxford", "cambridge", 
        "gloucester", "bath", "exeter", "canterbury", "salisbury"
    ],
    "Canada": [
       "canada", "ca", "ontario", "quebec", "british columbia", "alberta", "manitoba", 
       "saskatchewan", "nova scotia", "new brunswick", "newfoundland and labrador", 
       "prince edward island", "northwest territories", "yukon", "nunavut", "toronto", 
       "montreal", "vancouver", "calgary", "edmonton", "ottawa", "winnipeg", "quebec city", 
       "hamilton", "kitchener", "halifax", "victoria", "windsor", "saskatoon", "regina", 
       "st. john's", "kelowna", "barrie", "sherbrooke", "guelph", "kingston", "moncton", "sudbury", 
       "charlottetown", "whitehorse", "yellowknife", "iqaluit"
    ],
    "Australia": [
       "australia", "au", "new south wales", "victoria", "queensland", "western australia", 
       "south australia", "tasmania", "australian capital territory", "northern territory", 
       "sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra", "hobart", "darwin", 
       "gold coast", "newcastle", "central coast", "wollongong", "geelong", "townsville", 
       "cairns", "toowoomba", "ballarat", "bendigo", "albury", "launceston", "mackay", 
       "rockhampton", "bunbury", "bundaberg", "wagga wagga", "hervey bay", "mildura", "shepparton", 
       "port macquarie", "gladstone", "tamworth", "orange", "dubbo", "geraldton"
    ],
}
COUNTRY_PATTERNS = {
    country: re.compile(r"\b(?:" + "|".join(re.escape(h) for h in hints) + r")\b")
    for country, hints in COUNTRY_HINTS.items()
}

 
 
def normalize_header(col):
    return re.sub(r"[^a-z0-9]", "", str(col).lower())
 
 
def auto_map_columns(df):
    """Return {standard_name: actual_column_name_in_df} based on header synonyms."""
    mapping = {}
    normalized_cols = {normalize_header(c): c for c in df.columns}
    for standard, synonyms in COLUMN_SYNONYMS.items():
        for syn in synonyms:
            norm_syn = normalize_header(syn)
            if norm_syn in normalized_cols:
                mapping[standard] = normalized_cols[norm_syn]
                break
    return mapping
 
 
def count_special_chars(value):
    """Count special/junk characters in a value (special-char set + non-ASCII/control chars)."""
    if pd.isna(value):
        return 0
    text = str(value)
    count = len(SPECIAL_CHARS_PATTERN.findall(text))
    # Count non-ASCII characters not already covered by the pattern above
    for ch in text:
        if ord(ch) > 127 and not SPECIAL_CHARS_PATTERN.match(ch):
            count += 1
    return count
 
 
def clean_text(value):
    """Remove special/unicode junk, hidden non-printables, and trim spaces."""
    if pd.isna(value):
        return value
    text = str(value)
    # Remove specified special characters
    text = SPECIAL_CHARS_PATTERN.sub("", text)
    # Normalize unicode (decompose accented chars) then drop non-ASCII leftovers
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    text = text.encode("ascii", "ignore").decode("ascii")
    # Collapse multiple spaces, trim
    text = re.sub(r"\s+", " ", text).strip()
    # Remove stray leftover symbols like ~~ / ~ if any slipped through
    text = re.sub(r"~+", "", text).strip()
    return text
 
 
def is_indian_contact(row, location_col, email_col, check_location=True, check_domain=True):
    """Row-wise version, kept for reference/testing. The app uses the vectorized
    find_indian_contacts() below for performance on large files."""
    location_val = str(row.get(location_col, "")).lower() if location_col else ""
    email_val = str(row.get(email_col, "")).lower() if email_col else ""
 
    if check_location and INDIAN_HINT_PATTERN.search(location_val):
        return True, "Location text", row.get(location_col, "")
 
    if check_domain and email_val.endswith(".in"):
        return True, "Email domain (.in)", row.get(email_col, "")
 
    return False, "", ""
 
 
def find_indian_contacts(std, location_col="Location", email_col="Email", check_location=True, check_domain=True):
    """Vectorized Indian-contact detection — fast on large (300k+ row) files.
    Returns (is_match_series, reason_series, matched_value_series)."""
    n = len(std)
    location_series = std[location_col].astype(str) if location_col in std.columns else pd.Series([""] * n, index=std.index)
    email_series = std[email_col].astype(str) if email_col in std.columns else pd.Series([""] * n, index=std.index)
 
    location_match = (
        location_series.str.lower().str.contains(INDIAN_HINT_PATTERN, regex=True, na=False)
        if check_location else pd.Series(False, index=std.index)
    )
    domain_match = (
        email_series.str.lower().str.endswith(".in", na=False)
        if check_domain else pd.Series(False, index=std.index)
    )
 
    is_match = location_match | domain_match
    reason = pd.Series("", index=std.index)
    reason[domain_match] = "Email domain (.in)"
    reason[location_match] = "Location text"  # location takes priority if both match
 
    matched_value = pd.Series("", index=std.index)
    matched_value[domain_match] = email_series[domain_match]
    matched_value[location_match] = location_series[location_match]
 
    return is_match, reason, matched_value
 
 
def get_country_bucket(location_text):
    """Row-wise version, kept for reference/testing. The app uses the vectorized
    classify_countries() below for performance on large files."""
    text = str(location_text).lower()
    if not text.strip():
        return "Unknown"
    for country, pattern in COUNTRY_PATTERNS.items():
        if pattern.search(text):
            return country
    return "Other"
 
 
def classify_countries(location_series):
    """Vectorized country classification — fast on large (300k+ row) files."""
    text = location_series.astype(str).str.lower()
    result = pd.Series("Other", index=location_series.index)
    is_blank = text.str.strip() == ""
    # Apply in a fixed order so the first matching country wins for ambiguous text
    unmatched = pd.Series(True, index=location_series.index)
    for country, pattern in COUNTRY_PATTERNS.items():
        match = unmatched & text.str.contains(pattern, regex=True, na=False)
        result[match] = country
        unmatched &= ~match
    result[is_blank] = "Unknown"
    return result
 
 
def load_file(file):
    """Load CSV, XLSX, or XLS based on the actual file extension, with clear errors on failure."""
    filename = file.name.lower()
 
    if filename.endswith(".csv"):
        # Raw lead exports often aren't UTF-8 (Windows-1252/Latin-1 are common
        # from CRMs and Excel). Try a few encodings before giving up.
        encodings_to_try = ["utf-8", "utf-8-sig", "cp1252", "latin1"]
        for enc in encodings_to_try:
            try:
                file.seek(0)
                return pd.read_csv(file, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        # Last resort: decode leniently, replacing bad bytes instead of failing
        file.seek(0)
        return pd.read_csv(file, encoding="latin1", encoding_errors="replace")
 
    elif filename.endswith(".xlsx"):
        file.seek(0)
        return pd.read_excel(file, engine="openpyxl")
 
    elif filename.endswith(".xls"):
        file.seek(0)
        return pd.read_excel(file, engine="xlrd")
 
    else:
        raise ValueError(f"Unsupported file format: {file.name}. Please upload CSV, XLSX, or XLS.")
 
 
def get_email_series(df, mapping):
    email_col = mapping.get("Email")
    if email_col and email_col in df.columns:
        return df[email_col].astype(str).str.strip().str.lower()
    return pd.Series([""] * len(df))
 
 
# ---------------- FILE UPLOADS ----------------
st.subheader("Step 1 — Upload your files")
col1, col2, col3 = st.columns(3)
with col1:
    raw_file = st.file_uploader(
        "Raw lead file (required)",
        type=["csv", "xlsx", "xls"],
        key="raw",
        help="The messy export you want cleaned — from a scraper, CRM, or list purchase.",
    )
with col2:
    master_files = st.file_uploader(
        "Master file(s) — past campaign contacts (optional)",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        key="master",
        help="Contacts from previous campaigns. Anyone whose email matches will be removed so you don't re-contact them.",
    )
with col3:
    bounce_file = st.file_uploader(
        "Master Bounce file (optional)",
        type=["csv", "xlsx", "xls"],
        key="bounce",
        help="Emails that have previously bounced. Any matching address will be removed to protect your sender reputation.",
    )
 
if raw_file is not None:
    try:
        df = load_file(raw_file)
    except Exception as e:
        st.error(f"Could not read the uploaded raw lead file: {e}")
        st.info("Please make sure the file is a valid CSV, XLSX, or XLS file and isn't corrupted.")
        st.stop()
 
    st.subheader("Step 2 — Check your raw data")
    st.dataframe(df.head(10), width="stretch")
    st.caption(f"{df.shape[0]} rows x {df.shape[1]} columns. Scroll right to confirm nothing looks obviously broken before you continue.")
 
    auto_mapping = auto_map_columns(df)
 
    st.subheader("Step 3 — Confirm column mapping")
    st.write(
        "We auto-detected which of your file's columns correspond to each field below. "
        "Fix anything that's wrong before processing — this determines exactly what ends up in your final file. "
        "Fields greyed out have no matching column in this file and will be left blank."
    )
    options = ["(none)"] + list(df.columns)
    mapping = {}
    map_cols = st.columns(4)
    fields_to_map = ["Full Name", "First Name", "Last Name", "Company", "Email", "Job Title", "Industry", "Location"]
    for i, field in enumerate(fields_to_map):
        default_col = auto_mapping.get(field, "(none)")
        field_missing = default_col == "(none)"
        default_index = options.index(default_col) if default_col in options else 0
        with map_cols[i % 4]:
            help_text = "No matching column found in this file" if field_missing else FIELD_HELP.get(field)
            selected = st.selectbox(
                field,
                options,
                index=default_index,
                key=f"map_{field}",
                disabled=field_missing,
                help=help_text,
            )
        if selected != "(none)":
            mapping[field] = selected
 
    if "Email" not in mapping:
        st.warning("No Email column is mapped. Every row will be treated as blank-email and removed — double-check your mapping above.")
 
    with st.expander("⚙️ Indian-contact detection settings", expanded=False):
        st.caption(
            "If Step 3 is removing rows that shouldn't be flagged, check which signal is causing it "
            "by toggling these off one at a time and re-running. Every removed row is also available "
            "as a downloadable audit file after processing, showing exactly what matched."
        )
        check_location = st.checkbox(
            "Match by Location text (city/state/country names)", value=True,
            help="Uses the mapped Location field. Note: if your 'Location' column is really a sales "
                 "territory/region assignment rather than the contact's actual address, this can misfire.",
        )
        check_domain = st.checkbox(
            "Match by email domain ending in .in", value=True,
            help="Flags any email address whose domain ends in the .in (India) TLD.",
        )
 
    st.subheader("Step 4 — Run the pipeline")
    st.caption("This runs all 9 cleaning steps in order and produces a campaign-ready file. Nothing is saved until you click below.")
 
    if st.button("Run cleaning pipeline", type="primary"):
        progress_bar = st.progress(0, text="Starting cleaning pipeline...")
        TOTAL_STEPS = 9
 
        def update_progress(step_num, message):
            progress_bar.progress(step_num / TOTAL_STEPS, text=f"Step {step_num}/{TOTAL_STEPS} - {message}")
 
        report = []
        work = df.copy()
        start_count = len(work)
        report.append(f"Starting rows: {start_count}")
 
        # ---- STEP 1: Standardize ----
        update_progress(1, "Standardizing columns...")
        # Split Full Name if First/Last not directly available
        if "First Name" not in mapping and "Full Name" in mapping:
            full_col = mapping["Full Name"]
            split_names = work[full_col].astype(str).str.strip().str.split(" ", n=1, expand=True)
            work["__First Name"] = split_names[0]
            work["__Last Name"] = split_names[1] if split_names.shape[1] > 1 else ""
            mapping["First Name"] = "__First Name"
            mapping["Last Name"] = "__Last Name"
 
        # Build standardized dataframe with only relevant fields
        std = pd.DataFrame()
        for field in FINAL_COLUMNS:
            if field in mapping:
                std[field] = work[mapping[field]]
            else:
                std[field] = ""
        report.append(f"Step 1 - Standardized columns. Fields kept: {[c for c in FINAL_COLUMNS if c in mapping]}")
 
        # ---- STEP 2: Remove blank email records ----
        update_progress(2, "Removing blank emails...")
        before = len(std)
        std["Email"] = std["Email"].astype(str).str.strip()
        std = std[(std["Email"] != "") & (std["Email"].str.lower() != "nan")]
        std = std.reset_index(drop=True)
        report.append(f"Step 2 - Removed blank emails: {before - len(std)} rows removed")
 
        # ---- STEP 3: Remove Indian contacts ----
        update_progress(3, "Removing Indian contacts...")
        before = len(std)
        indian_mask, matched_reason, matched_value = find_indian_contacts(
            std, "Location", "Email", check_location, check_domain
        )
        removed_indian_df = std[indian_mask].copy()
        removed_indian_df["Matched On"] = matched_reason[indian_mask]
        removed_indian_df["Matched Value"] = matched_value[indian_mask]
        st.session_state["removed_indian_df"] = removed_indian_df
        std = std[~indian_mask].reset_index(drop=True)
        report.append(f"Step 3 - Removed Indian contacts: {before - len(std)} rows removed")
 
        # ---- STEP 4: Remove special characters & Unicode, trim ----
        update_progress(4, "Cleaning special characters and unicode...")
        # Count special characters BEFORE cleaning, so we can report exactly how much was found
        # (vectorized for speed on large files)
        email_special_char_counts = std["Email"].astype(str).str.count(SPECIAL_CHARS_COUNT_PATTERN)
        total_special_chars_email = int(email_special_char_counts.sum())
        rows_with_special_chars_email = int((email_special_char_counts > 0).sum())
 
        # Track which rows had ANY field changed by cleaning (for the audit file + row count)
        before_snapshot = std.copy()
        for col in FINAL_COLUMNS:
            std[col] = std[col].apply(clean_text)
 
        changed_mask = pd.Series(False, index=std.index)
        for col in FINAL_COLUMNS:
            changed_mask = changed_mask | (before_snapshot[col].astype(str) != std[col].astype(str))
        special_chars_audit_df = before_snapshot[changed_mask].copy()
        special_chars_audit_df["Cleaned Email"] = std.loc[changed_mask, "Email"]
        st.session_state["special_chars_audit_df"] = special_chars_audit_df
        rows_cleaned_count = int(changed_mask.sum())
 
        report.append(
            f"Step 4 - Cleaned special characters, hidden unicode, and extra spaces: "
            f"{rows_cleaned_count} rows changed "
            f"({rows_with_special_chars_email} emails had special characters, "
            f"{total_special_chars_email} special characters found in Email field total)"
        )
 
        # Re-check blank emails after cleaning (in case cleaning emptied any)
        before = len(std)
        std = std[(std["Email"] != "") & (std["Email"].str.lower() != "nan")]
        std = std.reset_index(drop=True)
        if before - len(std) > 0:
            report.append(f"Step 4b - Removed emails that became blank after cleaning: {before - len(std)} rows removed")
 
        # ---- STEP 5: Remove duplicates within file (by Email) ----
        update_progress(5, "Removing duplicate emails...")
        before = len(std)
        std["__email_lower"] = std["Email"].str.lower()
        duplicate_count = int(std["__email_lower"].duplicated().sum())
        std = std.drop_duplicates(subset="__email_lower", keep="first")
        std = std.reset_index(drop=True)
        report.append(f"Step 5 - Removed in-file duplicates: {duplicate_count} duplicate rows removed")
 
        # ---- STEP 6: Remove records already in Master File(s) ----
        update_progress(6, "Checking against Master File(s)...")
        if master_files:
            master_emails = set()
            for mf in master_files:
                try:
                    mdf = load_file(mf)
                except Exception as e:
                    st.error(f"Could not read Master file '{mf.name}': {e}")
                    st.info("Please make sure the file is a valid CSV, XLSX, or XLS file and isn't corrupted.")
                    st.stop()
                m_mapping = auto_map_columns(mdf)
                m_email_col = m_mapping.get("Email")
                if m_email_col:
                    master_emails.update(mdf[m_email_col].astype(str).str.strip().str.lower().tolist())
            before = len(std)
            std = std[~std["__email_lower"].isin(master_emails)].reset_index(drop=True)
            report.append(f"Step 6 - Removed emails already in Master File(s): {before - len(std)} rows removed")
        else:
            report.append("Step 6 - No Master File uploaded, step skipped")
 
        # ---- STEP 7: Remove bounced emails ----
        update_progress(7, "Checking against Master Bounce file...")
        if bounce_file is not None:
            try:
                bdf = load_file(bounce_file)
            except Exception as e:
                st.error(f"Could not read the Bounce file: {e}")
                st.info("Please make sure the file is a valid CSV, XLSX, or XLS file and isn't corrupted.")
                st.stop()
            b_mapping = auto_map_columns(bdf)
            b_email_col = b_mapping.get("Email")
            if b_email_col:
                bounce_emails = set(bdf[b_email_col].astype(str).str.strip().str.lower().tolist())
                before = len(std)
                std = std[~std["__email_lower"].isin(bounce_emails)].reset_index(drop=True)
                report.append(f"Step 7 - Removed bounced emails: {before - len(std)} rows removed")
            else:
                report.append("Step 7 - Could not detect Email column in Bounce file, step skipped")
        else:
            report.append("Step 7 - No Master Bounce file uploaded, step skipped")
 
        std = std.drop(columns="__email_lower")
 
        # ---- STEP 8: Arrange final column sequence ----
        update_progress(8, "Arranging final column order...")
        std = std[FINAL_COLUMNS]
 
        # Drop Industry/Location columns entirely if never available and fully empty
        for optional_col in ["Industry", "Location"]:
            if optional_col not in mapping and (std[optional_col] == "").all():
                std = std.drop(columns=optional_col)
 
        # ---- STEP 9: Final quality check ----
        update_progress(9, "Running final quality check...")
        before = len(std)
        std = std.replace("", pd.NA)
        std = std.dropna(how="all")
        std = std.fillna("")
        std = std[std["Email"].astype(str).str.strip() != ""]
        std = std.reset_index(drop=True)
        report.append(f"Step 9 - Final QC pass: removed {before - len(std)} blank/empty rows")
 
        dup_check = std["Email"].str.lower().duplicated().sum()
        blank_email_check = (std["Email"].astype(str).str.strip() == "").sum()
        report.append(f"Final QC - Duplicate emails remaining: {dup_check}")
        report.append(f"Final QC - Blank emails remaining: {blank_email_check}")
        report.append(f"Final row count: {len(std)} (started at {start_count})")
 
        # ---- Country split (for download) ----
        std["__country"] = classify_countries(std["Location"]) if "Location" in std.columns else "Unknown"
 
        progress_bar.progress(1.0, text="Done! Cleaning complete.")
 
        st.session_state["cleaned_df"] = std.drop(columns="__country")
        st.session_state["country_split"] = {
            country: std[std["__country"] == country].drop(columns="__country").reset_index(drop=True)
            for country in std["__country"].unique()
        }
        st.session_state["report"] = report
        st.session_state["metrics"] = {
            "indian_removed": len(st.session_state.get("removed_indian_df", [])),
            "duplicates_removed": duplicate_count,
            "special_char_rows": rows_cleaned_count,
            "special_char_total": total_special_chars_email,
            "special_char_emails": rows_with_special_chars_email,
        }
 
    if "cleaned_df" in st.session_state:
        st.subheader("Step 5 — Review the results")
 
        metrics = st.session_state.get("metrics", {})
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Duplicate emails removed", metrics.get("duplicates_removed", 0))
        m2.metric("Indian contacts removed", metrics.get("indian_removed", 0))
        m3.metric("Emails with special characters", metrics.get("special_char_emails", 0))
        m4.metric("Special characters found (Email field)", metrics.get("special_char_total", 0))
 
        with st.expander("Processing report (what happened at each step)", expanded=True):
            for line in st.session_state["report"]:
                st.write("- " + line)
 
        st.subheader("Final cleaned data (preview)")
        st.caption("This is what your downloaded file will contain, in final campaign order.")
        st.dataframe(st.session_state["cleaned_df"].head(50), width="stretch")
        st.caption(f"{st.session_state['cleaned_df'].shape[0]} rows x {st.session_state['cleaned_df'].shape[1]} columns")
 
        st.subheader("Step 6 — Download")
 
        tab_main, tab_country, tab_audit = st.tabs(["Final campaign file", "Split by location", "Audit files (removed rows)"])
 
        with tab_main:
            out_format = st.radio(
                "Download format",
                ["xlsx", "csv"],
                horizontal=True,
                help="xlsx opens directly in Excel; csv is the safer choice for uploading into most campaign/email tools.",
                key="main_format",
            )
            final_df = st.session_state["cleaned_df"]
            if out_format == "csv":
                data_bytes = final_df.to_csv(index=False).encode("utf-8")
                mime = "text/csv"
                filename = "final_campaign_file.csv"
            else:
                buffer = io.BytesIO()
                final_df.to_excel(buffer, index=False, engine="openpyxl")
                data_bytes = buffer.getvalue()
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                filename = "final_campaign_file.xlsx"
 
            st.download_button("Download final campaign file", data=data_bytes, file_name=filename, mime=mime, type="primary")
            st.caption("This file has already passed the final quality check — no duplicate or blank emails, ready to upload into your campaign tool.")
 
        with tab_country:
            st.caption(
                "Best-effort split based on the Location field (city/state/country name matching). "
                "'Other' means a location was present but didn't match a known country; 'Unknown' means Location was blank."
            )
            country_split = st.session_state.get("country_split", {})
            if not country_split or set(country_split.keys()) == {"Unknown"}:
                st.info("No Location data was available to split by — map a Location column and re-run to use this.")
            else:
                counts_df = pd.DataFrame(
                    [{"Group": k, "Rows": len(v)} for k, v in sorted(country_split.items(), key=lambda x: -len(x[1]))]
                )
                st.dataframe(counts_df, width="stretch", hide_index=True)
                for country, cdf in sorted(country_split.items(), key=lambda x: -len(x[1])):
                    if len(cdf) == 0:
                        continue
                    csv_bytes = cdf.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        f"Download {country} ({len(cdf)} rows)",
                        data=csv_bytes,
                        file_name=f"final_campaign_file_{country.lower().replace('/', '_')}.csv",
                        mime="text/csv",
                        key=f"dl_country_{country}",
                    )
 
        with tab_audit:
            st.caption("Rows removed or altered during cleaning, for your own verification — nothing here is in the final file.")
 
            removed_indian_df = st.session_state.get("removed_indian_df", pd.DataFrame())
            st.write(f"**Removed as Indian contacts:** {len(removed_indian_df)} rows")
            if len(removed_indian_df) > 0:
                st.dataframe(removed_indian_df.head(20), width="stretch")
                st.download_button(
                    "Download removed Indian contacts (full list)",
                    data=removed_indian_df.to_csv(index=False).encode("utf-8"),
                    file_name="removed_indian_contacts.csv",
                    mime="text/csv",
                    key="dl_indian_audit",
                )
                st.caption("Check the 'Matched On' and 'Matched Value' columns to see exactly what triggered each removal.")
 
            st.divider()
 
            special_chars_df = st.session_state.get("special_chars_audit_df", pd.DataFrame())
            st.write(f"**Rows with special characters found (now cleaned, kept in final file):** {len(special_chars_df)} rows")
            if len(special_chars_df) > 0:
                st.dataframe(special_chars_df.head(20), width="stretch")
                st.download_button(
                    "Download special-characters audit (full list)",
                    data=special_chars_df.to_csv(index=False).encode("utf-8"),
                    file_name="special_characters_found.csv",
                    mime="text/csv",
                    key="dl_specialchars_audit",
                )
                st.caption("Shows the original (uncleaned) values next to the cleaned Email, so you can verify nothing important was stripped out.")
else:
    st.info("Upload a raw lead file above to get started. Master File and Bounce File are optional but recommended for cleaner results.")
 

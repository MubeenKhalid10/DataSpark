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
import json
import re
import unicodedata
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Lead Cleaner", layout="wide")
st.title("Lead Data Cleaning & Sorting Tool")
st.write("Upload your raw lead file (plus optional Master File and Master Bounce File) to produce a clean, campaign-ready file.")
st.markdown(
    """
    <style>
    [data-testid="stFileUploaderDropzone"] svg {display: none;}
    [data-testid="stFileUploaderDropzoneInstructions"] {padding-top: 0.5rem;}
    .upload-card {border: 1px solid #E8E8E8; border-radius: 12px; padding: 0.75rem 1rem; background: #FAFAFB;}
    </style>
    """,
    unsafe_allow_html=True,
)

with st.expander("ℹ️ How this works ", expanded=False):
    st.markdown(
        """
1. Upload your raw lead file one file at a time. The tool auto-detects which columns correspond to First Name, Last Name, Company, Email, Job Title, Industry, and Location. If your file has a single Full Name column instead of separate First/Last, it will be auto-split. Only the relevant fields are kept for the final output.
2. Upload Master File and Master Bounce File (optional). These are used to remove any contacts that have already been contacted or have bounced in the past, so you don't re-contact them.
3. System will show preview of your raw data and detected column mapping. You can adjust the mapping if needed.
4. Click "Run cleaning pipeline" to process the data. Progress bar will show the status of each step. The steps include:
5. Sample of cleaned data will be shown for review, along with a processing report summarizing what happened at each step.
6. Select your preferred download format (CSV or XLSX) and download the final cleaned campaign file.
7. After processing you'll also get: a metrics summary (duplicates removed, Indian contacts removed, special characters found), a country-based split (using data/countries.json), and downloadable audit files showing exactly which rows were removed and why.
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

COUNTRIES_JSON_PATH = Path(__file__).parent / "data" / "countries.json"

 
 
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


def value_has_special_chars(value):
    if pd.isna(value):
        return False
    text = str(value)
    return bool(SPECIAL_CHARS_COUNT_PATTERN.search(text))
 
 
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
 
 
def normalize_place_text(value):
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
 
 
@st.cache_resource(show_spinner=False)
def load_country_reference(json_path_str):
    path = Path(json_path_str)
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError("countries.json format is invalid. Expected top-level 'data' list.")

    countries = payload["data"]
    country_name_lookup = {}
    city_to_countries = {}

    # Common aliases that appear in lead files but differ from the canonical JSON names.
    alias_overrides = {
        "us": "United States",
        "u s": "United States",
        "usa": "United States",
        "u s a": "United States",
        "united states of america": "United States",
        "uk": "United Kingdom",
        "u k": "United Kingdom",
        "england": "United Kingdom",
        "scotland": "United Kingdom",
        "wales": "United Kingdom",
        "northern ireland": "United Kingdom",
        "uae": "United Arab Emirates",
        "u a e": "United Arab Emirates",
        "south korea": "Korea South",
        "north korea": "Korea North",
    }

    for item in countries:
        country = str(item.get("country", "")).strip()
        if not country:
            continue

        country_norm = normalize_place_text(country)
        if country_norm:
            country_name_lookup[country_norm] = country

        for city in item.get("cities", []):
            norm_city = normalize_place_text(city)
            if norm_city:
                city_to_countries.setdefault(norm_city, set()).add(country)

    alias_lookup = {}
    for alias_norm, canonical in alias_overrides.items():
        canonical_norm = normalize_place_text(canonical)
        canonical_country = country_name_lookup.get(canonical_norm)
        if canonical_country:
            alias_lookup[normalize_place_text(alias_norm)] = canonical_country

    city_alias_overrides = {
        "los angles": "los angeles",
        "los angelos": "los angeles",
        "newyork": "new york",
        "sanfrancisco": "san francisco",
    }
    city_alias_lookup = {
        normalize_place_text(k): normalize_place_text(v)
        for k, v in city_alias_overrides.items()
    }

    return {
        "country_name_lookup": country_name_lookup,
        "alias_lookup": alias_lookup,
        "city_to_countries": city_to_countries,
        "city_alias_lookup": city_alias_lookup,
    }


def classify_country_from_location(location_text, country_ref):
    """Classify country from a free-form Location cell.

    Updated behaviour: prefer explicit state/country tokens first (exact country
    or alias matches). Only if no country/state is found do we fall back to
    city-based matching. This follows the requirement: "first look for
    state/country in location column, and if state/country name is not given
    then look for city name and classify/sort the email".
    """
    text_raw = str(location_text)
    normalized = normalize_place_text(text_raw)
    if not normalized:
        return "Unknown"

    country_name_lookup = country_ref["country_name_lookup"]
    alias_lookup = country_ref["alias_lookup"]
    city_to_countries = country_ref["city_to_countries"]
    city_alias_lookup = country_ref["city_alias_lookup"]

    # Split common location formats: "City, State, Country".
    raw_parts = re.split(r"[,;|/\\-]+", text_raw)
    parts = [normalize_place_text(p) for p in raw_parts if normalize_place_text(p)]
    parts = [city_alias_lookup.get(part, part) for part in parts]

    # 1) Prefer explicit country/state tokens in the parts (exact match or alias).
    for part in parts:
        if part in country_name_lookup:
            return country_name_lookup[part]
        if part in alias_lookup:
            return alias_lookup[part]

    # 2) Also check the full normalized text for a country phrase (handles
    #    cases like "State of X" or "Somewhere, United States").
    full_text = f" {normalized} "
    for country_norm, country in country_name_lookup.items():
        if f" {country_norm} " in full_text:
            return country
    for alias_norm, country in alias_lookup.items():
        if f" {alias_norm} " in full_text:
            return country

    # 3) Fallback: city-based matching if no explicit country/state token found.
    scores = {}
    # Fast path: try exact city matches first, then attempt substring matching
    for part in parts:
        matched_countries = city_to_countries.get(part)
        if not matched_countries:
            # Try to match known city keys that appear inside the part (e.g.,
            # "san francisco bay area" should match "san francisco"). This is
            # slightly more expensive but only runs when exact match fails.
            for city_key, countries_set in city_to_countries.items():
                if city_key in part:
                    matched_countries = countries_set
                    break
        if not matched_countries:
            continue
        weight = 3 if len(matched_countries) == 1 else 1
        for country in matched_countries:
            scores[country] = scores.get(country, 0) + weight

    if scores:
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_country, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else -1

        # Require a clear winner when city appears in multiple countries.
        if top_score >= second_score + 2:
            return top_country

        # If there is a tie-ish result, still return the top_country as a best guess.
        return top_country

    return "Other"


def classify_countries(location_series, country_ref):
    """Country classification with city-priority matching.
    Uses city evidence first, then country aliases as fallback."""
    return location_series.apply(lambda value: classify_country_from_location(value, country_ref))
 
 
def load_file(file):
    """Load CSV, XLSX, or XLS based on the actual file extension, with clear errors on failure.

    This function tries a few encodings for CSV. If a MemoryError occurs (very large files),
    it falls back to a streaming/chunked read for CSV to avoid crashing the app.
    """
    filename = file.name.lower()

    try:
        if filename.endswith(".csv"):
            # Raw lead exports often aren't UTF-8. Try a few encodings before giving up.
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
    except MemoryError:
        # Fallback for very large CSV files: stream in chunks and concatenate while
        # forcing strings to reduce memory pressure. This avoids crashing the app.
        if filename.endswith(".csv"):
            try:
                file.seek(0)
                chunks = []
                for chunk in pd.read_csv(file, encoding="latin1", encoding_errors="replace", chunksize=100_000, dtype=str):
                    chunks.append(chunk)
                if not chunks:
                    return pd.DataFrame()
                return pd.concat(chunks, ignore_index=True)
            except Exception as e:
                raise ValueError(f"Could not read large CSV file in streaming mode: {e}")
        raise
    except Exception as e:
        # Re-raise as ValueError to have consistent user-facing messages
        raise ValueError(f"Could not read file '{file.name}': {e}")
 
 
def get_email_series(df, mapping):
    email_col = mapping.get("Email")
    if email_col and email_col in df.columns:
        return df[email_col].astype(str).str.strip().str.lower()
    return pd.Series([""] * len(df))
 
 
# ---------------- FILE UPLOADS ----------------
st.subheader("Step 1 — Upload your files")
st.markdown('<div class="upload-card">Select files first, then click "Use selected files" to continue.</div>', unsafe_allow_html=True)
col1, col2, col3 = st.columns(3)
with col1:
    raw_file_selected = st.file_uploader(
        "Raw lead file (required)",
        type=["csv", "xlsx", "xls"],
        key="raw",
        help="The messy export you want cleaned — from a scraper, CRM, or list purchase.",
    )
with col2:
    master_files_selected = st.file_uploader(
        "Master file(s) — past campaign contacts (optional)",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        key="master",
        help="Contacts from previous campaigns. Anyone whose email matches will be removed so you don't re-contact them.",
    )
with col3:
    bounce_file_selected = st.file_uploader(
        "Master Bounce file (optional)",
        type=["csv", "xlsx", "xls"],
        key="bounce",
        help="Emails that have previously bounced. Any matching address will be removed to protect your sender reputation.",
    )

if st.button("Use selected files", type="secondary"):
    st.session_state["active_raw_file"] = raw_file_selected
    st.session_state["active_master_files"] = master_files_selected
    st.session_state["active_bounce_file"] = bounce_file_selected

active_raw_file = st.session_state.get("active_raw_file")
active_master_files = st.session_state.get("active_master_files", [])
active_bounce_file = st.session_state.get("active_bounce_file")

if active_raw_file is not None:
    st.success(f"Using raw file: {active_raw_file.name}")
if active_master_files:
    st.caption("Using master files: " + ", ".join(f.name for f in active_master_files))
if active_bounce_file is not None:
    st.caption(f"Using bounce file: {active_bounce_file.name}")
 
if active_raw_file is not None:
    try:
        with st.spinner("Loading raw lead file..."):
            df = load_file(active_raw_file)
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
        with st.spinner("Processing — this can take a while for large files..."):
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
 
        # ---- STEP 4: Separate special characters + clean non-email text ----
        update_progress(4, "Separating special-character rows and cleaning text fields...")
        email_special_char_counts = std["Email"].astype(str).str.count(SPECIAL_CHARS_COUNT_PATTERN)
        total_special_chars_email = int(email_special_char_counts.sum())
        rows_with_special_chars_email = int((email_special_char_counts > 0).sum())

        # Build row-level flags by field so removed records are fully auditable.
        field_masks = {}
        for col in FINAL_COLUMNS:
            series = std[col] if col in std.columns else pd.Series([""] * len(std), index=std.index)
            field_masks[col] = series.astype(str).str.contains(SPECIAL_CHARS_COUNT_PATTERN, regex=True, na=False)

        any_special_mask = pd.Series(False, index=std.index)
        for col in FINAL_COLUMNS:
            any_special_mask = any_special_mask | field_masks[col]

        email_special_mask = field_masks["Email"]
        removed_special_df = std[any_special_mask].copy()
        removed_special_df["Matched Fields"] = removed_special_df.apply(
            lambda row: ", ".join([c for c in FINAL_COLUMNS if field_masks[c].get(row.name, False)]),
            axis=1,
        )
        st.session_state["special_chars_removed_df"] = removed_special_df
        st.session_state["special_chars_email_df"] = std[email_special_mask].copy()

        before = len(std)
        std = std[~any_special_mask].reset_index(drop=True)

        # Clean non-email text only in the remaining campaign rows.
        rows_changed_after_clean = 0
        before_snapshot = std.copy()
        cleanable_columns = [c for c in FINAL_COLUMNS if c != "Email"]
        for col in cleanable_columns:
            std[col] = std[col].apply(clean_text)

        if len(std) > 0:
            changed_mask = pd.Series(False, index=std.index)
            for col in cleanable_columns:
                changed_mask = changed_mask | (before_snapshot[col].astype(str) != std[col].astype(str))
            rows_changed_after_clean = int(changed_mask.sum())

        report.append(
            f"Step 4 - Separated special-character rows into audit file: {before - len(std)} rows removed from final output "
            f"({rows_with_special_chars_email} emails had special characters, "
            f"{total_special_chars_email} special characters found in Email field total). "
            f"Then cleaned non-email text fields in remaining rows: {rows_changed_after_clean} rows changed"
        )
 
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
        if active_master_files:
            master_emails = set()
            for mf in active_master_files:
                try:
                    with st.spinner(f"Reading Master file {mf.name}..."):
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
        if active_bounce_file is not None:
            try:
                with st.spinner(f"Reading Bounce file {active_bounce_file.name}..."):
                    bdf = load_file(active_bounce_file)
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
        if "Location" in std.columns:
            try:
                with st.spinner("Loading country and city library..."):
                    country_ref = load_country_reference(str(COUNTRIES_JSON_PATH))
                std["__country"] = classify_countries(std["Location"], country_ref)
            except Exception as e:
                st.warning(f"Country split fallback: could not parse countries.json ({e}). All rows marked as Unknown.")
                std["__country"] = "Unknown"
        else:
            std["__country"] = "Unknown"
 
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
            "special_char_rows": len(st.session_state.get("special_chars_removed_df", [])),
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
        m4.metric("Rows separated due to special characters", metrics.get("special_char_rows", 0))
 
        with st.expander("Processing report (what happened at each step)", expanded=True):
            for line in st.session_state["report"]:
                st.write("- " + line)
 
        st.subheader("Final cleaned data (preview)")
        st.caption("This is what your downloaded file will contain, in final campaign order.")
        st.dataframe(st.session_state["cleaned_df"].head(50), width="stretch")
        st.caption(f"{st.session_state['cleaned_df'].shape[0]} rows x {st.session_state['cleaned_df'].shape[1]} columns")
 
        st.subheader("Step 6 — Download")
 
        tab_main, tab_country, tab_audit = st.tabs(["Final campaign file", "Split by country", "Audit files (removed rows)"])
 
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
                "Split based on [data/countries.json](data/countries.json) country and city matching against Location. "
                "'Other' means a location was present but no country/city keyword matched; 'Unknown' means Location was blank."
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
 
            special_chars_df = st.session_state.get("special_chars_removed_df", pd.DataFrame())
            st.write(f"**Rows separated because special characters were found (uncleaned raw values):** {len(special_chars_df)} rows")
            if len(special_chars_df) > 0:
                st.dataframe(special_chars_df.head(20), width="stretch")
                st.download_button(
                    "Download separated special-characters file (full list)",
                    data=special_chars_df.to_csv(index=False).encode("utf-8"),
                    file_name="special_characters_separated.csv",
                    mime="text/csv",
                    key="dl_specialchars_audit",
                )
                st.caption("This file is not cleaned. It contains original rows exactly as detected with special characters.")

            st.divider()

            email_special_df = st.session_state.get("special_chars_email_df", pd.DataFrame())
            st.write(f"**Rows where Email specifically contains special characters (uncleaned):** {len(email_special_df)} rows")
            if len(email_special_df) > 0:
                st.dataframe(email_special_df.head(20), width="stretch")
                st.download_button(
                    "Download email-special-characters file (full list)",
                    data=email_special_df.to_csv(index=False).encode("utf-8"),
                    file_name="email_special_characters_separated.csv",
                    mime="text/csv",
                    key="dl_email_specialchars_audit",
                )
else:
    st.info("Select files above, click 'Use selected files', then continue. Master File and Bounce File are optional but recommended for cleaner results.")
 
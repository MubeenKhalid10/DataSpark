"""
Lead Data Cleaning & Sorting Tool  (Raw Lead File -> Final Campaign File)
--------------------------------------------------------------------------
Run locally:      streamlit run app.py
Deploy for free:  push this file (+ requirements.txt) to a GitHub repo,
                   then deploy at https://share.streamlit.io

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

with st.expander("ℹ️ How this works — the 9-step process", expanded=False):
    st.markdown(
        """
1. **Standardize** — detect your columns, split *Full Name* into First/Last if needed, keep only the relevant fields
2. **Remove blank emails** — any row with no email address is dropped
3. **Remove Indian contacts** — matched by location/state/city or a `.in` email domain
4. **Clean text** — strip special characters, hidden Unicode junk, and extra spaces
5. **Remove in-file duplicates** — same email appearing more than once in your raw file
6. **Remove existing contacts** — anyone already in your uploaded Master File(s)
7. **Remove bounced emails** — anyone in your Master Bounce file
8. **Arrange columns** — final file is ordered First Name, Last Name, Company, Email, Job Title, Industry, Location
9. **Final QC** — double-checks for blank rows, blank emails, and duplicate emails before it's ready to download
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


SPECIAL_CHARS_PATTERN = re.compile(
    r"[ÃÂÄÅƒÙ¢€™žœ¦§µ¶®·¸»¼½¾¿ŸþÿΓÇ~*!#$%^]"
)


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


def is_indian_contact(row, location_col, email_col):
    location_val = str(row.get(location_col, "")).lower() if location_col else ""
    email_val = str(row.get(email_col, "")).lower() if email_col else ""
    # Word-boundary match so "Indialantic" doesn't false-match "india",
    # while still catching multi-word names like "uttar pradesh" or "new delhi".
    for hint in INDIAN_STATE_CITY_HINTS:
        if re.search(r"\b" + re.escape(hint) + r"\b", location_val):
            return True
    if email_val.endswith(".in"):
        return True
    return False


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
        indian_mask = std.apply(lambda r: is_indian_contact(r, "Location", "Email"), axis=1)
        std = std[~indian_mask].reset_index(drop=True)
        report.append(f"Step 3 - Removed Indian contacts: {before - len(std)} rows removed")

        # ---- STEP 4: Remove special characters & Unicode, trim ----
        update_progress(4, "Cleaning special characters and unicode...")
        for col in FINAL_COLUMNS:
            std[col] = std[col].apply(clean_text)
        report.append("Step 4 - Cleaned special characters, hidden unicode, and extra spaces")

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
        std = std.drop_duplicates(subset="__email_lower", keep="first")
        std = std.reset_index(drop=True)
        report.append(f"Step 5 - Removed in-file duplicates: {before - len(std)} rows removed")

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

        progress_bar.progress(1.0, text="Done! Cleaning complete.")

        st.session_state["cleaned_df"] = std
        st.session_state["report"] = report

    if "cleaned_df" in st.session_state:
        st.subheader("Step 5 — Review the results")
        with st.expander("Processing report (what happened at each step)", expanded=True):
            for line in st.session_state["report"]:
                st.write("- " + line)

        st.subheader("Final cleaned data (preview)")
        st.caption("This is what your downloaded file will contain, in final campaign order.")
        st.dataframe(st.session_state["cleaned_df"].head(50), width="stretch")
        st.caption(f"{st.session_state['cleaned_df'].shape[0]} rows x {st.session_state['cleaned_df'].shape[1]} columns")

        st.subheader("Step 6 — Download")
        out_format = st.radio(
            "Download format",
            ["xlsx", "csv"],
            horizontal=True,
            help="xlsx opens directly in Excel; csv is the safer choice for uploading into most campaign/email tools.",
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
else:
    st.info("Upload a raw lead file above to get started. Master File and Bounce File are optional but recommended for cleaner results.")
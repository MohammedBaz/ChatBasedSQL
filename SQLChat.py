import streamlit as st
import sqlite3
import logging
import re
import google.generativeai as genai
import os

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Environment Variables & Configuration (CRITICAL!) ---
# Attempt to get the API key from Streamlit secrets first, then environment variables
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except (AttributeError, KeyError): # Handles local dev where st.secrets might not be available/configured
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

DATABASE_PATH = "student_db.sqlite" # Ensure this database is in the same directory or provide a correct path

if not GEMINI_API_KEY:
    st.error("Missing required environment variable GEMINI_API_KEY. Please set it in your environment or Streamlit secrets.")
    st.stop() # Stop the app if API key is not found

# --- Google Generative AI Setup ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    st.error(f"Error configuring Google Generative AI: {e}")
    st.stop()

# --- SQL Query Execution with Validation ---
def read_sql_query(sql, db_path):
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(sql)
        results = cur.fetchall()
        conn.close()
        return results
    except sqlite3.Error as e:
        logging.error(f"SQL execution error: {e} for query: {sql}")
        return f"SQL_ERROR: {e}" # Return error message for display

# --- Generate Natural Language Response ---
def generate_natural_response(results, sql_query):
    if isinstance(results, str) and results.startswith("SQL_ERROR:"):
        return f"حدث خطأ أثناء تنفيذ استعلام SQL: {results.replace('SQL_ERROR: ', '')}"
    if results is None: # Should ideally be caught by the SQL_ERROR above
        return "حدث خطأ في الاستعلام (النتائج فارغة بشكل غير متوقع)."
    if not results:
        return "لا توجد نتائج للاستعلام."

    if "COUNT(*)" in sql_query.upper():
        try:
            count = results[0][0]
            return f"يوجد {count} نتيجة مطابقة للاستعلام."
        except (IndexError, TypeError):
            logging.warning("Could not parse COUNT(*) result.")
            return "لا يمكن تحديد عدد النتائج."

    try:
        # Assuming column names are not directly available from fetchall for simple display
        # For more complex scenarios, you might want to fetch column names: cur.description
        response_lines = []
        for row in results:
            response_lines.append(", ".join(str(item) for item in row))
        return "\n".join(response_lines)
    except Exception as e:
        logging.error(f"Error formatting response: {e}")
        return "حدث خطأ أثناء تنسيق الرد."

# --- Gemini AI for SQL Generation ---
def get_gemini_response(question, prompt_text):
    try:
        model = genai.GenerativeModel('gemini-1.5-flash') # Using a common and effective model
        full_prompt = f"{prompt_text}\n\nUser Question (in Arabic):\n{question}\n\nSQL Query (respond *only* with the SQL query inside ```sql ... ``` block):\n"
        
        # Log a snippet of the prompt being sent to Gemini (excluding API key)
        logging.info(f"Sending prompt to Gemini: {full_prompt[:500]}...")

        response = model.generate_content(full_prompt)

        if response and hasattr(response, 'text') and response.text:
            logging.info(f"Raw response from Gemini: {response.text}")
            # Improved regex to be more robust
            match = re.search(r"```sql\s*(.*?)\s*```", response.text, re.DOTALL | re.IGNORECASE)
            if match:
                sql_query = match.group(1).strip()
                # Basic validation: ensure it's not empty and contains SELECT
                if sql_query and "SELECT" in sql_query.upper():
                    return sql_query
                else:
                    logging.warning(f"Extracted SQL is invalid or empty: {sql_query}")
                    return "No valid SQL (empty or missing SELECT) found in response."
            else:
                logging.warning(f"No ```sql ``` block found in response: {response.text}")
                # Fallback: check if the response text itself is a plausible SQL query
                cleaned_response_text = response.text.strip()
                if cleaned_response_text.upper().startswith("SELECT"):
                    logging.info("No ```sql``` block, but raw response looks like SQL. Using it.")
                    return cleaned_response_text
                return "No SQL code block found in Gemini's response, and raw text doesn't appear to be SQL."
        else:
            logging.warning("No response or empty text from Gemini.")
            return "No response from Gemini."
    except Exception as e:
        logging.error(f"Gemini API error: {e}")
        if "API_KEY_INVALID" in str(e).upper():
             return "Error: Gemini API key is invalid."
        return f"Error in AI response: {e}"

prompt = '''
Database Schema:
Students Table: StudentID, FirstName, LastName, Gender, DateOfBirth
Education Table: EducationID, StudentID, Level, Grade
Parents Table: ParentID, StudentID, ContactNumber

Instructions: Generate SQL queries in response to Arabic questions based on the database schema.
Ensure to use JOIN clauses when necessary to combine information from different tables.

Examples:
### Example 1:
"قائمة بأسماء جميع الطالبات"
SQL: SELECT s.FirstName, s.LastName FROM Students s WHERE s.Gender = 'Female';

### Example 2:
"كم عدد الطلاب في المدرسة الابتدائية؟"
SQL: SELECT COUNT(*) FROM Education WHERE Level = 'ابتدائي';

### Example 3:
"اعرض أسماء ودرجات الطلاب الذين حصلوا على الدرجة 'ممتاز'"
SQL: SELECT s.FirstName, s.LastName, e.Grade FROM Students s
        JOIN Education e ON s.StudentID = e.StudentID
        WHERE e.Grade = 'ممتاز';

### Example 4:
"أظهر أسماء جميع الطلاب وأرقام هواتف آبائهم"
SQL: SELECT s.FirstName, s.LastName, p.ContactNumber FROM Students s
        JOIN Parents p ON s.StudentID = p.StudentID;

### Example 5:
"ما هي أسماء الطلاب في المرحلة الابتدائية؟"
SQL: SELECT s.FirstName, s.LastName
        FROM Students s JOIN Education e ON s.StudentID = e.StudentID
        WHERE e.Level = 'ابتدائي';

### Format your response as: ```sql <QUERY> ```
'''



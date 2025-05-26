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
        
        # Log the full prompt being sent to Gemini (excluding API key)
        logging.info(f"Sending prompt to Gemini: {full_prompt}")

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
                # If no ```sql ``` block, check if the response itself is a valid SQL.
                # This is a fallback and might need more robust validation.
                # For now, we assume the LLM will stick to the format.
                # If the LLM just returns plain SQL, we might try to use it,
                # but it's less reliable than the markdown block.
                # For simplicity, let's stick to expecting the ```sql ``` block.
                logging.warning(f"No ```sql ``` block found in response: {response.text}")
                return "No SQL code block found in Gemini's response."
        else:
            logging.warning("No response or empty text from Gemini.")
            return "No response from Gemini."
    except Exception as e:
        logging.error(f"Gemini API error: {e}")
        # Check for specific API errors if possible, e.g., authentication, quota
        if "API_KEY_INVALID" in str(e): # Example, check actual error messages
             return "Error: Gemini API key is invalid."
        return f"Error in AI response: {e}"

# --- SQL Prompt (Ensure this matches your database schema and desired behavior) ---
prompt = '''
You are an AI assistant that translates Arabic natural language questions into SQL queries for a student database.
The database has the following schema:

Students Table:
- StudentID (INTEGER, PRIMARY KEY)
- FirstName (TEXT)
- LastName (TEXT)
- Gender (TEXT) -- Example values: 'ذكر', 'Female' (Note: Use 'Female' for 'أنثى' or 'طالبة' in WHERE clauses if your data uses 'Female')
- DateOfBirth (TEXT) -- Format: YYYY-MM-DD

Education Table:
- EducationID (INTEGER, PRIMARY KEY)
- StudentID (INTEGER, FOREIGN KEY referencing Students.StudentID)
- Level (TEXT) -- Example values: 'ابتدائي', 'متوسط', 'ثانوي'
- Grade (TEXT) -- Example values: 'ممتاز', 'جيد جداً', 'جيد', 'مقبول'

Parents Table:
- ParentID (INTEGER, PRIMARY KEY)
- StudentID (INTEGER, FOREIGN KEY referencing Students.StudentID)
- ContactNumber (TEXT)

Instructions:
1.  Generate SQL queries in response to Arabic questions based on the database schema.
2.  Ensure to use JOIN clauses when necessary to combine information from different tables.
3.  **IMPORTANT**: Respond *ONLY* with the SQL query itself, enclosed in a ```sql ... ``` markdown block. Do not add any other text, explanation, or salutation.
4.  Pay attention to Arabic keywords for gender, levels, and grades and map them to the database values. For example, if the user asks for "طالبات" (female students), use `WHERE s.Gender = 'Female'`. If the user asks for "المرحلة الابتدائية", use `WHERE e.Level = 'ابتدائي'`.

Examples:
### Example 1:
User Question: قائمة بأسماء جميع الطالبات
SQL Query:
```
SELECT s.FirstName, s.LastName FROM Students s WHERE s.Gender = 'Female'

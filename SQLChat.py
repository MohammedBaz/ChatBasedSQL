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
GEMINI_API_KEY = None
try:
    # This is the recommended way for deployed Streamlit apps
    GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        logging.info("GEMINI_API_KEY loaded from st.secrets.")
except AttributeError:
    # st.secrets might not be available in all local dev environments
    logging.warning("st.secrets not available. Trying os.environ.")
    pass # Fall through to os.environ

if not GEMINI_API_KEY:
    # Fallback for local development or other environments
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        logging.info("GEMINI_API_KEY loaded from os.environ.")

DATABASE_PATH = "student_db.sqlite" # Ensure this database is in the same directory or provide a correct path

if not GEMINI_API_KEY:
    st.error("CRITICAL: Missing environment variable GEMINI_API_KEY. Please set it in your Streamlit secrets (recommended for deployment) or as an environment variable.")
    st.stop() # Stop the app if API key is not found

# --- Google Generative AI Setup ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    logging.info("Google Generative AI configured successfully.")
except Exception as e:
    st.error(f"CRITICAL: Error configuring Google Generative AI: {e}")
    st.stop()

# --- SQL Query Execution with Validation ---
def read_sql_query(sql, db_path):
    """Connects to the SQLite database, executes a SQL query, and returns results."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(sql)
        results = cur.fetchall()
        # If it's a SELECT query, get column names for better display later (optional)
        # col_names = [description[0] for description in cur.description] if cur.description else []
        conn.close()
        # return results, col_names
        return results
    except sqlite3.Error as e:
        logging.error(f"SQL execution error: {e} for query: {sql}")
        return f"SQL_ERROR: {e}" # Return error message for display

# --- Generate Natural Language Response ---
def generate_natural_response(results, sql_query):
    """Formats the SQL query results into a user-friendly natural language response."""
    if isinstance(results, str) and results.startswith("SQL_ERROR:"):
        return f"حدث خطأ أثناء تنفيذ استعلام SQL: {results.replace('SQL_ERROR: ', '')}"
    if results is None:
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
        response_lines = []
        for row in results:
            response_lines.append(" | ".join(str(item) for item in row)) # Using pipe for better separation
        return "\n".join(response_lines)
    except Exception as e:
        logging.error(f"Error formatting response: {e}")
        return "حدث خطأ أثناء تنسيق الرد."

# --- Gemini AI for SQL Generation ---
def get_gemini_response(question, prompt_text):
    """Sends the question and prompt to Gemini to get an SQL query."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        # The instruction for the format is now part of the main prompt.
        full_prompt = f"{prompt_text}\n\nUser Question (in Arabic):\n{question}\n"
        
        logging.info(f"Sending prompt to Gemini (first 500 chars): {full_prompt[:500]}...")

        response = model.generate_content(full_prompt)

        if response and hasattr(response, 'text') and response.text:
            logging.info(f"Raw response from Gemini: {response.text}")
            # Regex to find ```sql ... ``` block
            match = re.search(r"```sql\s*(.*?)\s*```", response.text, re.DOTALL | re.IGNORECASE)
            if match:
                sql_query = match.group(1).strip()
                if sql_query and "SELECT" in sql_query.upper(): # Basic validation
                    return sql_query
                else:
                    logging.warning(f"Extracted SQL is invalid or empty: '{sql_query}'")
                    return "No valid SQL (empty or missing SELECT) found in response."
            else:
                logging.warning(f"No ```sql ``` block found in response: {response.text}")
                # Fallback: Check if the raw response itself looks like SQL (less reliable)
                cleaned_response_text = response.text.strip()
                if cleaned_response_text.upper().startswith("SELECT"):
                    logging.info("No ```sql``` block, but raw response looks like SQL. Using it as a fallback.")
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

# --- SQL Prompt ---
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
3.  Pay attention to Arabic keywords for gender, levels, and grades and map them to the database values. For example, if the user asks for "طالبات" (female students), use `WHERE s.Gender = 'Female'`. If the user asks for "المرحلة الابتدائية", use `WHERE e.Level = 'ابتدائي'`.
4.  **IMPORTANT**: Format your response *ONLY* with the SQL query itself, enclosed in a ```sql ... ``` markdown block. Do not add any other text, explanation, or salutation before or after the SQL block.

Examples:
### Example 1:
User Question: قائمة بأسماء جميع الطالبات
SQL Query:
```sql
SELECT s.FirstName, s.LastName FROM Students s WHERE s.Gender = 'Female';
```

### Example 2:
User Question: كم عدد الطلاب في المدرسة الابتدائية؟
SQL Query:
```sql
SELECT COUNT(*) FROM Students s JOIN Education e ON s.StudentID = e.StudentID WHERE e.Level = 'ابتدائي';
```

### Example 3:
User Question: اعرض أسماء ودرجات الطلاب الذين حصلوا على الدرجة 'ممتاز'
SQL Query:
```sql
SELECT s.FirstName, s.LastName, e.Grade FROM Students s
JOIN Education e ON s.StudentID = e.StudentID
WHERE e.Grade = 'ممتاز';
```

### Example 4:
User Question: أظهر أسماء جميع الطلاب وأرقام هواتف آبائهم
SQL Query:
```sql
SELECT s.FirstName, s.LastName, p.ContactNumber FROM Students s
JOIN Parents p ON s.StudentID = p.StudentID;
```

### Example 5:
User Question: ما هي أسماء الطلاب في المرحلة الابتدائية؟
SQL Query:
```sql
SELECT s.FirstName, s.LastName
FROM Students s JOIN Education e ON s.StudentID = e.StudentID
WHERE e.Level = 'ابتدائي';
```
'''

# --- Streamlit App UI ---
st.set_page_config(page_title="NLP to SQL Chatbot �", layout="wide")

st.title("🤖 Chatbot الاستعلام عن بيانات الطلاب باللغة العربية")
st.caption("اسأل عن بيانات الطلاب وسأقوم بترجمة سؤالك إلى SQL وتنفيذ الاستعلام!")

# --- Database Check and Creation (Optional: For demonstration) ---
def initialize_db(db_path):
    """Creates the database and tables with sample data if the DB file doesn't exist."""
    if not os.path.exists(db_path):
        st.warning(f"قاعدة البيانات {db_path} غير موجودة. سأقوم بإنشاء واحدة بمخطط تجريبي.")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # Create Students Table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS Students (
                StudentID INTEGER PRIMARY KEY AUTOINCREMENT,
                FirstName TEXT NOT NULL,
                LastName TEXT NOT NULL,
                Gender TEXT,
                DateOfBirth TEXT
            );
            ''')
            # Create Education Table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS Education (
                EducationID INTEGER PRIMARY KEY AUTOINCREMENT,
                StudentID INTEGER,
                Level TEXT,
                Grade TEXT,
                FOREIGN KEY (StudentID) REFERENCES Students (StudentID)
            );
            ''')
            # Create Parents Table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS Parents (
                ParentID INTEGER PRIMARY KEY AUTOINCREMENT,
                StudentID INTEGER,
                ContactNumber TEXT,
                FOREIGN KEY (StudentID) REFERENCES Students (StudentID)
            );
            ''')
            # Insert some sample data
            sample_students = [
                ('أحمد', 'الغامدي', 'ذكر', '2005-03-15'),
                ('فاطمة', 'الشهري', 'Female', '2006-07-22'),
                ('محمد', 'القحطاني', 'ذكر', '2005-11-10'),
                ('نورة', 'العتيبي', 'Female', '2007-01-30')
            ]
            cursor.executemany("INSERT INTO Students (FirstName, LastName, Gender, DateOfBirth) VALUES (?, ?, ?, ?)", sample_students)
            
            sample_education = [
                (1, 'ثانوي', 'ممتاز'),
                (2, 'متوسط', 'جيد جداً'),
                (3, 'ثانوي', 'جيد'),
                (4, 'ابتدائي', 'ممتاز'),
                (1, 'متوسط', 'جيد') # Ahmed's previous record
            ]
            cursor.executemany("INSERT INTO Education (StudentID, Level, Grade) VALUES (?, ?, ?)", sample_education)

            sample_parents = [
                (1, '0501234567'),
                (2, '0559876543'),
                (4, '0512233445')
            ]
            cursor.executemany("INSERT INTO Parents (StudentID, ContactNumber) VALUES (?, ?)", sample_parents)
            
            conn.commit()
            conn.close()
            st.success(f"تم إنشاء قاعدة البيانات {db_path} مع بيانات تجريبية.")
        except Exception as e:
            st.error(f"فشل في إنشاء قاعدة البيانات التجريبية: {e}")
            # Do not stop the app here, let it run but with a DB error.
            # User might provide their own DB.
            logging.error(f"Database initialization failed: {e}")
    else:
        logging.info(f"Database {db_path} found.")

# Initialize the database if it doesn't exist (and if you want sample data)
# IMPORTANT: If your database student_db.sqlite ALREADY EXISTS and has your actual data, 
# you should COMMENT OUT or REMOVE the next line to prevent it from being overwritten or modified.
initialize_db(DATABASE_PATH)


# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "مرحباً! كيف يمكنني مساعدتك اليوم في الاستعلام عن بيانات الطلاب؟ (مثال: كم عدد الطلاب الذكور؟)"}]

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        # If the message was an assistant's response that included an SQL query, display it
        if message["role"] == "assistant" and message.get("sql_query"):
            with st.expander("عرض استعلام SQL الذي تم إنشاؤه"):
                st.code(message["sql_query"], language="sql")

# Accept user input
if user_question := st.chat_input("اسأل سؤالك هنا..."):
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": user_question})
    # Display user message in chat message container
    with st.chat_message("user"):
        st.markdown(user_question)

    # Display assistant response in chat message container
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        message_placeholder.markdown(" أفكر... 🤔 الرجاء الانتظار قليلاً.")

        # 1. Get SQL query from Gemini
        generated_sql = get_gemini_response(user_question, prompt)
        
        assistant_response_content = ""
        sql_to_display_in_chat = None # To store the SQL if successfully generated

        if generated_sql and not generated_sql.lower().startswith("error:") and \
           not "no valid sql" in generated_sql.lower() and \
           not "no sql code block" in generated_sql.lower() and \
           not "no response from gemini" in generated_sql.lower():
            
            sql_to_display_in_chat = generated_sql # Store for history
            message_placeholder.markdown(f"تم إنشاء استعلام SQL. الآن سأقوم بتنفيذه...")
            
            # 2. Execute SQL query
            results = read_sql_query(sql_to_display_in_chat, DATABASE_PATH)
            
            # 3. Generate natural language response from results
            natural_response = generate_natural_response(results, sql_to_display_in_chat)
            assistant_response_content = natural_response
            
            message_placeholder.markdown(assistant_response_content)
            # Show the SQL query in an expander below the natural language response
            if sql_to_display_in_chat:
                with st.expander("عرض استعلام SQL الذي تم إنشاؤه"):
                    st.code(sql_to_display_in_chat, language="sql")
        else: # Handle errors from Gemini or SQL generation
            assistant_response_content = f"عذراً، لم أتمكن من معالجة طلبك بالشكل الصحيح. سبب المشكلة: {generated_sql}"
            message_placeholder.markdown(assistant_response_content)

    # Add assistant response (and SQL if generated) to chat history
    st.session_state.messages.append({
        "role": "assistant",
        "content": assistant_response_content,
        "sql_query": sql_to_display_in_chat 
    })

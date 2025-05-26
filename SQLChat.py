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
        # The instruction for the format is now part of the main prompt.
        # The get_gemini_response function will still try to extract SQL from ```sql ... ``` block
        full_prompt = f"{prompt_text}\n\nUser Question (in Arabic):\n{question}\n"
        
        # Log a snippet of the prompt being sent to Gemini (excluding API key)
        logging.info(f"Sending prompt to Gemini: {full_prompt[:500]}...")

        response = model.generate_content(full_prompt)

        if response and hasattr(response, 'text') and response.text:
            logging.info(f"Raw response from Gemini: {response.text}")
            # Regex to find ```sql ... ``` block
            match = re.search(r"```sql\s*(.*?)\s*```", response.text, re.DOTALL | re.IGNORECASE)
            if match:
                sql_query = match.group(1).strip()
                if sql_query and "SELECT" in sql_query.upper():
                    return sql_query
                else:
                    logging.warning(f"Extracted SQL is invalid or empty: {sql_query}")
                    return "No valid SQL (empty or missing SELECT) found in response."
            else:
                logging.warning(f"No ```sql ``` block found in response: {response.text}")
                # Fallback: If the prompt tells the LLM to NOT use the block,
                # we might assume the whole text is the query.
                # However, your current prompt DOES ask for the block: "### Format your response as: ```sql <QUERY> ```"
                # So, if it's not found, it's an issue.
                # For robustness, we can check if the raw response itself looks like SQL as a last resort.
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
# Corrected prompt (removed the extra " at the end)
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

# --- Streamlit App UI (This is the part you asked about) ---
st.set_page_config(page_title="NLP to SQL Chatbot 🤖", layout="wide")

st.title("🤖 Chatbot الاستعلام عن بيانات الطلاب باللغة العربية")
st.caption("اسأل عن بيانات الطلاب وسأقوم بترجمة سؤالك إلى SQL وتنفيذ الاستعلام!")

# --- Database Check and Creation (Optional: For demonstration) ---
def initialize_db(db_path):
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
            cursor.execute("INSERT INTO Students (FirstName, LastName, Gender, DateOfBirth) VALUES ('أحمد', 'الغامدي', 'ذكر', '2005-03-15')")
            cursor.execute("INSERT INTO Students (FirstName, LastName, Gender, DateOfBirth) VALUES ('فاطمة', 'الشهري', 'Female', '2006-07-22')")
            cursor.execute("INSERT INTO Students (FirstName, LastName, Gender, DateOfBirth) VALUES ('محمد', 'القحطاني', 'ذكر', '2005-11-10')")
            
            cursor.execute("INSERT INTO Education (StudentID, Level, Grade) VALUES (1, 'ثانوي', 'ممتاز')")
            cursor.execute("INSERT INTO Education (StudentID, Level, Grade) VALUES (2, 'متوسط', 'جيد جداً')")
            cursor.execute("INSERT INTO Education (StudentID, Level, Grade) VALUES (3, 'ثانوي', 'جيد')")

            cursor.execute("INSERT INTO Parents (StudentID, ContactNumber) VALUES (1, '0501234567')")
            cursor.execute("INSERT INTO Parents (StudentID, ContactNumber) VALUES (2, '0559876543')")
            
            conn.commit()
            conn.close()
            st.success(f"تم إنشاء قاعدة البيانات {db_path} مع بيانات تجريبية.")
        except Exception as e:
            st.error(f"فشل في إنشاء قاعدة البيانات التجريبية: {e}")
            st.stop()
    else:
        logging.info(f"Database {db_path} found.")

# Initialize the database if it doesn't exist (and if you want sample data)
# If your database student_db.sqlite ALREADY EXISTS and has data, you can comment out or remove the next line.
initialize_db(DATABASE_PATH)


# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "مرحباً! كيف يمكنني مساعدتك اليوم في الاستعلام عن بيانات الطلاب؟ (مثال: كم عدد الطلاب الذكور؟)"}]

# Display chat messages from history on app rerun
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "sql_query" in message and message["sql_query"]:
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
        sql_to_display = None

        if generated_sql and not generated_sql.lower().startswith("error:") and \
           not "no valid sql" in generated_sql.lower() and \
           not "no sql code block" in generated_sql.lower() and \
           not "no response from gemini" in generated_sql.lower():
            sql_to_display = generated_sql
            message_placeholder.markdown(f"تم إنشاء استعلام SQL التالي:\n```sql\n{sql_to_display}\n```\nالآن سأقوم بتنفيذه...")
            
            # 2. Execute SQL query
            results = read_sql_query(sql_to_display, DATABASE_PATH)
            
            # 3. Generate natural language response from results
            natural_response = generate_natural_response(results, sql_to_display)
            assistant_response_content = natural_response
            
            message_placeholder.markdown(assistant_response_content)
            if sql_to_display: # Re-display SQL with the result
                 st.code(sql_to_display, language="sql")

        else: # Handle errors from Gemini or SQL generation
            assistant_response_content = f"عذراً، لم أتمكن من معالجة طلبك بالشكل الصحيح. {generated_sql}"
            message_placeholder.markdown(assistant_response_content)

    # Add assistant response (and SQL) to chat history
    st.session_state.messages.append({
        "role": "assistant",
        "content": assistant_response_content,
        "sql_query": sql_to_display # Store for potential redisplay
    })

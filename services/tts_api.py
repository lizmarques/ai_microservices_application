from flask import Flask, request, jsonify, Response
import psycopg2
from gtts import gTTS
import os
import datetime
from dotenv import load_dotenv
from celery import Celery
from celery.result import AsyncResult
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from shared.celery_config import app  # Import the Celery app
from flask import send_file  # Import send_file to return the file
from kombu import Queue
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time

# Load .env file
load_dotenv(dotenv_path=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vars.env")))


# Flask app initialization
app = Flask(__name__)

# Metrics definition
TTS_REQUEST_COUNT = Counter("tts_request_count", "Total de requisições ao TTS")
TTS_LATENCY = Histogram("tts_latency_seconds", "Tempo de geração do áudio")
TTS_TEXT_SIZE = Histogram("tts_text_size_bytes", "Tamanho do texto recebido")

# # Configure Celery
app.config['CELERY_BROKER_URL'] = 'redis://redis:6379/0'  # Redis URL for Celery broker
app.config['CELERY_RESULT_BACKEND'] = 'redis://redis:6379/0'  # Redis for result backend


# Define the default queue and custom queues
task_queues = (
    Queue('stt', routing_key='stt'),  # All tasks with stt.# go to stt
    Queue('llm', routing_key='llm'),  # All tasks with llm.# go to llm
    Queue('tts', routing_key='tts'),  # All tasks with llm.# go to llm
)

# Define task routes
task_routes = {
    'transcribe_audio': {'queue': 'stt', 'routing_key': 'stt'},
    'process_llm_query': {'queue': 'llm', 'routing_key': 'llm'},
    'generate_tts_audio': {'queue': 'tts', 'routing_key': 'tts'},
}

celery = Celery(
    "services.tts_api",
    broker=app.config['CELERY_BROKER_URL'],
    backend=app.config['CELERY_RESULT_BACKEND']  # Use backend instead of result_backend
)
celery.conf.update(
    task_queues=task_queues,
    task_routes=task_routes,
    broker_connection_retry_on_startup=True,  # Ensures retries on broker connection
)


db_config = {
    "dbname": os.getenv("POSTGRES_DB", "tts_service"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "mypassword"),
    "host": os.getenv("POSTGRES_HOST", "postgres_tts"),  # MUST MATCH DOCKER COMPOSE
    "port": int(os.getenv("POSTGRES_PORT", 5432))  # Default PostgreSQL port
}



# Directory to save audio files
AUDIO_DIR = "/app"  # Change this path if needed

# Ensure audio directory exists
os.makedirs(AUDIO_DIR, exist_ok=True)

# Celery Task to generate TTS audio and save it to PostgreSQL
@celery.task(bind=True, name="generate_tts_audio")
def generate_tts_audio(self, text):
    try:
        print(f"Generating TTS audio for text: {text}")

        # Request metrics
        TTS_REQUEST_COUNT.inc()
        start_time = time.time()

        # Size metrics
        TTS_TEXT_SIZE.observe(len(text))
        
        # Generate TTS audio
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        audio_filename = f"audio_{timestamp}.mp3"
        tts = gTTS(text=text, lang='en')

        # Latency metrics
        duration = time.time() - start_time
        TTS_LATENCY.observe(duration)

        tts.save(audio_filename)
        print("Salvou o audio")

        
        # Save text and audio to PostgreSQL
        with open(audio_filename, "rb") as audio_file:
            audio_binary = audio_file.read()


        try:
            conn = psycopg2.connect(
                dbname="tts_service",
                user="postgres",
                password="mypassword",
                host="postgres_tts",
                port=5432
            )
            cursor = conn.cursor()

            # Print before inserting
            print("🔍 Checking if table exists...")
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'tts_results';")
            table_exists = cursor.fetchone()[0]

            if table_exists:
                print("✅ Table 'tts_results' exists.")
            else:
                print("❌ Table 'tts_results' does NOT exist!")

                cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tts_results (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                audio_filename TEXT NOT NULL,
                audio_data BYTEA NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
                print("✅ Table 'tts_results'created!")


            cursor.execute("""
                INSERT INTO tts_results (text, audio_filename, audio_data)
                VALUES (%s, %s, %s)
                RETURNING id;
            """, (text, audio_filename, psycopg2.Binary(audio_binary)))

            inserted_id = cursor.fetchone()[0]  # Get the inserted row ID
            conn.commit()

            print(f"✅ Data inserted successfully! Inserted ID: {inserted_id}")

            # Verify by selecting last 5 entries
            cursor.execute("SELECT id, text, audio_filename FROM tts_results ORDER BY created_at DESC LIMIT 5;")
            rows = cursor.fetchall()

            print("📌 Last 5 records in tts_results:")
            for row in rows:
                print(row)

        except Exception as e:
            print(f"❌ ERROR: {e}")

        finally:
            cursor.close()
            conn.close()
            print("🔍 Connection closed.")

        return audio_filename

    except Exception as e:
        print(f"❌ Error occurred: {e}")
        raise self.retry(exc=e)

@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@app.route('/tts', methods=['POST'])
def tts_api():
    try:
        print("==============TTS==============")
        # Get text from request
        data = request.json
        text = data.get("text")

        # Call the Celery task to generate TTS audio asynchronously
        task = generate_tts_audio.apply_async(args=[text], routing_key='tts')

        # Return the task ID for the client to check the status
        return jsonify({"message": "Audio generation started", "task_id": task.id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/task_status_tts/<task_id>', methods=['GET'])
def get_task_status(task_id):
    try:
        task = celery.AsyncResult(task_id)

        if task.state == 'PENDING':
            response = {"status": "PENDING", "result": None}
        elif task.state == 'SUCCESS':
            response = {"status": "SUCCESS", "result": task.result}
        elif task.state == 'FAILURE':
            response = {"status": "FAILURE", "reasult": str(task.result)}
        else:
            response = {"status": task.state, "result": None}

        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    

@app.route('/get_audio/<filename>', methods=['GET'])
def get_audio(filename):
    file_path = os.path.join("/app", filename)
    #print(file_path)

    if os.path.exists(file_path):
        return send_file(file_path, mimetype="audio/mp3")  # Serve the MP3 file
    else:
        return jsonify({"error": "File not found"}), 404

# @app.route('/get_audio/<filename>', methods=['GET'])
# def get_audio(filename):
#     file_path = os.path.join("/app", filename)  # ✅ File is in /app

#     # 🔍 Debugging output
#     print(f"📁 Checking file: {file_path}")

#     if os.path.exists(file_path):
#         print("✅ File exists! Serving...")
#         return send_file(file_path, mimetype="audio/mp3")  # Serve the MP3 file
#     else:
#         print("❌ File NOT FOUND!")
#         return jsonify({"error": "File not found"}), 404




if __name__ == "__main__":
#    app.run(host="localhost", port=8504, debug=True)
    app.run(host="0.0.0.0", port=8504, debug=True)
